$ErrorActionPreference = "Stop"

$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Project ".venv\Scripts\python.exe"
$SystemPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"

Set-Location $Project

if (Test-Path $VenvPython) {
    & $VenvPython (Join-Path $Project "start_all.py")
} elseif (Test-Path $SystemPython) {
    & $SystemPython (Join-Path $Project "start_all.py")
} else {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if (!$pyLauncher) {
        throw "No project venv or Python 3.11 found. Run setup_env.ps1 first."
    }
    & $pyLauncher.Source -3.11 (Join-Path $Project "start_all.py")
}
