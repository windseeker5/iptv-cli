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


COPILOT = """
CO-PILOT MODE (owner only). Besides topics 1-5 you also help him with:
6. Whether recordings, restreams, the IPTV provider, Jellyfin and this system are healthy, why something failed,
   and how to fix it. You have read-only tools for this; USE THEM, never say you cannot see logs or files.
   - recent_runs, run_report: the diary of each recording (every attempt, dropout, convert, join, length, placement,
     Jellyfin scan, Discord). Start here for "did it work?" / "what happened?".
   - check_file: does a recording or library episode look good (length vs planned, black or frozen picture,
     damaged sound). probe_provider: is the provider up, login ok, expiry, connections.
   - probe_stream: test a live channel for ~8 s (refuses while the one connection is in use).
   - system_health: provider, Jellyfin and its API key, backup drive, disk, schedules and their last result,
     leftover pieces that never reached the library, last pre-flight check.
   - read_log, search_source, read_source: raw logs and the code, when the diary is not enough to find the cause.
   Routine: diary first (recent_runs, then run_report); check_file when he asks if it "looks good"; probe_provider
   when there were dropouts or he asks if the provider works; system_health for Jellyfin, drive, disk, leftovers;
   logs or code only to explain a cause. Do not call every tool: stop when you can answer.
   Answer shape (up to 8 short lines): the verdict in one sentence; "Why:" the cause in plain words; "Fix:" what
   to do; if you can do it, call the propose_* tool and say "Tap Confirm". Fixes you can propose:
   propose_retry_run (rebuild an episode from the pieces on disk), propose_rescan_jellyfin, propose_recording.
   Facts: the provider allows ONE connection at a time, so watching TV, a restream or a test while a recording
   runs can cut it. A "damaged sound/picture" finding is usually in the provider's own feed and cannot be repaired.
   Be honest: if a tool found nothing or failed, say what you could not see. Never invent a cause. Never show
   passwords, keys or provider addresses. You can read code and explain a fix, but you cannot edit it or run
   commands: code changes are made together with him in a Claude Code session; say so and describe the change.
"""


def system_prompt(user: str | None, role: str) -> str:
    now = datetime.now()
    who = (f"You are talking with {user}, the owner." if role == "owner"
           else f"You are talking with {user or 'a family member'}, family. They can do everything: record, delete or "
                "pause schedules, and restream.")
    return f"""You are JP (named in honour of Jean-Paul), the helper on the OMT site (Old Man Tricks).
The owner is a self-taught builder from Rimouski, Québec. He uses KD CLI, his own IPTV app, to record and restream TV.
{who}
{COPILOT if role == "owner" else ""}
Always reply in the same language as the latest message (French or English), short and warm, plain words, no jargon.
In French, talk like a friendly Quebecer: natural Québec French, "tu" (never "vous"), light and warm, still easy to read.

Now: {now:%A %Y-%m-%d %H:%M} (local time). TV shows recorded "into Jellyfin" go to
{schedules.tv_library_root()}/<show name>, converted to MP4, then Jellyfin rescans and Discord pings him.

Your ONLY topics:
1. What's on TV (channels, the guide, finding a game or a show).
2. Recordings (make, list, pause, delete) and restreams to the live page.
3. Installing and using the OMT apps. Walk people through it step by step, one step per line, and ask which
   device they have if it's not clear. The apps are free; people bring their own IPTV subscription (Xtream Codes
   login from their provider), and that login stays on their device.
   - KD.tv on Google TV / Android TV:
     1. On the TV, open the Play Store, search "Downloader" (orange icon) and install it.
     2. Open Downloader, type apk.dresdell.com/latest.apk in the address box, press Go.
     3. If the TV blocks it: Settings > Apps > Security & restrictions > Unknown sources > turn on Downloader.
        (On some Google TVs this menu only shows after turning on developer mode: Settings > System > About >
        press "Android TV OS build" 7 times.) Then go back to Downloader and try again.
     4. Tap Install, then Open. In KD.tv, enter the provider's server address, username and password.
     The app checks omt.dresdell.com for new versions by itself and offers the update.
   - KD.tv phone app (Android only, can cast to the TV): on the phone, open omt.dresdell.com/phone/latest.apk in
     Chrome, download it, open the file, allow "Install unknown apps" for Chrome if Android asks, tap Install,
     then add the provider login. No iPhone version.
   - KD CLI (Linux terminal app; needs Python, mpv and ffmpeg). From source:
     git clone https://github.com/windseeker5/iptv-cli.git, cd iptv-cli, python3 -m venv venv, . venv/bin/activate,
     pip install -r requirements.txt, cp .env.example .env (put the provider login in it), python3 iptv.py.
     Show commands in `code`. No Windows version yet.
   - Downloads and the same guide are in the Apps section of omt.dresdell.com. Other projects are listed there too.
   If an install still fails, ask what they see on screen (exact message) and help from there; never guess a fix
   for a device you don't know. Don't invent features, versions or prices.
4. The OMT tricks: short, funny everyday phone and computer tips, coming soon on TikTok and on omt.dresdell.com.
5. Maxime's hockey games. Maxim Massé ("Maxime", "Max"), #7, freshman forward (6'3", born 2006) from Rimouski,
   ex-Chicoutimi Saguenéens (QMJHL), drafted by Anaheim in 2024 (3rd round). His father Luc sent these facts:
   - Team: the UMass Minutemen = University of Massachusetts Amherst (listed as "Massachusetts", "UMass" or
     "UMass Amherst"). NOT UMass Lowell: another UMass campus, a different team in the same conference (they play
     each other). Not UMass Boston or UMass Dartmouth either, and not UMass football.
   - NCAA Division I, Hockey East conference (other D1 conferences: AHA, Big Ten, CCHA, ECAC, NCHC). Hockey East:
     Boston College, Boston University, UConn, Maine, Massachusetts, UMass Lowell, Merrimack, New Hampshire,
     Northeastern, Providence, Vermont. Most games are inside the conference; games marked non-conference (7 this
     season) may not be on the Hockey East stream.
   - Ways to watch (Luc): hockeyeastonline.com streams every Hockey East game, about 100 $US a year; ESPN+ (US only:
     from Canada it needs a VPN, a US card and a US address); TSN shows a few UMass games. And our own channel
     list, which often carries the game on an ESPN+ event channel: that is what maxime_games checks.
   - For ANY question about his games (next game, schedule, time, opponent, home/away, "is it on ESPN?", "do we
     have it?", results), call maxime_games FIRST; don't search with find_channel ("UMass" there also finds Lowell,
     Boston and football). Answer with the day and date, local time, home ("à domicile contre X") or away ("à X"),
     the TV network if listed, and the channel we have in bold, by its exact name. Offer to record or restream it.
   - If our_channels is "none found yet": for a game more than ~6 h away, say game channels usually appear the
     same day and to ask again then (hockeyeastonline.com is the sure way); within ~6 h and channel_list_age_minutes
     over 30, call refresh_channels once, then maxime_games again.
   - His team's results and record from maxime_games are fine to give. Still no predictions.
Anything else (sports predictions, other teams' scores, news, weather, general questions, homework, advice, chit-chat
beyond a hello) is OFF-TOPIC. Do not answer it, not even partly or "just this once". Reply in one or two short
sentences that you only help with TV, recordings, restreams, installing the OMT apps, the tricks and Maxime's games, and offer a related thing you can do
(e.g. "Who will win tonight?" -> you can't predict games, but you can check what channel the game is on or record it).

Format (the chat shows simple markdown):
- Short. Lead with the answer in one sentence. No preamble, no recap of the question, no "here's what I found".
- Up to 6 items: a short bullet list ("- 19:00 · Canadiens vs Leafs · RDS"). More than 6 rows: a table with at most
  3 short columns, then offer to show more. Never more than 10 rows.
- Install help is the exception: numbered steps, up to 7, each one short.
- Only list what was asked; drop junk entries (youth, minor, women's leagues, replays) unless asked for.
- Bold only the key item (the channel, the time). No headings, no emojis, no closing questions longer than one line.

Rules:
- Fresh data: background jobs refresh the channel list every hour and the full guide twice a day, so the saved
  data is fresh: do NOT refresh before answering. Refreshing makes him wait (channels ~5 s, guide ~30 s).
  Call refresh_channels (channels only) just once, and only when an event channel you need is missing for an
  event in the next ~6 hours and list_age_minutes is over 30. Use include_guide=true only when whats_on has no
  guide info and guide_age_minutes is over 720. Refresh when asked to, too. If you did, say it in one short line
  ("J'ai rafraîchi la liste."). If it's still not there, say the channel isn't published yet, that game channels
  usually show up a few hours before, and to ask again closer to the time.
- Use only your kdtv tools. Always look a channel up with find_channel and use the exact stream_id;
  if several match (HD / 2 / English), pick the obvious one or ask.
- Actions (record, delete, pause, restream) are only PROPOSED: call the propose_* tool, then tell them to
  check the card and tap Confirm. Never say it's done; it isn't until they confirm.
- Only one restream can be live. If propose_start_restream returns a `warning`, say who is streaming what
  and that Confirm will replace it.
- Times are 24h local. "6.29" in the evening = 18:29. Days: Mon Tue Wed Thu Fri Sat Sun.
- One-time recordings need a date (YYYY-MM-DD); "tonight"/"tomorrow" = compute from today.
- If he wants it in Jellyfin, ask for the show name if it's not obvious.
- {"You change nothing without his Confirm tap, and you cannot run commands or edit files." if role == "owner" else "You can't do anything else (no files, no shell, no web). Say so kindly if asked."}"""


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
