mod supervisor;
mod windows;

use serde::{Deserialize, Serialize};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use supervisor::{SidecarSnapshot, Sidecars};

/// %LOCALAPPDATA%\Halo — the same directory the Brain writes session.json to.
/// Created here rather than assumed, because the supervisor logs its first
/// spawn before the Brain has ever run and created it.
pub(crate) fn halo_dir() -> Option<&'static Path> {
    static DIR: OnceLock<Option<PathBuf>> = OnceLock::new();
    DIR.get_or_init(|| {
        let dir = PathBuf::from(std::env::var_os("LOCALAPPDATA")?).join("Halo");
        std::fs::create_dir_all(&dir).ok()?;
        Some(dir)
    })
    .as_deref()
}

/// ponytail: "rotation" is a single truncate past 4 MB — enough that a crash
/// loop can't fill the disk. A real scheme (numbered generations) only pays off
/// once someone needs history older than the current session.
const LOG_MAX_BYTES: u64 = 4 * 1024 * 1024;

/// Appending log file under `dir`, truncated first if it has grown past the cap.
pub(crate) fn open_log(dir: &Path, file: &str) -> std::io::Result<std::fs::File> {
    let path = dir.join(file);
    let too_big = std::fs::metadata(&path).is_ok_and(|m| m.len() > LOG_MAX_BYTES);
    std::fs::OpenOptions::new()
        .create(true)
        .append(!too_big)
        .write(too_big)
        .truncate(too_big)
        .open(path)
}

fn append_line(dir: &Path, file: &str, msg: &str) {
    if let Ok(mut f) = open_log(dir, file) {
        let secs = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_secs());
        let _ = writeln!(f, "{secs} {msg}");
    }
}

/// Native diagnostics. stderr is invisible in a release build (`main.rs` sets
/// `windows_subsystem = "windows"`), which is exactly why every one of these
/// also lands in %LOCALAPPDATA%\Halo\halo.log.
pub(crate) fn log(msg: &str) {
    eprintln!("{msg}");
    if let Some(dir) = halo_dir() {
        append_line(dir, "halo.log", msg);
    }
}

#[derive(Serialize, Deserialize)]
struct Session {
    port: u16,
    token: String,
}

/// Re-reads %LOCALAPPDATA%\Halo\session.json fresh on every call — the UI
/// must never cache port/token, since the Brain can be respawned on a new
/// port at any time (see supervisor.rs backoff loop).
#[tauri::command]
fn read_session() -> Result<Session, String> {
    let dir = std::env::var("LOCALAPPDATA").map_err(|e| e.to_string())?;
    let path = std::path::Path::new(&dir).join("Halo").join("session.json");
    let raw = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    serde_json::from_str(&raw).map_err(|e| e.to_string())
}

#[tauri::command]
fn sidecar_snapshot(sidecars: tauri::State<'_, std::sync::Arc<Sidecars>>) -> SidecarSnapshot {
    sidecars.snapshot()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecars = std::sync::Arc::new(
        Sidecars::new().expect("failed to establish Windows sidecar process ownership"),
    );

    let app = tauri::Builder::default()
        .manage(sidecars.clone())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_window_state::Builder::new()
                // ponytail: POSITION+SIZE only — VISIBLE would fight the
                // orb-visible/workspace-hidden startup state set in
                // tauri.conf.json (see windows.rs's clamp_offscreen for why
                // this plugin alone isn't enough: it skips restore rather
                // than clamping when the saved monitor is gone).
                .with_state_flags(tauri_plugin_window_state::StateFlags::POSITION | tauri_plugin_window_state::StateFlags::SIZE)
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            read_session,
            sidecar_snapshot,
            windows::toggle_workspace,
            windows::active_hotkey,
            windows::hotkey_status,
            windows::show_orb_menu,
            windows::show_workspace
        ])
        .setup({
            let sidecars = sidecars.clone();
            move |app| {
                windows::setup(app.handle())?;
                sidecars.start(app.handle().clone());
                Ok(())
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(move |app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            windows::teardown(app_handle);
            sidecars.kill_all();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Against a temp dir, not LOCALAPPDATA — mutating process env would race
    /// the other tests, which cargo runs on parallel threads.
    #[test]
    fn append_line_appends_and_truncates_past_the_cap() {
        let dir = std::env::temp_dir().join(format!("halo-log-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("t.log");
        let _ = std::fs::remove_file(&path);

        append_line(&dir, "t.log", "first");
        append_line(&dir, "t.log", "second");
        let body = std::fs::read_to_string(&path).unwrap();
        assert_eq!(body.lines().count(), 2, "second line must append, not overwrite");
        assert!(body.contains("first") && body.contains("second"));

        std::fs::write(&path, vec![b'x'; (LOG_MAX_BYTES + 1) as usize]).unwrap();
        append_line(&dir, "t.log", "after-rotate");
        let rotated = std::fs::read_to_string(&path).unwrap();
        assert!(rotated.ends_with("after-rotate\n") && rotated.len() < 64);

        std::fs::remove_dir_all(&dir).unwrap();
    }
}
