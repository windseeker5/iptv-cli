//! JP, the chat helper on the OMT site: its tools (`tools.rs`, an MCP server
//! Claude Code starts per turn), the proposals it makes and runs on a Confirm tap
//! (`actions.rs`), its read-only eyes for the owner (`eyes.rs`), and the bridge
//! the site talks to (`runner.rs`).

pub mod actions;
mod card;
pub mod eyes;
mod prompt;
pub mod runner;
pub mod tools;

/// `bytes` random bytes as hex, from /dev/urandom.
pub fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
        let _ = std::io::Read::read_exact(&mut f, &mut buf);
    }
    buf.iter().map(|b| format!("{b:02x}")).collect()
}
