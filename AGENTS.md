# AGENTS.md

Guidance for coding agents in `/home/kdresdell/Documents/DEV/kdtv/cli` (KDTV CLI,
part of the KDtv folder; siblings are `../app` and `../site`). `CLAUDE.md` has the
architecture and behavior; this file is the short version.

- The application is the Rust program `kdtv` in `rust/`. There is no Python left.
- Build and check: `cd rust && cargo clippy --all-targets -- -D warnings && cargo test`.
- Install: `install -Dm755 rust/target/release/kdtv ~/.local/bin/kdtv`, then
  `systemctl --user restart kdcli-agent-runner`.
- Docker lives in the DEV folder (`../../docker-compose.yml`); run compose there.
- Treat `.env`, `data/`, recordings and the Jellyfin library as user state: never
  delete or overwrite them unless asked.
- Never print provider URLs or logins (they carry credentials).
- Prefer the smallest correct change and follow the surrounding style: short doc
  comments, plain names, no comments that restate the code.
- New env vars or workflows: update `.env.example` and the README.
