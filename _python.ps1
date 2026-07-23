# Shared Python 3.11+ launcher resolution, dot-sourced by dev.ps1 and verify.ps1.
# Keep free of script-specific state so both callers get identical behavior.

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
    param(
        [string]$PythonCommand,
        [string[]]$PythonArguments = @()
    )

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

    # ponytail: bundled-runtime scan covers agent/CI envs where python isn't on
    # PATH; local dev resolves at the python/py branches above.
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
