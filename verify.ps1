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
. "$PSScriptRoot\_python.ps1"

$python = Resolve-PythonLauncher -PythonCommand $PythonCommand -PythonArguments $PythonArguments
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

Invoke-VerificationStep `
    -Label "Browser launcher environment" `
    -WorkingDirectory $root `
    -Command "powershell" `
    -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$root\shared\launcher_check.ps1")

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
$viteNode = "$root\ui\node_modules\.bin\vite-node.cmd"
foreach ($selfCheck in $uiSelfChecks) {
    $relativeSelfCheck = $selfCheck.FullName.Substring($root.Length).TrimStart("\").Replace("\", "/")
    Invoke-VerificationStep `
        -Label "UI self-check: $relativeSelfCheck" `
        -WorkingDirectory $root `
        -Command $viteNode `
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
    -Arguments @("test", "-j", "1")

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
