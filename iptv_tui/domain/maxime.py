"""Maxime's hockey games: the UMass Minutemen schedule, and where we can watch each game.

Maxim Massé (#7, Rimouski) plays for the University of Massachusetts Amherst
(NCAA Division I, Hockey East). His father Luc sent the team facts and the
2026-27 schedule (PDF); the schedule is refreshed every morning from Hockey
East's site by scripts/refresh_maxime.py so times, TV networks and results stay
current. JP's maxime_games tool reads that file plus our own channel list and
guide, so "when is Maxime's next game, and do we have it?" is one fast look-up
with no download.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db

SCHEDULE_URL = "https://hockeyeastonline.com/men/massachusetts/2026-27-schedule.php"
WATCH_URL = "https://hockeyeastonline.com/watch/index.php"
SCHEDULE_FILE = db.data_dir() / "maxime" / "schedule.json"

ZONES = {"ET": "America/New_York", "CT": "America/Chicago", "MT": "America/Denver", "PT": "America/Los_Angeles"}

# The PDF Luc sent (Hockey East, printed 2026-09-22): used until the first daily refresh, or if the site moves.
# (date, "at" / "vs." / "", opponent as printed, time as printed, footnote)
SEED = [
    ("2026-10-02", "at", "Colorado College (nc)", "7:00 MT", ""), ("2026-10-03", "at", "Colorado College (nc)", "6:00 MT", ""),
    ("2026-10-09", "", "RIT (nc)", "7:00 ET", ""), ("2026-10-10", "", "RIT (nc)", "7:00 ET", ""),
    ("2026-10-16", "", "Northeastern", "", ""), ("2026-10-17", "", "Northeastern (nc)", "", ""),
    ("2026-10-24", "", "Stonehill (nc)", "7:00 ET", ""), ("2026-10-29", "at", "Harvard (nc)", "7:00 ET", ""),
    ("2026-10-30", "", "Harvard (nc)", "7:00 ET", ""), ("2026-11-06", "at", "Maine", "7:00 ET", ""),
    ("2026-11-07", "at", "Maine", "7:00 ET", ""), ("2026-11-13", "", "Boston College", "", ""),
    ("2026-11-14", "at", "Boston College", "7:00 ET", ""), ("2026-11-19", "", "Providence", "7:00 ET", ""),
    ("2026-11-21", "at", "Providence", "", ""), ("2026-12-04", "", "Northeastern", "", ""),
    ("2026-12-05", "", "Northeastern", "", ""), ("2026-12-11", "at", "Merrimack", "7:00 ET", ""),
    ("2026-12-12", "", "Merrimack", "", ""),
    ("2026-12-29", "vs.", "Arizona State (nc)", "4:00 CT", "Holiday Face-Off – Fiserv Forum, Milwaukee, Wis."),
    ("2026-12-30", "vs.", "TBD (nc)", "", "Holiday Face-Off – Fiserv Forum, Milwaukee, Wis."),
    ("2027-01-08", "at", "Boston University", "7:00 ET", ""), ("2027-01-09", "", "Boston University", "", ""),
    ("2027-01-22", "", "Vermont", "", ""), ("2027-01-23", "", "Vermont", "", ""),
    ("2027-02-02", "", "UMass Lowell", "", ""), ("2027-02-06", "at", "New Hampshire", "7:00 ET", ""),
    ("2027-02-12", "", "New Hampshire", "", ""), ("2027-02-13", "", "New Hampshire", "", ""),
    ("2027-02-19", "", "UMass Lowell", "", ""), ("2027-02-20", "at", "UMass Lowell", "6:05 ET", ""),
    ("2027-02-25", "", "UConn", "", ""), ("2027-02-27", "at", "UConn", "2:00 ET", ""),
    ("2027-03-05", "at", "Boston College", "7:00 ET", ""),
]

# Other ways teams are written in channel names and guide titles.
ALIASES = {
    "Boston University": ["Boston University", "Boston U", "BU"],
    "Boston College": ["Boston College", "BC"],
    "Arizona State": ["Arizona State", "Arizona St"],
    "UConn": ["UConn", "Connecticut"],
    "New Hampshire": ["New Hampshire", "UNH"],
    "RIT": ["RIT", "Rochester"],
    "Colorado College": ["Colorado College", "Colorado Coll"],
}
# "UMass" also means these other teams; and UMass plays other sports.
NOT_OUR_UMASS = r"(?!\s*[-_ ]?\s*(lowell|boston|dartmouth))"
OTHER_SPORTS = re.compile(r"football|field hockey|women|volleyball|soccer|basketball|lacrosse|\bfc:", re.I)


def _game(d: str, where: str, opp: str, tm: str, note: str, tv: str = "", result: str = "") -> dict:
    nc = "(nc)" in opp
    return {"date": d, "where": {"at": "away", "vs.": "neutral"}.get(where, "home"),
            "opponent": opp.replace("(nc)", "").strip(), "non_conference": nc,
            "time": tm, "tv": tv, "result": result, "note": note}


# ── daily refresh (scripts/refresh_maxime.py) ─────────────────────────────

def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def parse_schedule(page: str) -> list[dict]:
    """Rows of Hockey East's team schedule table: month headers, then one row per game."""
    notes = {n: _cell(t) for n, t in re.findall(r"<b>(\d+)</b>(.*?)<br", page)}
    games, year, month = [], None, None
    for row in re.findall(r"<tr\b.*?</tr>", page, re.S):
        head = re.search(r'stats-section"><td[^>]*>\s*(\w+) (\d{4})', row)
        if head:
            month, year = datetime.strptime(head.group(1), "%B").month, int(head.group(2))
            continue
        cells = [_cell(c) for c in re.split(r"<td\b[^>]*>", row)[1:]]
        if not year or len(cells) < 10 or not re.match(r"\d\d? \w{3}$", cells[0]):
            continue
        day = int(cells[0].split()[0])
        # cells: date, footnote, W/L/T, our goals, "- their goals", ot, at/vs., opponent, TV, time or box score
        result = f"{cells[2]} {cells[3]}{cells[4].replace(' ', '')}{' ' + cells[5] if cells[5] else ''}" \
            if cells[2] else ""
        games.append(_game(date(year, month, day).isoformat(), cells[6], cells[7], _time(row),
                           notes.get(cells[1], ""), tv=_tv(row), result=result))
    return games


def _tv(row: str) -> str:
    m = re.search(r'class="left tvlist">(.*?)</td>', row, re.S)
    return _cell(m.group(1)) if m else ""


def _time(row: str) -> str:
    m = re.search(r"(\d{1,2}:\d\d)\s*(ET|CT|MT|PT)", html.unescape(row).replace("\xa0", " "))
    return f"{m.group(1)} {m.group(2)}" if m else ""


def refresh() -> dict:
    """Download the official schedule and keep it if it looks sane (else keep the last good copy)."""
    import requests
    r = requests.get(SCHEDULE_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0 (KD CLI; JP)"})
    r.raise_for_status()
    games = parse_schedule(r.text)
    if len(games) < 20:
        return {"ok": False, "error": f"only {len(games)} games parsed; kept the previous schedule"}
    record = re.search(r"([\d-]+ Overall \([^)]*\))", r.text)
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fetched_at": int(time.time()), "source": SCHEDULE_URL,
                               "record": record.group(1) if record else "", "games": games}, ensure_ascii=False, indent=1))
    tmp.replace(SCHEDULE_FILE)
    return {"ok": True, "games": len(games), "record": record.group(1) if record else ""}


# ── JP's look-up ──────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        data = json.loads(SCHEDULE_FILE.read_text())
        if len(data.get("games", [])) >= 20:
            return data
    except (OSError, ValueError):
        pass
    return {"fetched_at": None, "source": "the PDF Luc sent (2026-09-22)", "record": "",
            "games": [_game(*g) for g in SEED]}


def _start(game: dict) -> datetime | None:
    """Puck drop as a local (this machine's clock) datetime, or None when no time is published yet."""
    m = re.match(r"(\d{1,2}):(\d\d) (\w\w)", game.get("time") or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    h = h if h == 12 else h + 12        # printed without AM/PM; college puck drops are afternoon/evening
    d = date.fromisoformat(game["date"])
    when = datetime(d.year, d.month, d.day, h, mi, tzinfo=ZoneInfo(ZONES.get(m.group(3), ZONES["ET"])))
    return when.astimezone().replace(tzinfo=None)


def _opp_pattern(opponent: str) -> re.Pattern | None:
    if opponent.upper().startswith("TBD"):
        return None
    names = ALIASES.get(opponent, [opponent])
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)


def _is_our_game(text: str, opponent: str) -> bool:
    if OTHER_SPORTS.search(text):
        return False
    opp = _opp_pattern(opponent)
    if opp:
        if not opp.search(text):
            return False
        text = opp.sub(" ", text)       # "UMass Lowell vs. UMass": drop the opponent, our team must still be there
    return bool(re.search(r"\bmassachusetts\b|\bumass\b" + NOT_OUR_UMASS, text, re.I))


_DATE_PATTERNS = [
    (re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b"), lambda m, y: date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* (\d{1,2})\b", re.I),
     lambda m, y: datetime.strptime(f"{y} {m[1][:3].title()} {m[2]}", "%Y %b %d").date()),
    (re.compile(r"\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", re.I),
     lambda m, y: datetime.strptime(f"{y} {m[2].title()} {m[1]}", "%Y %b %d").date()),
]


def _date_in(text: str, year: int) -> date | None:
    for pat, make in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return make(m, year)
            except ValueError:
                pass
    return None


def _channels_for(games: list[dict]) -> dict[str, list[dict]]:
    """Our channels (event channel names and guide titles) carrying each of these games, keyed by date."""
    found: dict[str, list[dict]] = {g["date"]: [] for g in games}
    if not games:
        return found
    first = min(date.fromisoformat(g["date"]) for g in games)
    since = int(datetime(first.year, first.month, first.day).timestamp()) - 6 * 3600
    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        names = conn.execute(
            "SELECT stream_id, name, category_name FROM live_streams "
            "WHERE name LIKE '%massachusetts%' OR name LIKE '%umass%'").fetchall()
        guide = conn.execute(
            "SELECT e.stream_id, l.name, e.title, e.start_time FROM epg e JOIN live_streams l USING(stream_id) "
            "WHERE e.end_time > ? AND (e.title LIKE '%massachusetts%' OR e.title LIKE '%umass%')", (since,)).fetchall()
    for g in games:
        gday = date.fromisoformat(g["date"])
        same_opponent = [x for x in games if x["opponent"] == g["opponent"]]
        for r in names:
            if not _is_our_game(r["name"], g["opponent"]):
                continue
            d = _date_in(r["name"], gday.year)
            if d == gday or (d is None and g is same_opponent[0]):
                found[g["date"]].append({"stream_id": r["stream_id"], "channel": r["name"],
                                         "category": r["category_name"], "how": "event channel"})
        for r in guide:
            starts = datetime.fromtimestamp(r["start_time"])
            if starts.date() == gday and _is_our_game(r["title"], g["opponent"]):
                found[g["date"]].append({"stream_id": r["stream_id"], "channel": r["name"],
                                         "how": f"guide: {r['title']} at {starts:%H:%M}"})
        found[g["date"]] = found[g["date"]][:6]
    return found


def games(upcoming: int = 3, past: int = 1) -> dict:
    """Next games (with the channels we have for any in the next 3 days) and the latest results."""
    from . import tv_refresh
    data = _load()
    now = datetime.now()
    rows = []
    for g in data["games"]:
        start = _start(g)
        rows.append(g | {"local_start": start.strftime("%a %Y-%m-%d %H:%M") if start else None,
                         "_end": (start + timedelta(hours=3)) if start
                         else datetime.fromisoformat(g["date"]) + timedelta(days=1)})
    done = [g for g in rows if g["result"] or g["_end"] < now]
    nxt = [g for g in rows if not g["result"] and g["_end"] >= now][:max(1, min(upcoming, 10))]
    soon = [g for g in nxt if date.fromisoformat(g["date"]) <= now.date() + timedelta(days=3)]
    chans = _channels_for(soon)
    out_next = []
    for g in nxt:
        item = {k: v for k, v in g.items() if not k.startswith("_") and v not in ("", None)}
        if not g["local_start"]:
            item["local_start"] = f"{date.fromisoformat(g['date']):%a %Y-%m-%d}, time not published yet"
        if g in soon:
            item["our_channels"] = chans[g["date"]] or "none found yet"
        else:
            item["our_channels"] = "not checked (event channels only appear a day or so before the game)"
        out_next.append(item)
    fetched = data.get("fetched_at")
    return {
        "team": "UMass Minutemen (Massachusetts / UMass Amherst), NCAA D1, Hockey East",
        "record": data.get("record") or None,
        "next_games": out_next,
        "latest_results": [{k: v for k, v in g.items() if k in ("date", "where", "opponent", "result", "non_conference")}
                           for g in done[-max(0, min(past, 10)):] if g["result"]] if past else [],
        "schedule_source": data["source"],
        "schedule_age_hours": round((time.time() - fetched) / 3600, 1) if fetched else None,
        "channel_list_age_minutes": tv_refresh.age_minutes("channels"),
        "official_streams": f"Hockey East games: {WATCH_URL} (ESPN+ in the US). The `tv` field is the TV network, "
                            "when the schedule lists one.",
        "times": "local_start is already in local time (Eastern); the `time` field is as printed by Hockey East.",
    }
