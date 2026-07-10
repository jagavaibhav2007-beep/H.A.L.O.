# Launches all three Halo Phase-0 processes for local dev.
# Each runs in its own PowerShell window so logs stay readable.
# Usage: ./dev.ps1  (or ./dev.ps1 -Only brain|voice|ui to launch just one)

param(
    [ValidateSet("all", "ui", "brain", "voice")]
    [string]$Only = "all"
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

switch ($Only) {
    "ui"    { Start-Ui }
    "brain" { Start-Brain }
    "voice" { Start-Voice }
    "all"   { Start-Brain; Start-Voice; Start-Ui }
}
