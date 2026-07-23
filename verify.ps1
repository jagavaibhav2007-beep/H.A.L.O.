# Full automated verification for the implemented Phase 0-2 repository.
# This is intentionally broader than `./dev.ps1 -Smoke`, which runs only the
# three cross-process protocol phase checks.

[CmdletBinding()]
param(
    [string]$PythonCommand,
    [string[]]$PythonArguments = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = $PSScriptRoot

function Test-PythonLauncher {
    param(
        [Parameter(Mandatory)]
        [string]$Command,

        [string[]]$PrefixArguments = @()
    )

    try {
        & $Command @PrefixArguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-PythonLauncher {
    if ($PythonCommand) {
        if (-not (Test-PythonLauncher -Command $PythonCommand -PrefixArguments $PythonArguments)) {
            throw "The supplied Python launcher is unavailable or is older than Python 3.11: $PythonCommand $($PythonArguments -join ' ')"
        }
        return [pscustomobject]@{ Command = $PythonCommand; Arguments = @($PythonArguments) }
    }

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
    if ($env:USERPROFILE) {
        $runtimeRoots += Join-Path $env:USERPROFILE ".cache\codex-runtimes"
    }
    if ($env:LOCALAPPDATA) {
        $runtimeRoots += Join-Path $env:LOCALAPPDATA "codex-runtimes"
    }

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

$python = Resolve-PythonLauncher
Write-Host "Using Python launcher: $($python.Command) $($python.Arguments -join ' ')"

function Invoke-VerificationStep {
    param(
        [Parameter(Mandatory)]
        [string]$Label,

        [Parameter(Mandatory)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory)]
        [string]$Command,

        [string[]]$PrefixArguments = @(),

        [string[]]$Arguments = @()
    )

    Write-Host ""
    Write-Host "=== $Label ==="
    Push-Location $WorkingDirectory
    try {
        & $Command @PrefixArguments @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

Invoke-VerificationStep `
    -Label "IPC contract sync" `
    -WorkingDirectory $root `
    -Command $python.Command `
    -PrefixArguments $python.Arguments `
    -Arguments @("shared/check_contract_sync.py")

foreach ($suite in @("brain", "voice")) {
    $pythonTests = Get-ChildItem -LiteralPath "$root\$suite\tests" -Filter "test_*.py" |
        Sort-Object Name
    foreach ($test in $pythonTests) {
        Invoke-VerificationStep `
            -Label "Python: $suite/tests/$($test.Name)" `
            -WorkingDirectory $root `
            -Command $python.Command `
            -PrefixArguments $python.Arguments `
            -Arguments @($test.FullName)
    }
}

$uiSelfChecks = Get-ChildItem -LiteralPath "$root\ui\src" -Recurse -Filter "*.selfcheck.ts" |
    Sort-Object FullName
foreach ($selfCheck in $uiSelfChecks) {
    $relativeSelfCheck = $selfCheck.FullName.Substring($root.Length).TrimStart("\").Replace("\", "/")
    Invoke-VerificationStep `
        -Label "UI self-check: $relativeSelfCheck" `
        -WorkingDirectory $root `
        -Command "node" `
        -Arguments @($selfCheck.FullName)
}

Invoke-VerificationStep `
    -Label "UI Vitest" `
    -WorkingDirectory "$root\ui" `
    -Command "npm" `
    -Arguments @("test")

Invoke-VerificationStep `
    -Label "UI typecheck and production build" `
    -WorkingDirectory "$root\ui" `
    -Command "npm" `
    -Arguments @("run", "build")

Invoke-VerificationStep `
    -Label "Rust tests" `
    -WorkingDirectory "$root\ui\src-tauri" `
    -Command "cargo" `
    -Arguments @("test")

$phaseChecks = @(
    @{ Label = "Phase 0 protocol smoke"; Script = "shared/smoke_test.py" },
    @{ Label = "Phase 1 mock protocol gate"; Script = "shared/phase1_check.py" },
    @{ Label = "Phase 2 real-Brain offline protocol gate"; Script = "shared/phase2_check.py" }
)
foreach ($phase in $phaseChecks) {
    Invoke-VerificationStep `
        -Label $phase.Label `
        -WorkingDirectory $root `
        -Command $python.Command `
        -PrefixArguments $python.Arguments `
        -Arguments @($phase.Script)
}

Write-Host ""
Write-Host "=== FULL AUTOMATED VERIFICATION PASSED ==="
