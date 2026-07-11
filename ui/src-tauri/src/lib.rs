mod supervisor;

use serde::{Deserialize, Serialize};
use supervisor::Sidecars;

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecars = std::sync::Arc::new(Sidecars::new());

    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![read_session])
        .setup({
            let sidecars = sidecars.clone();
            move |app| {
                sidecars.start(app.handle().clone());
                Ok(())
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(move |_app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            sidecars.kill_all();
        }
    });
}
