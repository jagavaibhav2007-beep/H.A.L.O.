# Launches Halo Phase 0 for local development. Tauri owns and supervises
# Brain and Voice; the standalone options are for worker-only debugging.
# Usage: ./dev.ps1  (or ./dev.ps1 -Only brain|voice|ui to launch just one;
#        Voice requires a separately running Brain and its editable package)
#        ./dev.ps1 -Smoke  runs only the Phase 0/1/2 protocol phase checks.
#        ./dev.ps1 -Verify runs the full automated repository gate: contract
#        sync, Python suites, UI checks/build, Rust tests, and phase checks.
#        ./dev.ps1 -Mock  runs the scripted mock Brain (Phase 1 Step 2). With
#        the default (-Only all) it launches the full app: Tauri spawns the
#        Brain with --mock via the HALO_MOCK env var, so the real UI talks to
#        the mock. With -Only brain it runs a standalone `python -m brain --mock`
#        for raw WS debugging.
#        File watching is disabled by default so workspace-sync timestamp events
#        cannot repeatedly reload the webviews or relaunch the native app. Pass
#        -WatchNative to restore normal Vite and Rust hot reload while editing.

param(
    [ValidateSet("all", "ui", "brain", "voice")]
    [string]$Only = "all",

    [switch]$Smoke,
    [switch]$Verify,
    [switch]$Mock,
    [switch]$WatchNative
)

$root = $PSScriptRoot
. "$PSScriptRoot\_python.ps1"

if ($Smoke -and $Verify) {
    Write-Error "Choose either -Smoke (protocol checks) or -Verify (full automated gate), not both."
    exit 2
}

if (($Smoke -or $Verify) -and ($Mock -or $WatchNative -or $Only -ne "all")) {
    Write-Error "-Smoke and -Verify cannot be combined with -Only, -Mock, or -WatchNative."
    exit 2
}

function Start-Ui {
    $createdDevMutex = $false
    $devMutex = [System.Threading.Mutex]::new($true, "Local\HaloDevLauncher", [ref]$createdDevMutex)
    if (-not $createdDevMutex) {
        $devMutex.Dispose()
        Write-Error "A Halo UI dev session is already running. Stop that terminal before launching another."
        exit 1
    }

    $hadMockEnv = Test-Path Env:HALO_MOCK
    $previousMockEnv = $env:HALO_MOCK
    $tauriArgs = @("run", "tauri", "--", "dev")
    if (-not $WatchNative) {
        $tauriArgs += @("--no-watch", "--config", "src-tauri/tauri.stable.conf.json")
    }

    try {
        if ($Mock) {
            $env:HALO_MOCK = "1"
        } else {
            Remove-Item Env:HALO_MOCK -ErrorAction SilentlyContinue
        }
        Push-Location "$root\ui"
        & npm @tauriArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
        if ($hadMockEnv) {
            $env:HALO_MOCK = $previousMockEnv
        } else {
            Remove-Item Env:HALO_MOCK -ErrorAction SilentlyContinue
        }
        $devMutex.ReleaseMutex()
        $devMutex.Dispose()
    }
}

function Start-Brain {
    $brainCmd = if ($Mock) { "python -m brain --mock" } else { "python -m brain" }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\brain'; $brainCmd"
}

function Start-Voice {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\voice'; python -m voice"
}

if ($Smoke) {
    $python = Resolve-PythonLauncher
    Invoke-Python -Launcher $python -Arguments @("$root\shared\smoke_test.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Invoke-Python -Launcher $python -Arguments @("$root\shared\phase1_check.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Invoke-Python -Launcher $python -Arguments @("$root\shared\phase2_check.py")
    exit $LASTEXITCODE
}

if ($Verify) {
    & "$root\verify.ps1"
    exit $LASTEXITCODE
}

switch ($Only) {
    "ui"    { Start-Ui }
    "brain" { Start-Brain }
    "voice" { Start-Voice }
    "all"   { Start-Ui }
}
