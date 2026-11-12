param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$pythonPath = "$taskRoot/.venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first' }
New-Item -ItemType Directory -Force -Path "$taskRoot/data/logs","$taskRoot/.cache" | Out-Null
$env:PYTHONPATH = "$taskRoot/backend"
$env:STUDY_DATA = "$taskRoot/data"
$env:TEMP = "$taskRoot/.cache"
$env:TMP = "$taskRoot/.cache"
$env:OLLAMA_MODELS = "$taskRoot/models"
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_CONTEXT_LENGTH = '4096'
$env:OLLAMA_NO_CLOUD = '1'
$ollamaPath = "$taskRoot/tools/ollama/ollama.exe"
if (-not (Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)) {
    if (Test-Path -LiteralPath $ollamaPath) {
        # Keep Ollama's service identity together with the portable installation.
        $studyOriginalProfile = $env:USERPROFILE
        try {
            $env:USERPROFILE = "$taskRoot/tools/ollama-profile"
            New-Item -ItemType Directory -Force -Path $env:USERPROFILE | Out-Null
            Start-Process -FilePath $ollamaPath -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput "$taskRoot/data/logs/ollama.out.log" -RedirectStandardError "$taskRoot/data/logs/ollama.err.log"
        } finally { $env:USERPROFILE = $studyOriginalProfile }
    }
}
if (-not (Get-NetTCPConnection -LocalPort 18574 -State Bound -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $pythonPath -ArgumentList '-m','study.worker' -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput "$taskRoot/data/logs/worker.out.log" -RedirectStandardError "$taskRoot/data/logs/worker.err.log"
}
if (-not (Get-NetTCPConnection -LocalPort 18573 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','study.api:app','--host','127.0.0.1','--port','18573','--no-access-log' -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput "$taskRoot/data/logs/server.out.log" -RedirectStandardError "$taskRoot/data/logs/server.err.log"
}
$connected = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { Invoke-WebRequest -Uri 'http://127.0.0.1:18573/api/session' -UseBasicParsing | Out-Null; $connected = $true; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $connected) { throw 'Server did not start. See data/logs/server.err.log' }
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:18573' }
