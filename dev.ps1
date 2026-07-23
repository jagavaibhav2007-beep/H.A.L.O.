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

function Test-PythonLauncher {
    param([string]$Command, [string[]]$PrefixArguments = @())
    try {
        & $Command @PrefixArguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-PythonLauncher {
    $pythonApplication = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($pythonApplication -and (Test-PythonLauncher -Command $pythonApplication.Source)) {
        return [pscustomobject]@{ Command = $pythonApplication.Source; Arguments = @() }
    }

    $pyApplication = Get-Command py -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($pyApplication -and (Test-PythonLauncher -Command $pyApplication.Source -PrefixArguments @("-3"))) {
        return [pscustomobject]@{ Command = $pyApplication.Source; Arguments = @("-3") }
    }

    $runtimeRoots = @()
    if ($env:USERPROFILE) { $runtimeRoots += Join-Path $env:USERPROFILE ".cache\codex-runtimes" }
    if ($env:LOCALAPPDATA) { $runtimeRoots += Join-Path $env:LOCALAPPDATA "codex-runtimes" }
    foreach ($runtimeRoot in $runtimeRoots) {
        if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) { continue }
        $bundledPythons = Get-ChildItem -LiteralPath $runtimeRoot -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "dependencies\python\python.exe" } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
        foreach ($bundledPython in $bundledPythons) {
            if (Test-PythonLauncher -Command $bundledPython) {
                return [pscustomobject]@{ Command = $bundledPython; Arguments = @() }
            }
        }
    }

    throw "Python 3.11+ was not found. Install 'python', install the 'py' launcher, or run from a Codex environment with a discoverable bundled runtime."
}

function Invoke-Python {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$Launcher,

        [Parameter(Mandatory)]
        [string[]]$Arguments
    )
    & $Launcher.Command @($Launcher.Arguments) @Arguments
}

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
