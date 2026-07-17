// Phase 1 Step 5 — orb + workspace window architecture (D3, D9).
// Spec: phase-1-plan.md "Step 5 — Window architecture: orb + workspace",
// ui_ux/01-companion-orb.md, ui_ux/02-workspace.md.
//
// Owns: show/hide toggling of the workspace anchored to the orb's screen
// position, the global hotkey (with a fallback combo), the off-screen clamp
// for restored window positions, and the tray icon + shared orb context menu.

use std::sync::Mutex;
use serde::Serialize;
use tauri::menu::{ContextMenu, Menu, MenuBuilder, MenuEvent};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, WindowEvent, Wry};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutEvent, ShortcutState};

const PRIMARY_HOTKEY: &str = "alt+space";
const FALLBACK_HOTKEY: &str = "ctrl+alt+space";

/// Which hotkey ended up registered (primary or fallback) — read by the
/// `active_hotkey` command so a later step's status strip can show it
/// (edge case: primary already taken by another app).
pub struct ActiveHotkey(pub Mutex<&'static str>);

#[derive(Clone, Serialize)]
struct WorkspaceAnchor {
    x: f64,
    y: f64,
}

#[tauri::command]
pub fn active_hotkey(state: tauri::State<ActiveHotkey>) -> String {
    (*state.0.lock().unwrap()).to_string()
}

/// Show+focus the workspace if hidden, hide it if visible. `orb_x`/`orb_y`
/// (screen coords) become the CSS transform-origin the frontend anchors its
/// scale+fade to (D3 spatial continuity) — sent as a `workspace-anchor` event
/// so it arrives before the window paints.
#[tauri::command]
pub fn toggle_workspace(app: AppHandle, orb_x: f64, orb_y: f64) -> Result<(), String> {
    toggle_workspace_impl(&app, orb_x, orb_y)
}

/// Pop the shared orb/tray context menu at the cursor — bound to the orb
/// webview's contextmenu (right-click) event.
#[tauri::command]
pub fn show_orb_menu(app: AppHandle) -> Result<(), String> {
    let orb = app.get_webview_window("orb").ok_or("no orb window")?;
    let menu = app.state::<Menu<Wry>>();
    menu.popup(orb.as_ref().window()).map_err(|e| e.to_string())
}

fn toggle_workspace_impl(app: &AppHandle, orb_x: f64, orb_y: f64) -> Result<(), String> {
    let workspace = app.get_webview_window("main").ok_or("no main window")?;
    let visible = workspace.is_visible().map_err(|e| e.to_string())?;
    if visible {
        workspace.hide().map_err(|e| e.to_string())?;
    } else {
        let _ = app.emit_to("main", "workspace-anchor", WorkspaceAnchor { x: orb_x, y: orb_y });
        workspace.show().map_err(|e| e.to_string())?;
        workspace.set_focus().map_err(|e| e.to_string())?; // D3: focus goes to the workspace, never the orb
    }
    Ok(())
}

/// Show (never hide) the workspace — the away-approval toast's click target
/// (Step 10). Toggling would risk hiding an already-open workspace; the card
/// is a bottom-center overlay so "deep-jump to the card" is just "be visible".
#[tauri::command]
pub fn show_workspace(app: AppHandle) {
    open_workspace(&app);
}

/// Always shows (never hides) the workspace, anchored at the orb's current
/// position — used by the "Open workspace" menu item, where toggling would
/// surprise-close an already-open workspace.
fn open_workspace(app: &AppHandle) {
    let Some(workspace) = app.get_webview_window("main") else { return };
    if let Some((x, y)) = orb_position(app) {
        let _ = app.emit_to("main", "workspace-anchor", WorkspaceAnchor { x, y });
    }
    let _ = workspace.show();
    let _ = workspace.set_focus();
}

fn orb_position(app: &AppHandle) -> Option<(f64, f64)> {
    let orb = app.get_webview_window("orb")?;
    let pos = orb.outer_position().ok()?;
    Some((pos.x as f64, pos.y as f64))
}

/// Registers the global hotkey, falling back to a secondary combo if the
/// primary is already taken by another app. Returns the combo that won.
fn register_hotkey(app: &AppHandle) -> &'static str {
    fn on_press(app: &AppHandle, _shortcut: &tauri_plugin_global_shortcut::Shortcut, event: ShortcutEvent) {
        if event.state != ShortcutState::Pressed {
            return;
        }
        let (x, y) = orb_position(app).unwrap_or((0.0, 0.0));
        let _ = toggle_workspace_impl(app, x, y);
    }

    if app.global_shortcut().on_shortcut(PRIMARY_HOTKEY, on_press).is_ok() {
        return PRIMARY_HOTKEY;
    }
    // ponytail: one fallback combo, not a search over N candidates — matches
    // the plan's stated edge case exactly, nothing more speculative.
    match app.global_shortcut().on_shortcut(FALLBACK_HOTKEY, on_press) {
        Ok(()) => FALLBACK_HOTKEY,
        Err(e) => {
            eprintln!("halo: failed to register both hotkeys: {e}");
            "none"
        }
    }
}

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    MenuBuilder::new(app)
        .text("open_workspace", "Open workspace")
        .text("mute_mic", "Mute mic")
        .text("pause_tasks", "Pause all tasks")
        .separator()
        .text("quit", "Quit")
        .build()
}

fn handle_menu_event(app: &AppHandle, event: MenuEvent) {
    match event.id().as_ref() {
        "open_workspace" => open_workspace(app),
        // ponytail: mic mute / pause-all wiring needs the UI's live WS
        // connection, which ui/src/ipc owns and this step doesn't touch.
        // Steps 11 (tasks) / 14 (voice) send the real `task_op`/`mic` frames.
        "mute_mic" | "pause_tasks" => {}
        "quit" => app.exit(0),
        _ => {}
    }
}

/// If a restored window position (window-state plugin) lies outside every
/// current monitor — e.g. the monitor it was on got unplugged — clamp it to
/// the nearest visible monitor's edge instead of leaving it unreachable.
fn clamp_offscreen(app: &AppHandle, label: &str) {
    let Some(window) = app.get_webview_window(label) else { return };
    let (Ok(pos), Ok(size)) = (window.outer_position(), window.outer_size()) else { return };
    let Ok(monitors) = window.available_monitors() else { return };
    if monitors.is_empty() {
        return;
    }

    let on_screen = monitors.iter().any(|m| {
        let mp = m.position();
        let ms = m.size();
        pos.x + size.width as i32 > mp.x
            && pos.x < mp.x + ms.width as i32
            && pos.y + size.height as i32 > mp.y
            && pos.y < mp.y + ms.height as i32
    });
    if on_screen {
        return;
    }

    let nearest = monitors
        .iter()
        .min_by_key(|m| {
            let mp = m.position();
            let ms = m.size();
            let cx = mp.x + ms.width as i32 / 2;
            let cy = mp.y + ms.height as i32 / 2;
            let dx = (pos.x - cx) as i64;
            let dy = (pos.y - cy) as i64;
            dx * dx + dy * dy
        })
        .expect("checked non-empty above");

    let mp = nearest.position();
    let ms = nearest.size();
    let clamped_x = pos.x.clamp(mp.x, mp.x + ms.width as i32 - size.width as i32);
    let clamped_y = pos.y.clamp(mp.y, mp.y + ms.height as i32 - size.height as i32);
    let _ = window.set_position(PhysicalPosition::new(clamped_x, clamped_y));
}

/// Wires everything this module owns: off-screen clamp, hide-not-close on
/// the workspace, the global hotkey, and the tray icon + shared context menu.
/// Called once from `lib.rs`'s `setup`.
pub fn setup(app: &AppHandle) -> tauri::Result<()> {
    clamp_offscreen(app, "orb");
    clamp_offscreen(app, "main");

    // Closing the workspace (native X) must never quit the app or kill
    // sidecars — same as Esc, it just hides (Phase-0 gotcha: only the tray
    // menu's "Quit" may trigger the real ExitRequested/kill_all path).
    if let Some(workspace) = app.get_webview_window("main") {
        let ws = workspace.clone();
        workspace.on_window_event(move |event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = ws.hide();
            }
        });
    }

    let active = register_hotkey(app);
    app.manage(ActiveHotkey(Mutex::new(active)));

    let menu = build_menu(app)?;
    app.manage(menu.clone());

    let mut tray = TrayIconBuilder::new().menu(&menu).show_menu_on_left_click(true).on_menu_event(handle_menu_event);
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    // TrayIcon is ref-counted and removes itself from the tray when the last
    // instance drops — `app.manage` keeps one alive for the app's lifetime.
    app.manage(tray.build(app)?);

    Ok(())
}

/// Unregisters the global hotkey — called on app exit alongside sidecar
/// teardown so a killed/restarted app doesn't leave a dangling OS-level
/// hotkey registration.
pub fn teardown(app: &AppHandle) {
    let _ = app.global_shortcut().unregister_all();
}
