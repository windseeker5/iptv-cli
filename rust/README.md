# KDTV CLI (`kdtv`)

One program for live TV, movies, series, YouTube, recordings, restreams and
downloads. `kdtv` is the brain and the screen; the proven Python recorder
(`scripts/record_scheduled.py`) still does the recording itself.

## Run it

```bash
cd rust && cargo build --release          # the program: target/release/kdtv
cp kdtv.example.toml ~/.config/kdtv/kdtv.toml   # then set cli_dir
target/release/kdtv                       # the full-screen interface
target/release/kdtv --help                # every command; add --json for scripts/JP
```

## The screen

A menu on the left, the list in the middle, details and action buttons on the
right, and a status bar (REC, RESTREAMING, downloads) at the bottom. `?` shows
every key; the bottom-right corner always shows the keys that work right now.

| # | Section | What | Keys |
| --- | --- | --- | --- |
| 1 | Search | live, movies, series and YouTube in one box | type · ↵ open |
| 2 | Live | favorites, what's on, time left; game channels by their game | ↵ watch · r record · n next · s restream · x stop restream |
| 3 | Movies | categories or search | ↵ watch · d download |
| 4 | Series | categories, seasons, episodes | ↵ watch · [ ] season · d / s / D download episode, season, all |
| 5 | YouTube | search, watch, download | ↵ watch · d video · a audio |
| 6 | Recordings | now, schedules, recent | x stop · space pause · d delete |
| 7 | Downloads | the queue, one at a time | x cancel |
| 8 | Library | every file on disk | ↵ play |
| 9 | Account | provider login, subscription, lists | u update list · g update guide · p password · e edit |
| 0 | Status | services, folders, today's log | |

Your provider allows **one connection**. Playing, recording or restreaming while
something else holds it asks first; a schedule that overlaps another is refused;
"record now" ends before the next scheduled recording; provider downloads pause
by themselves while a recording or restream runs, then resume.

No Nerd Font on a terminal (a Pi console)? Set `icons = false` in `kdtv.toml`.

## How the code is laid out

| File | Job |
| --- | --- |
| `main.rs` | the commands (`kdtv record`, `kdtv schedule add`, `kdtv youtube`, …) |
| `config.rs` | reads `kdtv.toml` and the CLI's `.env` |
| `db.rs` | the channel/movie/series database; game-channel names |
| `record.rs`, `schedules.rs` | record now, stop, schedules (same files and timers as the Python side) |
| `restream.rs` | restream unit `kd-restream` (FFmpeg → NGINX-RTMP) |
| `downloads.rs` | download queue + worker unit `kd-downloads` (curl, yt-dlp) |
| `youtube.rs`, `provider.rs`, `refresh.rs` | YouTube search, series episodes, "update the list now" |
| `library.rs`, `runs.rs`, `status.rs`, `favorites.rs` | reading what's on disk |
| `systemd.rs`, `play.rs`, `log.rs` | helpers: units, mpv, the JSON event log |
| `tui/` | the screen: one file per section, `modal.rs` for pop-ups, `theme.rs` for the look |

Everything is logged as one JSON line in `data/logs/kd/kd-YYYY-MM-DD.jsonl`, with
the provider login removed.
