$ErrorActionPreference = "Stop"
. "$PSScriptRoot\..\_process_env.ps1"

Repair-DuplicateProcessPath | Out-Null
$variables = [Environment]::GetEnvironmentVariables("Process")
$pathKeys = @($variables.Keys | Where-Object { [string]$_ -ieq "Path" })
if ($pathKeys.Count -ne 1) {
    throw "process environment still has $($pathKeys.Count) case-insensitive Path entries"
}

$probe = Start-Process `
    -FilePath "$env:SystemRoot\System32\cmd.exe" `
    -ArgumentList "/d", "/c", "exit 0" `
    -WindowStyle Hidden `
    -PassThru `
    -Wait `
    -ErrorAction Stop
if ($probe.ExitCode -ne 0) { throw "normalized Start-Process probe failed" }
Write-Host "[launcher_check] duplicate Path aliases normalized; Start-Process succeeds"
