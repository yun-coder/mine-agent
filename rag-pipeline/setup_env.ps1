$ErrorActionPreference = "Stop"

$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Project ".venv\Scripts\python.exe"
$Requirements = Join-Path $Project "requirements.txt"

function Get-BasePython {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @($pyLauncher.Source, "-3.11")
    }

    $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path $localPython) {
        return @($localPython)
    }

    throw "Python 3.11 was not found. Install Python 3.11 or make the 'py' launcher available."
}

Set-Location $Project

if (!(Test-Path $VenvPython)) {
    $base = Get-BasePython
    $baseArgs = @()
    if ($base.Length -gt 1) {
        $baseArgs = $base[1..($base.Length - 1)]
    }
    Write-Host "Creating project venv at $Project\.venv"
    & $base[0] @baseArgs -m venv ".venv"
}

Write-Host "Installing dependencies into project venv"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r $Requirements

Write-Host ""
Write-Host "Project environment is ready:"
Write-Host "  $VenvPython"
