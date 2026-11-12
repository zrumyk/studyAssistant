param([switch]$AppOnly,[switch]$DocumentTools)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
New-Item -ItemType Directory -Force -Path "$taskRoot/.cache","$taskRoot/tools","$taskRoot/data","$taskRoot/models" | Out-Null
$env:TEMP = "$taskRoot/.cache"
$env:TMP = "$taskRoot/.cache"
$env:UV_CACHE_DIR = "$taskRoot/.cache/uv"
$env:UV_PYTHON_INSTALL_DIR = "$taskRoot/tools/python"
$env:npm_config_cache = "$taskRoot/.cache/npm"
$requirementsFile = if (Test-Path -LiteralPath "$taskRoot/requirements.lock.txt") { "$taskRoot/requirements.lock.txt" } else { "$taskRoot/requirements.txt" }
if (-not $AppOnly -and (Get-PSDrive C).Free -lt 10GB) {
    throw 'Free at least 10 GB on C: before installing models and runtimes. No personal files will be deleted. Use -AppOnly to prepare the application.'
}
if (-not (Test-Path -LiteralPath "$taskRoot/.venv/Scripts/python.exe")) {
    $uvPath = "$taskRoot/tools/uv/uv.exe"
    if (-not (Test-Path -LiteralPath $uvPath)) {
        Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/download/0.13.1/uv-x86_64-pc-windows-msvc.zip' -OutFile "$taskRoot/.cache/uv.zip"
        Expand-Archive -LiteralPath "$taskRoot/.cache/uv.zip" -DestinationPath "$taskRoot/tools/uv" -Force
    }
    & $uvPath python install 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 installation failed' }
    & $uvPath venv --python 3.12 --seed --relocatable "$taskRoot/.venv"
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
    & $uvPath pip install --python "$taskRoot/.venv/Scripts/python.exe" -r $requirementsFile
} else {
    & "$taskRoot/.venv/Scripts/python.exe" -m pip install --cache-dir "$taskRoot/.cache/pip" -r $requirementsFile
}
if ($LASTEXITCODE -ne 0) { throw 'Python dependencies installation failed' }
Push-Location frontend
try {
    npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
if (-not $AppOnly) {
    $ollamaPath = "$taskRoot/tools/ollama/ollama.exe"
    if (-not (Test-Path -LiteralPath $ollamaPath)) {
        Invoke-WebRequest -Uri 'https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip' -OutFile "$taskRoot/.cache/ollama.zip"
        Expand-Archive -LiteralPath "$taskRoot/.cache/ollama.zip" -DestinationPath "$taskRoot/tools/ollama" -Force
    }
    Write-Host 'Portable Ollama prepared. start.cmd will start it. Pull models in Settings.'
    Write-Host 'Set Docker Desktop Settings > Resources > Advanced > Disk image location to D: before downloading runtime images.'
    Write-Host 'For PDF export install LibreOffice. For OCR install Tesseract with ukr+eng. Paths can be set in Settings.'
    if ($DocumentTools) { & "$taskRoot/scripts/install-document-tools.ps1" }
}
Write-Host 'Application prepared. Run start.cmd.'
