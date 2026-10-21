//! Where kd finds the CLI, its data and the provider login.
//!
//! kdtv reads one small file, `kdtv.toml`, looked up in this order: `--config`,
//! `$KDTV_CONFIG`, `./kdtv.toml`, `~/.config/kdtv/kdtv.toml` (the older `kd.toml`
//! names still work). Paths inside it are relative to the file itself. Without a
//! file, kdtv uses the current folder.
//!
//! ```toml
//! profile = "home"            # or "travel"
//! cli_dir = "../cli"          # the KDTV folder: data/ and .env
//! # Optional overrides
//! records_dir = "~/Videos/kd" # where recordings go (default: USB_RECORDS_PATH from .env)
//! containers = ["iptv-nginx-rtmp", "iptv-jellyfin", "caddy", "omt"]
//! icons = true                # Nerd Font icons; false on a terminal without one
//! restream_via = "nginx"      # or "kdtv": FFmpeg writes HLS and kdtv serves it
//! web_port = 8080             # where kdtv serves the restream (restream_via = "kdtv")
//! ```

use anyhow::{Context, Result};
use serde::Deserialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const DEFAULT_CONTAINERS: &[&str] = &["iptv-nginx-rtmp", "iptv-jellyfin", "caddy", "omt"];

#[derive(Deserialize, Default)]
struct ConfigFile {
    profile: Option<String>,
    cli_dir: Option<PathBuf>,
    data_dir: Option<PathBuf>,
    env_file: Option<PathBuf>,
    records_dir: Option<PathBuf>,
    downloads_dir: Option<PathBuf>,
    compose_dir: Option<PathBuf>,
    watch_url: Option<String>,
    containers: Option<Vec<String>>,
    icons: Option<bool>,
    restream_via: Option<String>,
    web_port: Option<u16>,
    web_bind: Option<String>,
    hls_dir: Option<PathBuf>,
    html_dir: Option<PathBuf>,
    access_log: Option<PathBuf>,
}

/// Who receives the restream and serves it to viewers.
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum RestreamVia {
    /// FFmpeg pushes RTMP to the NGINX-RTMP container, which serves HLS.
    Nginx,
    /// FFmpeg writes HLS files and kdtv's own web server serves them.
    Kdtv,
}

#[derive(Clone)]
pub struct Config {
    /// The kd.toml in use, handed to the background workers kd starts.
    pub file: Option<PathBuf>,
    pub profile: String,
    /// The KDTV folder, holding `.env` and (by default) `data/`.
    pub cli_dir: PathBuf,
    pub data_dir: PathBuf,
    records_dir: Option<PathBuf>,
    downloads_dir: Option<PathBuf>,
    /// The folder holding docker-compose.yml.
    pub compose_dir: PathBuf,
    pub watch_url: String,
    pub containers: Vec<String>,
    /// Nerd Font icons in the TUI.
    pub icons: bool,
    pub restream_via: RestreamVia,
    pub web_port: u16,
    pub web_bind: String,
    hls_dir: Option<PathBuf>,
    html_dir: Option<PathBuf>,
    access_log: Option<PathBuf>,
    /// Where the provider login comes from (the CLI's .env).
    pub env_file: PathBuf,
    pub provider: Provider,
    env: HashMap<String, String>,
}

/// The provider login. Deliberately not `Debug`: it must never end up in a log.
#[derive(Clone)]
pub struct Provider {
    pub server: String,
    pub username: String,
    pub password: String,
}

impl Provider {
    pub fn stream_url(&self, stream_id: i64) -> String {
        format!("{}/live/{}/{}/{}.ts", self.server, self.username, self.password, stream_id)
    }

    /// Replace the server, user name and password with `***` anywhere in `text`.
    pub fn redact(&self, text: &str) -> String {
        let mut out = text.to_string();
        for secret in [&self.password, &self.username, &self.server] {
            if secret.len() >= 3 {
                out = out.replace(secret.as_str(), "***");
            }
        }
        out
    }
}

impl Config {
    pub fn load(explicit: Option<&Path>) -> Result<Config> {
        let path = explicit.map(Path::to_path_buf).or_else(find_config_file);
        let (file, base) = match &path {
            Some(p) => {
                let text = std::fs::read_to_string(p).with_context(|| format!("reading {}", p.display()))?;
                let file: ConfigFile = toml::from_str(&text).with_context(|| format!("parsing {}", p.display()))?;
                (file, absolute(p.parent().unwrap_or(Path::new("."))))
            }
            None => (ConfigFile::default(), absolute(Path::new("."))),
        };
        let at = |p: PathBuf| normalize(&base.join(expand_home(&p)));

        let cli_dir = file.cli_dir.map(at).unwrap_or_else(|| normalize(&base));
        let data_dir = file.data_dir.map(at).unwrap_or_else(|| cli_dir.join("data"));
        let env_file = file.env_file.map(at).unwrap_or_else(|| cli_dir.join(".env"));
        let env = read_env(&env_file);
        let get = |key: &str| std::env::var(key).ok().or_else(|| env.get(key).cloned()).unwrap_or_default();
        let provider = Provider {
            server: get("IPTV_SERVER_URL").trim_end_matches('/').to_string(),
            username: get("IPTV_USERNAME"),
            password: get("IPTV_PASSWORD"),
        };

        Ok(Config {
            file: path.map(|p| absolute(&p)),
            profile: file.profile.unwrap_or_else(|| "home".into()),
            compose_dir: file.compose_dir.map(at).unwrap_or_else(|| normalize(&cli_dir.join("../.."))),
            records_dir: file.records_dir.map(at),
            downloads_dir: file.downloads_dir.map(at),
            watch_url: file.watch_url.unwrap_or_else(|| "https://live.dresdell.com/".into()),
            containers: file
                .containers
                .unwrap_or_else(|| DEFAULT_CONTAINERS.iter().map(|s| s.to_string()).collect()),
            icons: file.icons.unwrap_or(true),
            restream_via: if file.restream_via.as_deref() == Some("kdtv") { RestreamVia::Kdtv } else { RestreamVia::Nginx },
            web_port: file.web_port.unwrap_or(8080),
            web_bind: file.web_bind.unwrap_or_else(|| "0.0.0.0".into()),
            hls_dir: file.hls_dir.map(at),
            html_dir: file.html_dir.map(at),
            access_log: file.access_log.map(at),
            env_file,
            cli_dir,
            data_dir,
            provider,
            env,
        })
    }

    /// A value from the CLI's .env (the process environment wins, like python-dotenv).
    pub fn env(&self, key: &str) -> Option<String> {
        std::env::var(key).ok().or_else(|| self.env.get(key).cloned()).filter(|v| !v.is_empty())
    }

    /// A path from the CLI's .env; relative ones are relative to the CLI folder.
    fn env_path(&self, key: &str) -> Option<PathBuf> {
        self.env(key).map(|v| normalize(&self.cli_dir.join(expand_home(Path::new(&v)))))
    }

    /// Preferred recordings folder: `records_dir` in kd.toml, else USB_RECORDS_PATH.
    pub fn records_dir(&self) -> PathBuf {
        self.records_dir
            .clone()
            .or_else(|| self.env_path("USB_RECORDS_PATH"))
            .unwrap_or_else(|| self.cli_dir.join("nginx/recordings"))
    }

    pub fn downloads_dir(&self) -> PathBuf {
        self.downloads_dir.clone().unwrap_or_else(|| self.data_dir.join("downloads"))
    }

    /// Where FFmpeg writes the restream's HLS files (restream_via = "kdtv").
    pub fn hls_dir(&self) -> PathBuf {
        self.hls_dir.clone().unwrap_or_else(|| self.data_dir.join("hls"))
    }

    /// The pages NGINX used to serve (/live, /iptv.m3u…); missing on the road.
    pub fn html_dir(&self) -> PathBuf {
        self.html_dir.clone().unwrap_or_else(|| self.cli_dir.join("nginx/html"))
    }

    /// The viewer log OMT and tv-counter read to count who is watching.
    pub fn access_log(&self) -> PathBuf {
        self.access_log.clone().unwrap_or_else(|| self.cli_dir.join("nginx/logs/hls_access.log"))
    }

    /// Folder with one sub-folder per TV show in Jellyfin.
    pub fn tv_library_root(&self) -> Option<PathBuf> {
        self.env_path("TVSHOW_LIBRARY_ROOT")
            .or_else(|| self.env_path("NOOVO_LIBRARY_DIR").and_then(|p| p.parent().map(Path::to_path_buf)))
    }

    /// The kd.toml argument to hand to a worker started under systemd.
    pub fn config_args(&self) -> Vec<String> {
        match &self.file {
            Some(f) => vec!["--config".into(), f.display().to_string()],
            None => vec![],
        }
    }
}

fn find_config_file() -> Option<PathBuf> {
    let mut candidates = vec![];
    for var in ["KDTV_CONFIG", "KD_CONFIG"] {
        if let Ok(p) = std::env::var(var) {
            candidates.push(PathBuf::from(p));
        }
    }
    candidates.extend([PathBuf::from("kdtv.toml"), PathBuf::from("kd.toml")]);
    if let Ok(home) = std::env::var("HOME") {
        candidates.push(Path::new(&home).join(".config/kdtv/kdtv.toml"));
        candidates.push(Path::new(&home).join(".config/kd/kd.toml"));
    }
    candidates.into_iter().find(|p| p.is_file())
}

fn absolute(p: &Path) -> PathBuf {
    std::path::absolute(p).unwrap_or_else(|_| p.to_path_buf())
}

/// "/a/cli-rust/../cli" → "/a/cli". systemd refuses paths with `..` in them.
fn normalize(p: &Path) -> PathBuf {
    if let Ok(real) = p.canonicalize() {
        return real;
    }
    let mut out = PathBuf::new();
    for part in p.components() {
        match part {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            other => out.push(other),
        }
    }
    out
}

fn expand_home(p: &Path) -> PathBuf {
    match (p.strip_prefix("~"), std::env::var("HOME")) {
        (Ok(rest), Ok(home)) => Path::new(&home).join(rest),
        _ => p.to_path_buf(),
    }
}

/// The .env file as key/value pairs, without touching the process environment.
fn read_env(path: &Path) -> HashMap<String, String> {
    std::fs::read_to_string(path).map(|text| parse_env(&text)).unwrap_or_default()
}

/// As forgiving as python-dotenv, which the .env was first written for: unquoted
/// values may contain spaces, `export ` is allowed, `#` starts a comment.
fn parse_env(text: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for line in text.lines() {
        let line = line.trim();
        let line = line.strip_prefix("export ").unwrap_or(line);
        if line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else { continue };
        let value = value.trim();
        let value = match value.chars().next() {
            Some(q @ ('"' | '\'')) => value[1..].split(q).next().unwrap_or("").to_string(),
            _ => value.split(" #").next().unwrap_or("").trim().to_string(),
        };
        out.insert(key.trim().to_string(), value);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::{normalize, parse_env};
    use std::path::Path;

    #[test]
    fn paths_have_no_dot_dot_for_systemd() {
        assert_eq!(normalize(Path::new("/no/such/kd-rust/../cli/./x")), Path::new("/no/such/cli/x"));
    }

    #[test]
    fn reads_env_files_like_python_dotenv() {
        let env = parse_env("# c\nA=1\nB=/mnt/Home Media/TV Show\nexport C=\"x y\" # z\nD='q' \nE=v # note\nbad line\n");
        assert_eq!(env["A"], "1");
        assert_eq!(env["B"], "/mnt/Home Media/TV Show");
        assert_eq!(env["C"], "x y");
        assert_eq!(env["D"], "q");
        assert_eq!(env["E"], "v");
        assert_eq!(env.len(), 5);
    }
}
