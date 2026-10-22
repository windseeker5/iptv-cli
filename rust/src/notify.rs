//! Discord messages and the Jellyfin library scan. Both go through curl, with the
//! secret (webhook URL, API key, password) handed over on stdin so it never shows
//! in the process list, and neither ever fails the job that calls it.

use crate::config::Config;
use serde_json::{json, Value};
use std::io::Write;
use std::process::{Command, Stdio};

/// Post to DISCORD_WEBHOOK_URL. Ok(()) when Discord accepted it, else why not.
pub fn discord(cfg: &Config, message: &str) -> Result<(), String> {
    let url = cfg.env("DISCORD_WEBHOOK_URL").ok_or("DISCORD_WEBHOOK_URL is not set")?;
    let content: String = message.chars().take(1900).collect();
    curl(&[("url", &url)], &["-H", "Content-Type: application/json", "--data-binary", &json!({ "content": content }).to_string()])
        .map(|_| ())
        // curl's own error text can include the webhook, which is a secret.
        .map_err(|_| "Discord did not accept the message".to_string())
}

/// Ask Jellyfin to rescan its libraries. Prefers the API key (survives password
/// changes); falls back to the login.
pub fn jellyfin_scan(cfg: &Config) -> Result<(), String> {
    let base = cfg.env("JELLYFIN_PUBLISHED_SERVER_URL").ok_or("JELLYFIN_PUBLISHED_SERVER_URL is not set")?;
    let base = base.trim_end_matches('/');
    let token = match cfg.env("JELLYFIN_API_KEY") {
        Some(key) => key,
        None => match (cfg.env("JELLYFIN_USERNAME"), cfg.env("JELLYFIN_PASSWORD")) {
            (Some(user), Some(password)) => jellyfin_login(base, &user, &password)?,
            _ => return Err("no Jellyfin API key or login configured".into()),
        },
    };
    let url = format!("{base}/Library/Refresh");
    curl(&[("url", &url), ("header", &auth_header(&token))], &["-X", "POST"]).map(|_| ()).map_err(|_| "Jellyfin did not accept the scan request".into())
}

/// Jellyfin 10.11+ only accepts the token in this header (not the old X-Emby-Token).
pub fn auth_header(token: &str) -> String {
    format!(r#"Authorization: MediaBrowser Token="{token}""#)
}

fn jellyfin_login(base: &str, user: &str, password: &str) -> Result<String, String> {
    let url = format!("{base}/Users/AuthenticateByName");
    let auth = r#"Authorization: MediaBrowser Client="KDTV CLI", Device="kdtv", DeviceId="kdtv-automation", Version="1.0.0""#;
    let body = json!({ "Username": user, "Pw": password }).to_string();
    let out = curl(&[("url", &url), ("data-binary", &body)], &["-H", auth, "-H", "Content-Type: application/json"])
        .map_err(|_| "Jellyfin rejected the login".to_string())?;
    serde_json::from_slice::<Value>(&out)
        .ok()
        .and_then(|v| v["AccessToken"].as_str().map(str::to_string))
        .ok_or_else(|| "Jellyfin rejected the login".into())
}

/// Run curl with `secrets` as a config file on stdin (`url = "…"` lines) plus `args`.
fn curl(secrets: &[(&str, &str)], args: &[&str]) -> Result<Vec<u8>, String> {
    let mut child = Command::new("curl")
        .args(["-sS", "--fail", "--max-time", "20", "-K", "-"])
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("running curl: {e}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        for (key, value) in secrets {
            let value = value.replace('\\', "\\\\").replace('"', "\\\"");
            let _ = writeln!(stdin, "{key} = \"{value}\"");
        }
    }
    let out = child.wait_with_output().map_err(|e| format!("running curl: {e}"))?;
    if out.status.success() {
        Ok(out.stdout)
    } else {
        Err(format!("curl exit {}", out.status.code().unwrap_or(-1)))
    }
}
