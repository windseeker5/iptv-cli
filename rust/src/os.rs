//! The few things Linux and Windows do differently: is a process still alive,
//! stopping one (and what it started), hiding a background process's window,
//! keeping the computer awake, the home folder, random ids.

use std::path::PathBuf;
use std::process::Command;

/// Whether the process with this id is still running.
pub fn alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        // SAFETY: signal 0 only asks whether the process exists; nothing is sent.
        let r = unsafe { libc::kill(pid as libc::pid_t, 0) };
        r == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::{CloseHandle, STILL_ACTIVE};
        use windows_sys::Win32::System::Threading::{GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};
        // SAFETY: a handle we open, read and close; a failed open is a null handle.
        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if handle.is_null() {
                return false;
            }
            let mut code = 0u32;
            let ok = GetExitCodeProcess(handle, &mut code) != 0;
            CloseHandle(handle);
            ok && code == STILL_ACTIVE as u32
        }
    }
}

/// Ask one process to stop (FFmpeg keeps what it wrote).
pub fn terminate(pid: u32) {
    #[cfg(unix)]
    {
        // SAFETY: plain kill(2) on a pid we started.
        unsafe { libc::kill(pid as libc::pid_t, libc::SIGTERM) };
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_TERMINATE, TerminateProcess};
        // SAFETY: a handle we open, use and close; a failed open is a null handle.
        unsafe {
            let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
            if !handle.is_null() {
                TerminateProcess(handle, 1);
                CloseHandle(handle);
            }
        }
    }
}

/// Stop a background job and everything it started (FFmpeg, curl, yt-dlp).
pub fn terminate_tree(pid: u32) {
    #[cfg(unix)]
    {
        // The job leads its own process group (see `detach`): signal the whole group.
        // SAFETY: plain kill(2) on a group we created, then on the job itself.
        unsafe {
            libc::kill(-(pid as libc::pid_t), libc::SIGTERM);
            libc::kill(pid as libc::pid_t, libc::SIGTERM);
        }
    }
    #[cfg(windows)]
    {
        let mut cmd = Command::new("taskkill");
        cmd.args(["/PID", &pid.to_string(), "/T", "/F"]);
        hide_window(&mut cmd);
        let _ = cmd.stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).status();
    }
}

/// A background job: no window on Windows, its own process group on Linux, so
/// closing the terminal doesn't stop it and stopping it reaches its children.
pub fn detach(cmd: &mut Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use windows_sys::Win32::System::Threading::{CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW};
        cmd.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    }
}

/// No console window popping up for a helper (taskkill, ffprobe…) on Windows.
#[cfg_attr(unix, allow(dead_code))]
pub fn hide_window(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    let _ = cmd;
}

/// Keep the computer from going to sleep while this thread asks for it (Windows;
/// Linux leaves that to the desktop). The screen may still turn off.
pub fn keep_awake(on: bool) {
    #[cfg(windows)]
    {
        use windows_sys::Win32::System::Power::{ES_CONTINUOUS, ES_SYSTEM_REQUIRED, SetThreadExecutionState};
        // SAFETY: only sets this thread's power request.
        unsafe { SetThreadExecutionState(if on { ES_CONTINUOUS | ES_SYSTEM_REQUIRED } else { ES_CONTINUOUS }) };
    }
    #[cfg(unix)]
    let _ = on;
}

/// Where output goes to be thrown away (`curl -o`).
pub fn null_device() -> &'static str {
    if cfg!(windows) { "NUL" } else { "/dev/null" }
}

/// The user's home folder: $HOME, or %USERPROFILE% on Windows.
pub fn home() -> Option<PathBuf> {
    std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")).filter(|h| !h.is_empty()).map(PathBuf::from)
}

/// `bytes` random bytes as hex (the standard library's random seed, mixed with a counter).
pub fn random_hex(bytes: usize) -> String {
    use std::hash::{BuildHasher, Hasher};
    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let mut out = String::new();
    while out.len() < bytes * 2 {
        let mut h = std::collections::hash_map::RandomState::new().build_hasher();
        h.write_u64(COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed));
        h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
        out.push_str(&format!("{:016x}", h.finish()));
    }
    out.truncate(bytes * 2);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn random_ids_differ_and_have_the_right_length() {
        let (a, b) = (random_hex(4), random_hex(4));
        assert_eq!(a.len(), 8);
        assert_ne!(a, b);
        assert_eq!(random_hex(13).len(), 26);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn this_process_is_alive() {
        assert!(alive(std::process::id()));
    }

    #[test]
    fn a_stopped_job_and_its_children_end() {
        #[cfg(unix)]
        let mut cmd = {
            let mut c = Command::new("sh");
            c.args(["-c", "sleep 30 & sleep 30"]);
            c
        };
        #[cfg(windows)]
        let mut cmd = {
            let mut c = Command::new("cmd");
            c.args(["/C", "ping -n 30 127.0.0.1 >NUL"]);
            c
        };
        detach(&mut cmd);
        let mut child = cmd.spawn().unwrap();
        let pid = child.id();
        std::thread::sleep(std::time::Duration::from_millis(300));
        assert!(alive(pid));
        terminate_tree(pid);
        let _ = child.wait();
        assert!(!alive(pid));
    }
}
