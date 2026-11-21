//! JP, the chat helper on the OMT site: its tools (`tools.rs`, an MCP server
//! Claude Code starts per turn), the proposals it makes and runs on a Confirm tap
//! (`actions.rs`), its read-only eyes for the owner (`eyes.rs`), and the bridge
//! the site talks to (`runner.rs`).

// On Windows JP's runner (the OMT bridge) isn't built, so parts only it uses sit idle.
#![cfg_attr(not(unix), allow(dead_code))]

pub mod actions;
mod card;
pub mod eyes;
mod prompt;
#[cfg(unix)]
pub mod runner;
pub mod tools;

pub use crate::os::random_hex;
