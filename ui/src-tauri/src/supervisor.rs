// Phase 0 Step 6 — Tauri sidecar spawn & supervision with backoff.
// Spec: systemdesign/11-ipc-contract.md "Process lifecycle" / phase-0-plan.md Step 6.

use serde::Serialize;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

/// Shared handle to a sidecar's `Child` so the shutdown path (main thread) and
/// the supervision loop (background thread) can both see/kill the same process.
type Shared = Arc<Mutex<Option<Child>>>;

#[derive(Clone, Serialize)]
struct SidecarState {
    process: &'static str,
    state: &'static str,
}

fn emit_state(app: &AppHandle, process: &'static str, state: &'static str) {
    // ponytail: emit() failures (no listeners yet) are not actionable here, drop them.
    let _ = app.emit("sidecar-state", SidecarState { process, state });
}

/// 1s / 5s / 30s ladder; `None` means exhausted -> caller surfaces `error`.
fn backoff_delay(attempt: u32) -> Option<Duration> {
    match attempt {
        0 => Some(Duration::from_secs(1)),
        1 => Some(Duration::from_secs(5)),
        2 => Some(Duration::from_secs(30)),
        _ => None,
    }
}

/// A child that stayed up at least this long before dying doesn't count as
/// part of a crash loop — its next restart gets a clean slate at 1s.
const HEALTHY_UPTIME: Duration = Duration::from_secs(10);

// ponytail: dev-mode cwd resolution walks up from the cargo manifest dir
// (ui/src-tauri) to the repo root. The packaged-binary phase (sidecar
// externalBin) replaces this with a resource-dir-relative path.
fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent() // ui/
        .expect("ui/src-tauri has a parent")
        .parent() // repo root
        .expect("ui/ has a parent")
        .to_path_buf()
}

fn brain_cmd() -> Command {
    let mut cmd = Command::new("python");
    cmd.args(["-m", "brain"])
        .current_dir(repo_root().join("brain"));
    cmd
}

fn voice_cmd() -> Command {
    let mut cmd = Command::new("python");
    cmd.args(["-m", "voice"])
        .current_dir(repo_root().join("voice"));
    cmd
}

/// Runs the spawn/watch/backoff loop for one sidecar on a background thread.
/// Exits the loop (no more restarts) once the flag is set or the backoff
/// ladder is exhausted (the latter emits a persistent `"error"` state first).
fn supervise(
    app: AppHandle,
    name: &'static str,
    mk_cmd: fn() -> Command,
    shared: Shared,
    shutdown: Arc<AtomicBool>,
) {
    thread::spawn(move || {
        let mut attempt: u32 = 0;
        loop {
            if shutdown.load(Ordering::SeqCst) {
                return;
            }
            emit_state(&app, name, "starting");
            let child = match mk_cmd().spawn() {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("halo: failed to spawn {name}: {e}");
                    match backoff_delay(attempt) {
                        Some(d) => {
                            attempt += 1;
                            emit_state(&app, name, "restarting");
                            thread::sleep(d);
                            continue;
                        }
                        None => {
                            emit_state(&app, name, "error");
                            return;
                        }
                    }
                }
            };
            *shared.lock().unwrap() = Some(child);
            emit_state(&app, name, "running");
            let start = Instant::now();

            // Poll (not a blocking wait()) so we can also notice the shutdown
            // flag without racing the app-exit kill path over the same Child.
            loop {
                if shutdown.load(Ordering::SeqCst) {
                    return; // app exit handler owns killing it now
                }
                let mut guard = shared.lock().unwrap();
                let done = match guard.as_mut() {
                    Some(c) => matches!(c.try_wait(), Ok(Some(_)) | Err(_)),
                    None => return, // taken by the shutdown kill path
                };
                drop(guard);
                if done {
                    break;
                }
                thread::sleep(Duration::from_millis(200));
            }
            *shared.lock().unwrap() = None;

            if start.elapsed() > HEALTHY_UPTIME {
                attempt = 0;
            }
            match backoff_delay(attempt) {
                Some(d) => {
                    attempt += 1;
                    emit_state(&app, name, "restarting");
                    thread::sleep(d);
                }
                None => {
                    emit_state(&app, name, "error");
                    return;
                }
            }
        }
    });
}

/// Owns the shared shutdown flag + per-sidecar Child handles for the app's
/// lifetime; `setup` starts the supervisors, the `RunEvent::ExitRequested`
/// handler uses `kill_all` to tear them down.
pub struct Sidecars {
    shutdown: Arc<AtomicBool>,
    brain: Shared,
    voice: Shared,
}

impl Sidecars {
    pub fn new() -> Self {
        Self {
            shutdown: Arc::new(AtomicBool::new(false)),
            brain: Arc::new(Mutex::new(None)),
            voice: Arc::new(Mutex::new(None)),
        }
    }

    pub fn start(&self, app: AppHandle) {
        supervise(
            app.clone(),
            "brain",
            brain_cmd,
            self.brain.clone(),
            self.shutdown.clone(),
        );
        supervise(app, "voice", voice_cmd, self.voice.clone(), self.shutdown.clone());
    }

    /// Set the shutdown flag BEFORE killing children — otherwise the
    /// supervision loop reads the intentional kill as a crash and respawns it.
    pub fn kill_all(&self) {
        self.shutdown.store(true, Ordering::SeqCst);
        for shared in [&self.brain, &self.voice] {
            if let Some(mut child) = shared.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_ladder_is_1s_5s_30s_then_exhausted() {
        assert_eq!(backoff_delay(0), Some(Duration::from_secs(1)));
        assert_eq!(backoff_delay(1), Some(Duration::from_secs(5)));
        assert_eq!(backoff_delay(2), Some(Duration::from_secs(30)));
        assert_eq!(backoff_delay(3), None);
        assert_eq!(backoff_delay(100), None);
    }
}
