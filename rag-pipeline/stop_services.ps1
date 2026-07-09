$ErrorActionPreference = "Continue"

$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Project

$ports = @(8000)
foreach ($port in $ports) {
    $processIds = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique

    foreach ($processId in $processIds) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "Stopping port $port PID $processId ($($process.ProcessName))"
            Stop-Process -Id $processId -Force
        }
    }
}

Write-Host "Stopping docker compose services"
docker compose down

$ollamaProcesses = Get-Process -Name "ollama", "ollama app" -ErrorAction SilentlyContinue
foreach ($process in $ollamaProcesses) {
    Write-Host "Stopping $($process.ProcessName) PID $($process.Id)"
    Stop-Process -Id $process.Id -Force
}

Write-Host "Done"
