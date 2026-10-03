#!/usr/bin/env python3
"""JP's runner: the bridge between the OMT site and KD CLI on this host.

Runs as the owner (systemd user service, see systemd/kdcli-agent-runner.service)
and listens on a Unix socket (data/agent/run/runner.sock) that is mounted into
the OMT container: no network port at all, so nothing outside this machine
(or the firewall) is involved. Every request needs
`Authorization: Bearer <AGENT_RUNNER_TOKEN>` (read from ~/Documents/DEV/.env).

  POST /chat     {"session_id"?, "message"}  -> Claude Code (headless, only the
                 kdtv MCP toolbox, no shell/files/web) -> {"session_id", "reply", "pending": [...]}
  POST /confirm  {"pending_id"}              -> execute a proposed action
  POST /cancel   {"pending_id"}
  GET  /state                                -> schedules, restream, recent history

Claude runs on the owner's own Claude Code subscription: personal use only,
which is why the OMT page in front of this is behind a login.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn, UnixStreamServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from iptv_tui.domain import agent_actions as A, schedules  # noqa: E402

SOCKET_PATH = Path(os.environ.get("AGENT_RUNNER_SOCKET", PROJECT_ROOT / "data" / "agent" / "run" / "runner.sock"))
ENV_FILE = Path(os.environ.get("AGENT_ENV_FILE", Path.home() / "Documents" / "DEV" / ".env"))
CLAUDE = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local" / "bin" / "claude"))
MODEL = os.environ.get("AGENT_MODEL", "sonnet")
WORK_DIR = A.AGENT_DIR / "work"           # empty cwd for Claude: nothing to read there
SESSIONS_FILE = A.AGENT_DIR / "sessions.json"
CLAUDE_TIMEOUT = 180
_claude_lock = threading.Lock()


def read_token() -> str:
    try:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("AGENT_RUNNER_TOKEN="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def system_prompt(user: str | None, role: str) -> str:
    now = datetime.now()
    who = (f"You are talking with {user}, the owner." if role == "owner"
           else f"You are talking with {user or 'a family member'}, family. They can do everything: record, delete or "
                "pause schedules, and restream.")
    return f"""You are JP (named in honour of Jean-Paul), the helper on the OMT site (Old Man Tricks).
The owner is a self-taught builder from Rimouski, Québec. He uses KD CLI, his own IPTV app, to record and restream TV.
{who}
Always reply in the same language as the latest message (French or English), short and warm, plain words, no jargon.

Now: {now:%A %Y-%m-%d %H:%M} (local time). TV shows recorded "into Jellyfin" go to
{schedules.tv_library_root()}/<show name>, converted to MP4, then Jellyfin rescans and Discord pings him.

Your ONLY topics:
1. What's on TV (channels, the guide, finding a game or a show).
2. Recordings (make, list, pause, delete) and restreams to the live page.
3. The OMT apps:
   - KD.tv for Google TV / Android TV: install the free "Downloader" app from the Play Store on the TV,
     type apk.dresdell.com/latest.apk, press Go, tap Install, then add your IPTV provider.
   - KD.tv phone app (Android, casts to the TV): download from omt.dresdell.com (Apps section).
   - KD CLI: Linux terminal app, install steps on omt.dresdell.com.
   - The apps are free; people bring their own IPTV subscription. Other projects are listed on omt.dresdell.com.
4. The OMT tricks: short, funny everyday phone and computer tips, coming soon on TikTok and on omt.dresdell.com.
Anything else (sports predictions or scores, news, weather, general questions, homework, advice, chit-chat
beyond a hello) is OFF-TOPIC. Do not answer it, not even partly or "just this once". Reply in one or two short
sentences that you only help with TV, recordings, the OMT apps and the tricks, and offer a related thing you can do
(e.g. "Who will win tonight?" -> you can't predict games, but you can check what channel the game is on or record it).

Format (the chat shows simple markdown):
- Short. Lead with the answer in one sentence. No preamble, no recap of the question, no "here's what I found".
- Up to 6 items: a short bullet list ("- 19:00 · Canadiens vs Leafs · RDS"). More than 6 rows: a table with at most
  3 short columns, then offer to show more. Never more than 10 rows.
- Only list what was asked; drop junk entries (youth, minor, women's leagues, replays) unless asked for.
- Bold only the key item (the channel, the time). No headings, no emojis, no closing questions longer than one line.

Rules:
- Use only your kdtv tools. Always look a channel up with find_channel and use the exact stream_id;
  if several match (HD / 2 / English), pick the obvious one or ask.
- Actions (record, delete, pause, restream) are only PROPOSED: call the propose_* tool, then tell them to
  check the card and tap Confirm. Never say it's done; it isn't until they confirm.
- Only one restream can be live. If propose_start_restream returns a `warning`, say who is streaming what
  and that Confirm will replace it.
- Times are 24h local. "6.29" in the evening = 18:29. Days: Mon Tue Wed Thu Fri Sat Sun.
- One-time recordings need a date (YYYY-MM-DD); "tonight"/"tomorrow" = compute from today.
- If he wants it in Jellyfin, ask for the show name if it's not obvious.
- You can't do anything else (no files, no shell, no web). Say so kindly if asked."""


def load_sessions() -> set:
    try:
        return set(json.loads(SESSIONS_FILE.read_text()))
    except (OSError, ValueError):
        return set()


def save_session(sid: str) -> None:
    s = load_sessions()
    s.add(sid)
    A.AGENT_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(sorted(s)[-200:]))


def ask_claude(session_id: str, message: str, user: str | None = None, role: str = "family") -> dict:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    mcp = {"mcpServers": {"kdtv": {
        "command": str(PROJECT_ROOT / "venv" / "bin" / "python"),
        "args": [str(PROJECT_ROOT / "scripts" / "agent_mcp.py")],
        "env": {"AGENT_SESSION": session_id, "AGENT_USER": user or "", "AGENT_ROLE": role}}}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", dir=A.AGENT_DIR, delete=False) as f:
        json.dump(mcp, f)
        mcp_path = f.name
    resume = session_id in load_sessions()
    cmd = [CLAUDE, "-p", message, "--output-format", "json",
           "--tools", "", "--strict-mcp-config", "--mcp-config", mcp_path,
           "--allowedTools", "mcp__kdtv", "--setting-sources", "",
           "--model", MODEL, "--system-prompt", system_prompt(user, role),
           *(["--resume", session_id] if resume else ["--session-id", session_id])]
    try:
        with _claude_lock:
            proc = subprocess.run(cmd, cwd=WORK_DIR, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"reply": "Sorry, that took too long. Try again in a moment.", "ok": False}
    finally:
        os.unlink(mcp_path)
    # The `claude` launcher (mise shim) may print setup chatter before the JSON.
    json_line = next((l for l in reversed(proc.stdout.splitlines()) if l.lstrip().startswith("{")), "")
    try:
        out = json.loads(json_line)
    except ValueError:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return {"reply": f"JP couldn't answer ({tail or 'no output'}).", "ok": False}
    save_session(session_id)
    if out.get("is_error"):
        msg = out.get("result") or out.get("subtype") or "error"
        if re.search(r"limit|quota|rate", str(msg), re.I):
            msg = "My Claude allowance is used up for now. Try again later."
        return {"reply": msg, "ok": False}
    return {"reply": out.get("result", ""), "ok": True}


def state() -> dict:
    return {"schedules": A.list_schedules(), "restream": A.restream_status(), "history": A.history(15)}


class UnixHTTPServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "jp/1"

    def address_string(self):  # Unix sockets have no client address
        return "omt"

    def _send(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = read_token()
        return bool(token) and self.headers.get("Authorization", "") == f"Bearer {token}"

    def _json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20000:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        if self.path == "/state":
            return self._send(200, state())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        data = self._json()
        if self.path == "/chat":
            message = str(data.get("message", "")).strip()[:2000]
            sid = str(data.get("session_id") or "")
            try:
                sid = str(uuid.UUID(sid))
            except ValueError:
                sid = str(uuid.uuid4())
            if not message:
                return self._send(400, {"error": "empty message"})
            user = str(data.get("user") or "")[:40] or None
            role = "owner" if data.get("role") == "owner" else "family"
            result = ask_claude(sid, message, user, role)
            return self._send(200, {"session_id": sid, **result,
                                    "pending": [{"id": p["id"], "summary": p["summary"], "kind": p["kind"]}
                                                for p in A.pending_for(sid)]})
        if self.path == "/confirm":
            role = "owner" if data.get("role") == "owner" else "family"
            return self._send(200, A.execute(str(data.get("pending_id", "")), str(data.get("user") or "") or None, role))
        if self.path == "/cancel":
            return self._send(200, {"ok": A.cancel(str(data.get("pending_id", "")), str(data.get("user") or "") or None)})
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):  # keep the journal short, no request bodies
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main():
    if not read_token():
        sys.exit(f"AGENT_RUNNER_TOKEN missing in {ENV_FILE}")
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    srv = UnixHTTPServer(str(SOCKET_PATH), Handler)
    os.chmod(SOCKET_PATH, 0o660)
    print(f"JP runner on {SOCKET_PATH}", file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    main()
