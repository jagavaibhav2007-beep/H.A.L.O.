# Launches all three Halo Phase-0 processes for local dev.
# Each runs in its own PowerShell window so logs stay readable.
# Usage: ./dev.ps1  (or ./dev.ps1 -Only brain|voice|ui to launch just one)
#        ./dev.ps1 -Smoke  runs the Phase 0 exit-criteria smoke test in-place
#        (no windows spawned) instead of launching the dev processes.

param(
    [ValidateSet("all", "ui", "brain", "voice")]
    [string]$Only = "all",

    [switch]$Smoke
)

$root = $PSScriptRoot

function Start-Ui {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\ui'; npm run tauri dev"
}

function Start-Brain {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\brain'; python -m brain"
}

function Start-Voice {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\voice'; python -m voice"
}

if ($Smoke) {
    python "$root\shared\smoke_test.py"
    exit $LASTEXITCODE
}

switch ($Only) {
    "ui"    { Start-Ui }
    "brain" { Start-Brain }
    "voice" { Start-Voice }
    "all"   { Start-Brain; Start-Voice; Start-Ui }
}
