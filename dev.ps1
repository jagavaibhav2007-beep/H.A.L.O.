# Launches Halo Phase 0 for local development. Tauri owns and supervises
# Brain and Voice; the standalone options are for worker-only debugging.
# Usage: ./dev.ps1  (or ./dev.ps1 -Only brain|voice|ui to launch just one)
#        ./dev.ps1 -Smoke  runs the Phase 0 and Phase 1 exit-criteria protocol
#        checks in-place (no windows spawned) instead of launching processes.
#        ./dev.ps1 -Mock  runs the scripted mock Brain (Phase 1 Step 2). With
#        the default (-Only all) it launches the full app: Tauri spawns the
#        Brain with --mock via the HALO_MOCK env var, so the real UI talks to
#        the mock. With -Only brain it runs a standalone `python -m brain --mock`
#        for raw WS debugging.

param(
    [ValidateSet("all", "ui", "brain", "voice")]
    [string]$Only = "all",

    [switch]$Smoke,
    [switch]$Mock
)

$root = $PSScriptRoot

function Start-Ui {
    # -Mock: set HALO_MOCK in the child shell so Tauri's supervisor spawns Brain
    # with --mock (see supervisor.rs brain_cmd). Single-quoted so PowerShell
    # passes the literal assignment through instead of expanding it here.
    $prefix = if ($Mock) { '$env:HALO_MOCK=''1''; ' } else { '' }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "${prefix}cd '$root\ui'; npm run tauri dev"
}

function Start-Brain {
    $brainCmd = if ($Mock) { "python -m brain --mock" } else { "python -m brain" }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\brain'; $brainCmd"
}

function Start-Voice {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\voice'; python -m voice"
}

if ($Smoke) {
    python "$root\shared\smoke_test.py"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    python "$root\shared\phase1_check.py"
    exit $LASTEXITCODE
}

switch ($Only) {
    "ui"    { Start-Ui }
    "brain" { Start-Brain }
    "voice" { Start-Voice }
    "all"   { Start-Ui }
}
