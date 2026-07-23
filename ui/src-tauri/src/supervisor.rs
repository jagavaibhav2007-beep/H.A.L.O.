// Phase 0 Step 6 — Tauri sidecar spawn & supervision with backoff.
// Spec: systemdesign/11-ipc-contract.md "Process lifecycle" / phase-0-plan.md Step 6.

use serde::Serialize;
use std::path::PathBuf;
use std::process::{Child, Command};
#[cfg(windows)]
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};
#[cfg(windows)]
use windows::core::PCWSTR;
#[cfg(windows)]
use windows::Win32::Foundation::HANDLE;
#[cfg(windows)]
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

/// Shared handle to a sidecar's `Child` so the shutdown path (main thread) and
/// the supervision loop (background thread) can both see/kill the same process.
type Shared = Arc<Mutex<Option<Child>>>;

#[derive(Clone, Serialize)]
struct SidecarState {
    process: &'static str,
    state: &'static str,
    revision: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct SidecarSnapshot {
    pub revision: u64,
    pub brain: &'static str,
    pub voice: &'static str,
}

struct SidecarStatuses {
    current: Mutex<SidecarSnapshot>,
}

impl SidecarStatuses {
    fn new() -> Self {
        Self {
            current: Mutex::new(SidecarSnapshot {
                revision: 0,
                brain: "unknown",
                voice: "unknown",
            }),
        }
    }

    fn record(&self, process: &'static str, state: &'static str) -> SidecarState {
        let mut current = self.current.lock().unwrap();
        current.revision += 1;
        match process {
            "brain" => current.brain = state,
            "voice" => current.voice = state,
            _ => unreachable!("only known sidecars may publish state"),
        }
        SidecarState {
            process,
            state,
            revision: current.revision,
        }
    }

    fn snapshot(&self) -> SidecarSnapshot {
        *self.current.lock().unwrap()
    }
}

#[cfg(windows)]
struct ProcessJob {
    handle: OwnedHandle,
}

#[cfg(windows)]
impl ProcessJob {
    fn new() -> Result<Self, String> {
        let raw = unsafe { CreateJobObjectW(None, PCWSTR::null()) }
            .map_err(|error| format!("failed to create sidecar job object: {error}"))?;
        let handle = unsafe { OwnedHandle::from_raw_handle(raw.0) };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        unsafe {
            SetInformationJobObject(
                HANDLE(handle.as_raw_handle()),
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const std::ffi::c_void,
                std::mem::size_of_val(&limits) as u32,
            )
        }
        .map_err(|error| format!("failed to configure sidecar job object: {error}"))?;
        Ok(Self { handle })
    }

    fn assign(&self, child: &Child) -> Result<(), String> {
        unsafe {
            AssignProcessToJobObject(
                HANDLE(self.handle.as_raw_handle()),
                HANDLE(child.as_raw_handle()),
            )
        }
        .map_err(|error| format!("failed to assign sidecar to job object: {error}"))
    }
}

#[cfg(not(windows))]
struct ProcessJob;

#[cfg(not(windows))]
impl ProcessJob {
    fn new() -> Result<Self, String> {
        Ok(Self)
    }

    fn assign(&self, _child: &Child) -> Result<(), String> {
        Ok(())
    }
}

fn emit_state(
    app: &AppHandle,
    statuses: &SidecarStatuses,
    process: &'static str,
    state: &'static str,
) {
    let event = statuses.record(process, state);
    // ponytail: emit() failures (no listeners yet) are not actionable here, drop them.
    // The managed snapshot retains the same revision for late/reloaded listeners.
    let _ = app.emit("sidecar-state", event);
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

fn python_cmd(module: &str) -> Command {
    let mut cmd = Command::new("python");
    cmd.args(["-m", module]).current_dir(repo_root().join(module));
    cmd
}

fn brain_cmd() -> Command {
    let mut cmd = python_cmd("brain");
    // ponytail: HALO_MOCK=1 (set by dev.ps1 -Mock) runs the supervised Brain
    // as the scripted mock scenario player. An env var, not a Cargo feature,
    // because it's a dev-time toggle, not a compile-time one.
    if matches!(std::env::var("HALO_MOCK").as_deref(), Ok("1")) {
        cmd.arg("--mock");
    }
    cmd
}

fn voice_cmd() -> Command {
    python_cmd("voice")
}

/// One rung of the ladder: sleeps and returns true to retry, or emits the
/// persistent "error" state and returns false once the ladder is exhausted.
/// (Control flow only — `backoff_delay` stays a separate pure fn for its test.)
fn backoff_or_error(
    app: &AppHandle,
    statuses: &SidecarStatuses,
    name: &'static str,
    attempt: &mut u32,
) -> bool {
    match backoff_delay(*attempt) {
        Some(d) => {
            *attempt += 1;
            emit_state(app, statuses, name, "restarting");
            thread::sleep(d);
            true
        }
        None => {
            emit_state(app, statuses, name, "error");
            false
        }
    }
}

fn publish_child(shared: &Shared, shutdown: &AtomicBool, mut child: Child) -> bool {
    let mut guard = shared.lock().unwrap();
    if shutdown.load(Ordering::SeqCst) {
        drop(guard);
        let _ = child.kill();
        let _ = child.wait();
        return false;
    }
    *guard = Some(child);
    true
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
    statuses: Arc<SidecarStatuses>,
    process_job: Arc<ProcessJob>,
) {
    thread::spawn(move || {
        let mut attempt: u32 = 0;
        loop {
            if shutdown.load(Ordering::SeqCst) {
                return;
            }
            emit_state(&app, &statuses, name, "starting");
            let mut child = match mk_cmd().spawn() {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("halo: failed to spawn {name}: {e}");
                    if backoff_or_error(&app, &statuses, name, &mut attempt) {
                        continue;
                    }
                    return;
                }
            };
            if let Err(error) = process_job.assign(&child) {
                eprintln!("halo: {error}");
                match child.kill() {
                    Ok(()) => {
                        let _ = child.wait();
                        if backoff_or_error(&app, &statuses, name, &mut attempt) {
                            continue;
                        }
                    }
                    Err(kill_error) => {
                        eprintln!("halo: failed to terminate unowned {name}: {kill_error}");
                        *shared.lock().unwrap() = Some(child);
                        emit_state(&app, &statuses, name, "error");
                    }
                }
                return;
            }
            if !publish_child(&shared, &shutdown, child) {
                return;
            }
            emit_state(&app, &statuses, name, "running");
            let start = Instant::now();
            let mut wait_errors = 0;

            // Poll (not a blocking wait()) so we can also notice the shutdown
            // flag without racing the app-exit kill path over the same Child.
            loop {
                if shutdown.load(Ordering::SeqCst) {
                    return; // app exit handler owns killing it now
                }
                let mut guard = shared.lock().unwrap();
                let mut killed = false;
                let done = match guard.as_mut() {
                    Some(c) => match c.try_wait() {
                        Ok(Some(_)) => true,
                        Ok(None) => {
                            wait_errors = 0;
                            false
                        }
                        Err(error) => {
                            wait_errors += 1;
                            eprintln!("halo: failed to poll {name} (attempt {wait_errors}/3): {error}");
                            if wait_errors < 3 {
                                false
                            } else {
                                match c.kill() {
                                    Ok(()) => {
                                        killed = true;
                                        true
                                    }
                                    Err(kill_error) => {
                                        eprintln!("halo: failed to terminate {name}: {kill_error}");
                                        wait_errors = 0;
                                        false
                                    }
                                }
                            }
                        }
                    },
                    None => return, // taken by the shutdown kill path
                };
                drop(guard);
                if done {
                    let child = shared.lock().unwrap().take();
                    if killed {
                        if let Some(mut child) = child {
                            let _ = child.wait();
                        }
                    }
                    break;
                }
                thread::sleep(Duration::from_millis(200));
            }

            if start.elapsed() > HEALTHY_UPTIME {
                attempt = 0;
            }
            if !backoff_or_error(&app, &statuses, name, &mut attempt) {
                return;
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
    statuses: Arc<SidecarStatuses>,
    process_job: Arc<ProcessJob>,
}

impl Sidecars {
    pub fn new() -> Result<Self, String> {
        Ok(Self {
            shutdown: Arc::new(AtomicBool::new(false)),
            brain: Arc::new(Mutex::new(None)),
            voice: Arc::new(Mutex::new(None)),
            statuses: Arc::new(SidecarStatuses::new()),
            process_job: Arc::new(ProcessJob::new()?),
        })
    }

    pub fn start(&self, app: AppHandle) {
        supervise(
            app.clone(),
            "brain",
            brain_cmd,
            self.brain.clone(),
            self.shutdown.clone(),
            self.statuses.clone(),
            self.process_job.clone(),
        );
        supervise(
            app,
            "voice",
            voice_cmd,
            self.voice.clone(),
            self.shutdown.clone(),
            self.statuses.clone(),
            self.process_job.clone(),
        );
    }

    pub fn snapshot(&self) -> SidecarSnapshot {
        self.statuses.snapshot()
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

    #[test]
    fn child_spawned_during_shutdown_is_reaped_not_published() {
        let shared: Shared = Arc::new(Mutex::new(None));
        let shutdown = AtomicBool::new(true);
        let child = Command::new(std::env::current_exe().unwrap())
            .arg("--list")
            .spawn()
            .unwrap();

        assert!(!publish_child(&shared, &shutdown, child));
        assert!(shared.lock().unwrap().is_none());
    }

    #[test]
    fn sidecar_snapshot_retains_latest_states_and_revision() {
        let statuses = SidecarStatuses::new();
        assert_eq!(statuses.snapshot().revision, 0);
        assert_eq!(statuses.snapshot().brain, "unknown");
        assert_eq!(statuses.snapshot().voice, "unknown");

        let first = statuses.record("brain", "starting");
        let second = statuses.record("voice", "running");
        let snapshot = statuses.snapshot();

        assert_eq!(first.revision, 1);
        assert_eq!(second.revision, 2);
        assert_eq!(snapshot.revision, 2);
        assert_eq!(snapshot.brain, "starting");
        assert_eq!(snapshot.voice, "running");
    }

    #[cfg(windows)]
    #[test]
    fn job_object_kills_an_assigned_child_when_dropped() {
        if std::env::var_os("HALO_JOB_OBJECT_TEST_CHILD").is_some() {
            thread::sleep(Duration::from_secs(30));
            return;
        }

        let job = ProcessJob::new().expect("create kill-on-close job");
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "supervisor::tests::job_object_kills_an_assigned_child_when_dropped",
                "--nocapture",
            ])
            .env("HALO_JOB_OBJECT_TEST_CHILD", "1")
            .spawn()
            .expect("spawn test child");
        job.assign(&child).expect("assign test child to job");

        thread::sleep(Duration::from_millis(100));
        assert!(child.try_wait().unwrap().is_none(), "fixture child exited before the job was dropped");
        drop(job);

        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if child.try_wait().unwrap().is_some() {
                return;
            }
            thread::sleep(Duration::from_millis(25));
        }
        let _ = child.kill();
        let _ = child.wait();
        panic!("assigned child survived after the final job handle closed");
    }
}
