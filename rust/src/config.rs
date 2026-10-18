//! Where kd finds the CLI, its data and the provider login.
//!
//! kd reads one small file, `kd.toml`, looked up in this order: `--config`,
//! `$KD_CONFIG`, `./kd.toml`, `~/.config/kd/kd.toml`. Paths inside it are
//! relative to the file itself. Without a file, kd uses the current folder.
//!
//! ```toml
//! profile = "home"            # or "travel"
//! cli_dir = "../cli"          # the Python CLI: scripts/, venv/, data/, .env
//! # Optional overrides
//! records_dir = "~/Videos/kd" # where recordings go (default: USB_RECORDS_PATH from .env)
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
    cli_dir: Option<PathBuf>,
    data_dir: Option<PathBuf>,
    env_file: Option<PathBuf>,
    records_dir: Option<PathBuf>,
    downloads_dir: Option<PathBuf>,
    compose_dir: Option<PathBuf>,
    watch_url: Option<String>,
    containers: Option<Vec<String>>,
}

#[derive(Clone)]
pub struct Config {
    /// The kd.toml in use, handed to the background workers kd starts.
    pub file: Option<PathBuf>,
    pub profile: String,
    /// The Python CLI (scripts/, venv/). Its own data folder is `cli_dir/data`.
    pub cli_dir: PathBuf,
    pub data_dir: PathBuf,
    records_dir: Option<PathBuf>,
    downloads_dir: Option<PathBuf>,
    /// The folder holding docker-compose.yml.
    pub compose_dir: PathBuf,
    pub watch_url: String,
    pub containers: Vec<String>,
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
        let at = |p: PathBuf| base.join(expand_home(&p));

        let cli_dir = file.cli_dir.map(at).unwrap_or_else(|| base.clone());
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
            compose_dir: file.compose_dir.map(at).unwrap_or_else(|| cli_dir.join("../..")),
            records_dir: file.records_dir.map(at),
            downloads_dir: file.downloads_dir.map(at),
            watch_url: file.watch_url.unwrap_or_else(|| "https://live.dresdell.com/".into()),
            containers: file
                .containers
                .unwrap_or_else(|| DEFAULT_CONTAINERS.iter().map(|s| s.to_string()).collect()),
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

    /// A path from the CLI's .env; relative ones are relative to the CLI folder,
    /// which is where the Python side runs from.
    fn env_path(&self, key: &str) -> Option<PathBuf> {
        self.env(key).map(|v| self.cli_dir.join(expand_home(Path::new(&v))))
    }

    pub fn python(&self) -> PathBuf {
        self.cli_dir.join("venv/bin/python3")
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
    if let Ok(p) = std::env::var("KD_CONFIG") {
        candidates.push(PathBuf::from(p));
    }
    candidates.push(PathBuf::from("kd.toml"));
    if let Ok(home) = std::env::var("HOME") {
        candidates.push(Path::new(&home).join(".config/kd/kd.toml"));
    }
    candidates.into_iter().find(|p| p.is_file())
}

fn absolute(p: &Path) -> PathBuf {
    std::path::absolute(p).unwrap_or_else(|_| p.to_path_buf())
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

/// As forgiving as python-dotenv, which the CLI's .env is written for: unquoted
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
    use super::parse_env;

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
