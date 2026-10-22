//! `kdtv jp-runner`: the bridge between the OMT site and this host.
//!
//! Runs as the owner (systemd user service `kdcli-agent-runner`) and listens on a
//! Unix socket (`data/agent/run/runner.sock`) mounted into the OMT container: no
//! network port at all. Every request needs `Authorization: Bearer
//! <AGENT_RUNNER_TOKEN>` (read from ~/Documents/DEV/.env on each request).
//!
//!   POST /chat     {"session_id"?, "message", "user", "role"} -> Claude Code (headless,
//!                  only the kdtv MCP toolbox) -> {"session_id", "reply", "ok", "pending": [...]}
//!   POST /confirm  {"pending_id", "user", "role"} -> run a proposed action
//!   POST /cancel   {"pending_id", "user"}
//!   GET  /state    -> schedules, restream, recent history
//!
//! Claude runs on the owner's own Claude Code subscription: personal use only,
//! which is why the OMT page in front of this is behind a login.

use super::actions as a;
use crate::config::Config;
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

const CLAUDE_TIMEOUT_SECONDS: u64 = 180;
const MAX_BODY: usize = 20_000;
/// One Claude call at a time: they share the owner's subscription.
static CLAUDE: Mutex<()> = Mutex::new(());

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default())
}

fn env_file() -> PathBuf {
    std::env::var("AGENT_ENV_FILE").map(PathBuf::from).unwrap_or_else(|_| home().join("Documents/DEV/.env"))
}

fn token() -> String {
    std::fs::read_to_string(env_file())
        .unwrap_or_default()
        .lines()
        .find_map(|l| l.strip_prefix("AGENT_RUNNER_TOKEN="))
        .map(|v| v.trim().trim_matches(['\'', '"']).to_string())
        .unwrap_or_default()
}

pub fn serve(cfg: &Config) -> Result<()> {
    if token().is_empty() {
        anyhow::bail!("AGENT_RUNNER_TOKEN missing in {}", env_file().display());
    }
    let socket = std::env::var("AGENT_RUNNER_SOCKET").map(PathBuf::from).unwrap_or_else(|_| cfg.data_dir.join("agent/run/runner.sock"));
    if let Some(dir) = socket.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let _ = std::fs::remove_file(&socket);
    let listener = UnixListener::bind(&socket).with_context(|| format!("listening on {}", socket.display()))?;
    std::fs::set_permissions(&socket, std::os::unix::fs::PermissionsExt::from_mode(0o660))?;
    eprintln!("JP runner on {}", socket.display());
    for stream in listener.incoming() {
        let Ok(stream) = stream else { continue };
        let cfg = cfg.clone();
        std::thread::spawn(move || {
            if let Err(e) = handle(&cfg, stream) {
                eprintln!("omt request failed: {e:#}");
            }
        });
    }
    Ok(())
}

fn handle(cfg: &Config, stream: UnixStream) -> Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut request_line = String::new();
    reader.read_line(&mut request_line)?;
    let mut parts = request_line.split_whitespace();
    let (method, path) = (parts.next().unwrap_or("").to_string(), parts.next().unwrap_or("").to_string());
    let (mut length, mut auth) = (0usize, String::new());
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 || line.trim().is_empty() {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            match name.trim().to_ascii_lowercase().as_str() {
                "content-length" => length = value.trim().parse().unwrap_or(0),
                "authorization" => auth = value.trim().to_string(),
                _ => {}
            }
        }
    }
    let mut body = vec![0u8; length.min(MAX_BODY)];
    reader.read_exact(&mut body)?;
    let data: Value = if length > MAX_BODY { json!({}) } else { serde_json::from_slice(&body).unwrap_or(json!({})) };

    let token = token();
    let (code, reply) = if token.is_empty() || auth != format!("Bearer {token}") {
        (401, json!({ "error": "unauthorized" }))
    } else {
        route(cfg, &method, &path, &data)
    };
    eprintln!("omt \"{method} {path}\" {code}");
    let body = reply.to_string();
    let reason = match code {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        _ => "Not Found",
    };
    let mut stream = stream;
    write!(stream, "HTTP/1.1 {code} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len())?;
    stream.flush()?;
    Ok(())
}

fn route(cfg: &Config, method: &str, path: &str, data: &Value) -> (u16, Value) {
    let user = data["user"].as_str().map(|u| u.chars().take(40).collect::<String>()).filter(|u| !u.is_empty());
    let owner = data["role"] == "owner";
    match (method, path) {
        ("GET", "/state") => (200, json!({ "schedules": a::list_schedules(cfg), "restream": a::restream_status(cfg), "history": a::history(cfg, 15) })),
        ("POST", "/chat") => {
            let message: String = data["message"].as_str().unwrap_or("").trim().chars().take(2000).collect();
            if message.is_empty() {
                return (400, json!({ "error": "empty message" }));
            }
            let session = data["session_id"].as_str().filter(|s| is_uuid(s)).map(str::to_lowercase).unwrap_or_else(new_uuid);
            let mut out = ask_claude(cfg, &session, &message, user.as_deref(), owner);
            out["session_id"] = session.clone().into();
            out["pending"] = a::pending_for(cfg, &session)
                .iter()
                .map(|p| json!({ "id": p["id"], "summary": p["summary"], "kind": p["kind"], "card": p["card"] }))
                .collect();
            (200, out)
        }
        ("POST", "/confirm") => (200, a::execute(cfg, data["pending_id"].as_str().unwrap_or(""), user.as_deref(), owner)),
        ("POST", "/cancel") => (200, json!({ "ok": a::cancel(cfg, data["pending_id"].as_str().unwrap_or(""), user.as_deref()) })),
        _ => (404, json!({ "error": "not found" })),
    }
}

fn is_uuid(s: &str) -> bool {
    let groups: Vec<&str> = s.split('-').collect();
    groups.iter().map(|g| g.len()).eq([8, 4, 4, 4, 12]) && groups.iter().all(|g| g.chars().all(|c| c.is_ascii_hexdigit()))
}

/// A random (version 4) UUID.
fn new_uuid() -> String {
    let h = super::random_hex(16);
    let variant = ["8", "9", "a", "b"][usize::from_str_radix(&h[16..17], 16).unwrap_or(0) % 4];
    format!("{}-{}-4{}-{}{}-{}", &h[0..8], &h[8..12], &h[13..16], variant, &h[17..20], &h[20..32])
}

fn sessions_file(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("agent/sessions.json")
}

fn known_sessions(cfg: &Config) -> Vec<String> {
    std::fs::read_to_string(sessions_file(cfg)).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default()
}

fn save_session(cfg: &Config, id: &str) {
    let mut all = known_sessions(cfg);
    if !all.iter().any(|s| s == id) {
        all.push(id.to_string());
    }
    all.sort();
    let keep = &all[all.len().saturating_sub(200)..];
    let _ = std::fs::write(sessions_file(cfg), json!(keep).to_string());
}

fn ask_claude(cfg: &Config, session: &str, message: &str, user: Option<&str>, owner: bool) -> Value {
    let fail = |reply: String| json!({ "reply": reply, "ok": false });
    let agent_dir = cfg.data_dir.join("agent");
    let work = agent_dir.join("work"); // an empty folder: nothing for Claude to read there
    let _ = std::fs::create_dir_all(&work);
    let Ok(exe) = std::env::current_exe() else { return fail("JP couldn't find the kdtv program.".into()) };
    let mut args: Vec<String> = cfg.config_args();
    args.push("jp-mcp".into());
    let mcp = json!({ "mcpServers": { "kdtv": {
        "command": exe, "args": args,
        "env": { "AGENT_SESSION": session, "AGENT_USER": user.unwrap_or(""), "AGENT_ROLE": if owner { "owner" } else { "family" } },
    }}});
    let mcp_path = agent_dir.join(format!("mcp-{}.json", super::random_hex(6)));
    if std::fs::write(&mcp_path, mcp.to_string()).is_err() {
        return fail("JP couldn't start (could not write its tool list).".into());
    }

    let claude = std::env::var("CLAUDE_BIN").map(PathBuf::from).unwrap_or_else(|_| home().join(".local/bin/claude"));
    let model = std::env::var("AGENT_MODEL").unwrap_or_else(|_| "sonnet".into());
    let resume = known_sessions(cfg).iter().any(|s| s == session);
    let mut cmd = Command::new("timeout");
    cmd.arg(CLAUDE_TIMEOUT_SECONDS.to_string())
        .arg(&claude)
        .args(["-p", message, "--output-format", "json", "--tools", "", "--strict-mcp-config", "--mcp-config"])
        .arg(&mcp_path)
        .args(["--allowedTools", "mcp__kdtv", "--setting-sources", "", "--model", &model, "--system-prompt"])
        .arg(super::prompt::system_prompt(cfg, user, owner))
        .args(if resume { ["--resume", session] } else { ["--session-id", session] })
        .current_dir(&work)
        .stdin(Stdio::null());
    let output = {
        let _one_at_a_time = CLAUDE.lock().unwrap_or_else(|e| e.into_inner());
        cmd.output()
    };
    let _ = std::fs::remove_file(&mcp_path);
    let Ok(output) = output else { return fail("JP couldn't start Claude Code.".into()) };
    if output.status.code() == Some(124) {
        return fail("Sorry, that took too long. Try again in a moment.".into());
    }
    // The `claude` launcher may print setup chatter before the JSON.
    let stdout = String::from_utf8_lossy(&output.stdout);
    let Some(out) = stdout.lines().rev().find(|l| l.trim_start().starts_with('{')).and_then(|l| serde_json::from_str::<Value>(l).ok()) else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let tail = if stderr.trim().is_empty() { stdout.trim() } else { stderr.trim() };
        let tail: String = tail.chars().rev().take(300).collect::<Vec<_>>().into_iter().rev().collect();
        return fail(format!("JP couldn't answer ({}).", if tail.is_empty() { "no output" } else { &tail }));
    };
    save_session(cfg, session);
    if out["is_error"] == true {
        let msg = out["result"].as_str().or(out["subtype"].as_str()).unwrap_or("error").to_string();
        let lower = msg.to_lowercase();
        if ["limit", "quota", "rate"].iter().any(|w| lower.contains(w)) {
            return fail("My Claude allowance is used up for now. Try again later.".into());
        }
        return fail(msg);
    }
    json!({ "reply": out["result"].as_str().unwrap_or(""), "ok": true })
}

#[cfg(test)]
mod tests {
    use super::{is_uuid, new_uuid};

    #[test]
    fn session_ids_are_uuids() {
        let id = new_uuid();
        assert!(is_uuid(&id), "{id}");
        assert_eq!(&id[14..15], "4");
        assert!(is_uuid("6f1c2a9e-3b4d-4e5f-8a7b-0c1d2e3f4a5b"));
        assert!(!is_uuid("../../etc/passwd"));
        assert!(!is_uuid("6f1c2a9e3b4d4e5f8a7b0c1d2e3f4a5b"));
    }
}
