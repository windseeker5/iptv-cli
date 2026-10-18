//! Where kd finds its data and the provider login.
//!
//! kd reads one small file, `kd.toml`, looked up in this order: `$KD_CONFIG`,
//! `./kd.toml`, `~/.config/kd/kd.toml`. Paths inside it are relative to the
//! file itself. Without a file, kd uses `./data` and `./.env`.
//!
//! ```toml
//! profile = "home"            # or "travel"
//! data_dir = "../cli/data"
//! env_file = "../cli/.env"
//! containers = ["iptv-nginx-rtmp", "iptv-jellyfin", "caddy", "omt"]
//! ```

use anyhow::{Context, Result};
use serde::Deserialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const DEFAULT_CONTAINERS: &[&str] = &["iptv-nginx-rtmp", "iptv-jellyfin", "caddy", "omt"];

#[derive(Deserialize, Default)]
struct ConfigFile {
    profile: Option<String>,
    data_dir: Option<PathBuf>,
    env_file: Option<PathBuf>,
    containers: Option<Vec<String>>,
}

pub struct Config {
    pub profile: String,
    pub data_dir: PathBuf,
    pub containers: Vec<String>,
    pub provider: Provider,
}

/// The provider login. Deliberately not `Debug`: it must never end up in a log.
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
                (file, p.parent().unwrap_or(Path::new(".")).to_path_buf())
            }
            None => (ConfigFile::default(), PathBuf::from(".")),
        };

        let data_dir = base.join(file.data_dir.unwrap_or_else(|| "data".into()));
        let env_file = base.join(file.env_file.unwrap_or_else(|| ".env".into()));
        let env = read_env(&env_file);
        let get = |key: &str| std::env::var(key).ok().or_else(|| env.get(key).cloned()).unwrap_or_default();

        Ok(Config {
            profile: file.profile.unwrap_or_else(|| "home".into()),
            data_dir,
            containers: file
                .containers
                .unwrap_or_else(|| DEFAULT_CONTAINERS.iter().map(|s| s.to_string()).collect()),
            provider: Provider {
                server: get("IPTV_SERVER_URL").trim_end_matches('/').to_string(),
                username: get("IPTV_USERNAME"),
                password: get("IPTV_PASSWORD"),
            },
        })
    }
}

fn find_config_file() -> Option<PathBuf> {
    let mut candidates = vec![];
    if let Ok(p) = std::env::var("KD_CONFIG") {
        candidates.push(PathBuf::from(p));
    }
    candidates.push(PathBuf::from("kd.toml"));
    if let Ok(home) = std::env::var("HOME") {
        candidates.push(Path::new(&home).join(".config/kd/kd.toml"));
    }
    candidates.into_iter().find(|p| p.is_file())
}

/// The .env file as key/value pairs, without touching the process environment.
fn read_env(path: &Path) -> HashMap<String, String> {
    dotenvy::from_path_iter(path)
        .map(|iter| iter.filter_map(Result::ok).collect())
        .unwrap_or_default()
}
