$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if ((Get-PSDrive C).Free -lt 10GB) { throw 'Free at least 10 GB on C: first. No files will be deleted.' }
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'Windows App Installer (winget) is required for document tools.' }
$packages = @(
    @{ Id='TheDocumentFoundation.LibreOffice'; Folder='LibreOffice'; Exe='soffice.exe'; Existing='C:\Program Files\LibreOffice\program\soffice.exe' },
    @{ Id='UB-Mannheim.TesseractOCR'; Folder='tesseract'; Exe='tesseract.exe'; Existing='C:\Program Files\Tesseract-OCR\tesseract.exe' },
    @{ Id='oschwartz10612.Poppler'; Folder='poppler'; Exe='pdftoppm.exe'; Existing="$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe" }
)
foreach ($package in $packages) {
    $location = Join-Path "$taskRoot/tools" $package.Folder
    New-Item -ItemType Directory -Force -Path $location | Out-Null
    $existing = Get-ChildItem -LiteralPath $location -Filter $package.Exe -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $existing -and -not (Test-Path -LiteralPath $package.Existing) -and -not (Get-Command $package.Exe -ErrorAction SilentlyContinue)) {
        & winget install --id $package.Id --exact --silent --location $location --accept-source-agreements --accept-package-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not install $($package.Id). Set its executable path in Settings after manual installation." }
    }
}
$tessdata = "$taskRoot/tools/tessdata"
New-Item -ItemType Directory -Force -Path $tessdata | Out-Null
foreach ($language in @('ukr','eng')) {
    if (-not (Test-Path -LiteralPath "$tessdata/$language.traineddata")) {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$language.traineddata" -OutFile "$tessdata/$language.traineddata"
    }
}
& "$taskRoot/.venv/Scripts/python.exe" "$taskRoot/scripts/configure-tools.py"
