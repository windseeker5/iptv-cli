# kd — KD TV in Rust

One program for live TV, movies, series, recordings, restreams and downloads.
`kd` is the brain and the screen; the proven Python recorder
(`scripts/record_scheduled.py`) still does the recording itself.

## Run it

```bash
cd rust && cargo build --release      # the program: target/release/kd
cp kd.example.toml ~/.config/kd/kd.toml   # then set cli_dir
target/release/kd                     # the TUI
target/release/kd --help              # every command; add --json for scripts/JP
```

## The TUI

| Tab | What | Keys |
| --- | --- | --- |
| 1 Watch | Live (favorites), Movies, Series; search first | `/` search · `←→` live/movies/series · `↵` play/open · `r` record · `s` restream · `d` download · `D` whole series · `esc` back |
| 2 Record | Recording and restream now, schedules, recent recordings | `x` stop · `space` pause/resume · `d` delete |
| 3 Library | Download queue, then every file on disk | `↵` play · `x` cancel download |
| 4 Status | Services, connection, data freshness, folders | `ctrl+r` refresh |

Your provider allows **one connection**. Playing, recording or restreaming while
something else holds it asks first, and downloads pause by themselves while a
recording or restream runs, then resume where they stopped.

## How the code is laid out

| File | Job |
| --- | --- |
| `main.rs` | the commands (`kd record`, `kd schedule add`, …) |
| `config.rs` | reads `kd.toml` and the CLI's `.env` |
| `db.rs` | the channel/movie/series database (read-only, except caching episodes) |
| `record.rs` | record now, stop (runs the Python recorder under systemd) |
| `schedules.rs` | schedules: same JSON + systemd timers as the Python side |
| `restream.rs` | restream unit `kd-restream` (FFmpeg → NGINX-RTMP) |
| `downloads.rs` | download queue + worker unit `kd-downloads` |
| `library.rs`, `runs.rs`, `status.rs`, `favorites.rs` | reading what's on disk |
| `provider.rs` | the provider API (series episodes) |
| `systemd.rs`, `play.rs`, `log.rs` | helpers: units, mpv, the JSON event log |
| `tui/` | the screen: one file per tab, `modal.rs` for pop-ups |

Everything kd does is logged as one JSON line in `data/logs/kd/kd-YYYY-MM-DD.jsonl`,
with the provider login removed.
