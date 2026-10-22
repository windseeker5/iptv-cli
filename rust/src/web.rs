//! A small web server for the restream, so no NGINX container is needed.
//!
//! It answers like the NGINX config it replaces (`nginx/nginx.conf`):
//! - `/hls/*`: the HLS playlist and segments FFmpeg writes, with the same
//!   no-cache and CORS headers, and one line per request in the same
//!   `hls_access.log` format, which OMT and tv-counter read to count viewers;
//! - `/live` and other pages from `nginx/html` when that folder exists;
//!   otherwise a built-in player page (on the road, for a phone on the same Wi-Fi);
//! - `/now.json`: what's being restreamed (name and stream key only).
//!
//! Runs inside the restream unit while a restream is on, or for good as
//! `kdtv web` (the `kdtv-web` service).

use crate::config::Config;
use anyhow::{Context, Result};
use serde_json::json;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Component, Path, PathBuf};

const PLAYER: &str = include_str!("player.html");

pub fn serve(cfg: &Config) -> Result<()> {
    let listener = bind(cfg)?;
    crate::log::event(cfg, "web.start", json!({ "port": cfg.web_port }));
    serve_on(cfg, listener);
    Ok(())
}

pub fn bind(cfg: &Config) -> Result<TcpListener> {
    TcpListener::bind((cfg.web_bind.as_str(), cfg.web_port)).with_context(|| format!("port {} is taken", cfg.web_port))
}

/// Whether something already answers on the web port (NGINX, or `kdtv web`).
pub fn port_in_use(cfg: &Config) -> bool {
    TcpStream::connect(("127.0.0.1", cfg.web_port)).is_ok()
}

pub fn serve_on(cfg: &Config, listener: TcpListener) {
    for stream in listener.incoming().flatten() {
        let cfg = cfg.clone();
        std::thread::spawn(move || {
            let _ = handle(&cfg, stream);
        });
    }
}

fn handle(cfg: &Config, stream: TcpStream) -> Result<()> {
    stream.set_read_timeout(Some(std::time::Duration::from_secs(10)))?;
    let peer = stream.peer_addr().map(|a| a.ip().to_string()).unwrap_or_default();
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut request = String::new();
    reader.read_line(&mut request)?;
    let mut forwarded = None;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 || line.trim().is_empty() {
            break;
        }
        if let Some((name, value)) = line.split_once(':')
            && name.eq_ignore_ascii_case("x-forwarded-for")
        {
            forwarded = Some(value.trim().to_string());
        }
    }
    let mut parts = request.split_whitespace();
    let (method, uri) = (parts.next().unwrap_or(""), parts.next().unwrap_or("/"));
    let path = uri.split('?').next().unwrap_or("/");
    let mut out = stream;

    match (method, path) {
        ("OPTIONS", _) => respond(&mut out, 204, "text/plain", &[], method, true),
        ("GET" | "HEAD", p) if p.starts_with("/hls/") => {
            let status = match safe_join(&cfg.hls_dir(), &p[5..]) {
                Some(file) if file.is_file() => respond(&mut out, 200, content_type(&file), &std::fs::read(&file)?, method, true),
                _ => respond(&mut out, 404, "text/plain", b"not found", method, true),
            };
            log_view(cfg, &peer, forwarded.as_deref(), uri, status);
            status
        }
        ("GET" | "HEAD", "/now.json") => {
            let now = crate::restream::active(&cfg.data_dir).map(|a| json!({ "live": true, "key": a.stream_key, "channel": a.channel_name }));
            let body = now.unwrap_or(json!({ "live": false })).to_string();
            respond(&mut out, 200, "application/json", body.as_bytes(), method, true)
        }
        ("GET" | "HEAD", "/stat" | "/stat.xsl") => respond(&mut out, 404, "text/plain", b"not found", method, false),
        ("GET" | "HEAD", p) => page(cfg, &mut out, p, method),
        _ => respond(&mut out, 405, "text/plain", b"method not allowed", method, false),
    };
    Ok(())
}

/// Pages: like NGINX's `try_files $uri /index.html` (and `/live` → live.html),
/// or the built-in player when there is no html folder.
fn page(cfg: &Config, out: &mut TcpStream, path: &str, method: &str) -> u16 {
    let html = cfg.html_dir();
    if !html.is_dir() {
        return respond(out, 200, "text/html; charset=utf-8", PLAYER.as_bytes(), method, false);
    }
    let wanted = if path.starts_with("/live") { Some(html.join("live.html")) } else { safe_join(&html, path.trim_start_matches('/')) };
    let file = wanted.filter(|f| f.is_file()).unwrap_or_else(|| html.join("index.html"));
    match std::fs::read(&file) {
        Ok(body) => respond(out, 200, content_type(&file), &body, method, false),
        Err(_) => respond(out, 200, "text/html; charset=utf-8", PLAYER.as_bytes(), method, false),
    }
}

fn respond(out: &mut TcpStream, status: u16, kind: &str, body: &[u8], method: &str, cors: bool) -> u16 {
    let reason = match status {
        200 => "OK",
        204 => "No Content",
        404 => "Not Found",
        _ => "Method Not Allowed",
    };
    let mut head = format!("HTTP/1.1 {status} {reason}\r\nContent-Type: {kind}\r\nContent-Length: {}\r\nCache-Control: no-cache\r\nConnection: close\r\n", body.len());
    if cors {
        head.push_str("Access-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET\r\nAccess-Control-Expose-Headers: Content-Length\r\nAccess-Control-Max-Age: 1728000\r\n");
    }
    head.push_str("\r\n");
    let _ = out.write_all(head.as_bytes());
    if method != "HEAD" && status != 204 {
        let _ = out.write_all(body);
    }
    let _ = out.flush();
    status
}

/// One line per HLS request, exactly NGINX's `viewer` log format:
/// time, client, X-Forwarded-For, URI, status (tab separated).
fn log_view(cfg: &Config, peer: &str, forwarded: Option<&str>, uri: &str, status: u16) {
    let path = cfg.access_log();
    let time = chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, false);
    let line = format!("{time}\t{peer}\t{}\t{uri}\t{status}\n", forwarded.unwrap_or("-"));
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = f.write_all(line.as_bytes());
    }
}

/// `root` + a request path, refusing anything that climbs out of `root`.
fn safe_join(root: &Path, rel: &str) -> Option<PathBuf> {
    let rel = Path::new(rel);
    if rel.components().any(|c| !matches!(c, Component::Normal(_))) {
        return None;
    }
    Some(root.join(rel))
}

fn content_type(file: &Path) -> &'static str {
    match file.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "m3u8" => "application/vnd.apple.mpegurl",
        "ts" => "video/mp2t",
        "html" => "text/html; charset=utf-8",
        "css" => "text/css",
        "js" => "application/javascript",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "svg" => "image/svg+xml",
        "m3u" => "audio/x-mpegurl",
        "json" => "application/json",
        _ => "application/octet-stream",
    }
}

/// Addresses a phone on the same network can use: http://192.168.x.y:8080/
pub fn lan_urls(cfg: &Config) -> Vec<String> {
    // `ip -4 -o addr show scope global`: "2: wlan0    inet 192.168.1.20/24 brd …"
    let out = std::process::Command::new("ip").args(["-4", "-o", "addr", "show", "scope", "global"]).output();
    let text = out.map(|o| String::from_utf8_lossy(&o.stdout).into_owned()).unwrap_or_default();
    text.lines()
        .filter(|l| !l.contains("docker") && !l.contains(" br-") && !l.contains("veth"))
        .filter_map(|l| l.split_whitespace().skip_while(|w| *w != "inet").nth(1))
        .filter_map(|cidr| cidr.split('/').next())
        .map(|ip| format!("http://{ip}:{}/", cfg.web_port))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::safe_join;
    use std::path::Path;

    #[test]
    fn paths_cannot_climb_out() {
        assert!(safe_join(Path::new("/srv/hls"), "noovo.m3u8").is_some());
        assert!(safe_join(Path::new("/srv/hls"), "../../etc/passwd").is_none());
        assert!(safe_join(Path::new("/srv/hls"), "/etc/passwd").is_none());
    }
}
