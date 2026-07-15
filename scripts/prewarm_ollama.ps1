param(
    [string]$BaseUrl = "http://127.0.0.1:11434",
    [string]$LlmModel = "qwen2.5:7b",
    [string]$EmbedModel = "bge-m3:latest",
    [string]$KeepAlive = "24h",
    [int]$WaitSeconds = 180,
    [string]$ReportDir = "D:\langgraph-agent\reports"
)

$ErrorActionPreference = "Stop"
$started = Get-Date
$deadline = $started.AddSeconds($WaitSeconds)

while ((Get-Date) -lt $deadline) {
    try {
        Invoke-RestMethod -Uri "$BaseUrl/api/version" -TimeoutSec 5 | Out-Null
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}

if ((Get-Date) -ge $deadline) {
    throw "Ollama did not become ready within $WaitSeconds seconds"
}

$llmBody = @{
    model = $LlmModel
    prompt = "ready"
    stream = $false
    keep_alive = $KeepAlive
    options = @{
        num_predict = 1
    }
} | ConvertTo-Json -Depth 5

$llmStarted = Get-Date
$llmResult = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/api/generate" `
    -ContentType "application/json" `
    -Body $llmBody `
    -TimeoutSec 600
$llmSeconds = ((Get-Date) - $llmStarted).TotalSeconds

if (-not $llmResult.done) {
    throw "LLM prewarm did not complete"
}

$embedBody = @{
    model = $EmbedModel
    input = @("production readiness")
    keep_alive = $KeepAlive
    truncate = $true
} | ConvertTo-Json -Depth 5

$embedStarted = Get-Date
$embedResult = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/api/embed" `
    -ContentType "application/json" `
    -Body $embedBody `
    -TimeoutSec 600
$embedSeconds = ((Get-Date) - $embedStarted).TotalSeconds

$vector = $embedResult.embeddings[0]
$sum = 0.0
$finite = $true
foreach ($value in $vector) {
    $number = [double]$value
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
        $finite = $false
    }
    $sum += $number * $number
}

if ($vector.Count -ne 1024 -or -not $finite -or $sum -le 1e-24) {
    throw "Embedding prewarm returned an invalid vector"
}

$loaded = (Invoke-RestMethod -Uri "$BaseUrl/api/ps" -TimeoutSec 10).models
$report = [ordered]@{
    ok = $true
    generated_at = (Get-Date).ToString("o")
    base_url = $BaseUrl
    keep_alive = $KeepAlive
    llm = [ordered]@{
        model = $LlmModel
        elapsed_seconds = [math]::Round($llmSeconds, 3)
    }
    embedding = [ordered]@{
        model = $EmbedModel
        elapsed_seconds = [math]::Round($embedSeconds, 3)
        dimension = $vector.Count
        norm = [math]::Round([math]::Sqrt($sum), 6)
    }
    loaded_models = @($loaded | ForEach-Object { $_.name })
    total_elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
}

New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$reportPath = Join-Path $ReportDir (
    "ollama-prewarm-{0}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss")
)
$report | ConvertTo-Json -Depth 6 | Set-Content -Path $reportPath -Encoding UTF8
Write-Output "Ollama prewarm passed: $reportPath"
