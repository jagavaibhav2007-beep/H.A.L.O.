function Repair-DuplicateProcessPath {
    # Some launchers inject both Path and PATH. PowerShell's Env: provider and
    # Start-Process build a case-insensitive dictionary and throw before the
    # child starts. Collapse those aliases inside this script process only.
    $variables = [Environment]::GetEnvironmentVariables("Process")
    $pathKeys = @($variables.Keys | Where-Object { [string]$_ -ieq "Path" })
    if ($pathKeys.Count -le 1) { return $false }

    $pathValue = $null
    foreach ($key in $pathKeys) {
        $candidate = [string]$variables[$key]
        if ($candidate) { $pathValue = $candidate; break }
    }
    foreach ($key in $pathKeys) {
        [Environment]::SetEnvironmentVariable([string]$key, $null, "Process")
    }
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
    return $true
}
