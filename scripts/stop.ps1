$ErrorActionPreference = 'Stop'
$taskRoot = (Split-Path -Parent $PSScriptRoot).Replace('/','\')
# Stop only this application's Python processes; never kill other Python/Ollama sessions.
$taskProcesses = @(Get-CimInstance Win32_Process)
function Stop-StudyTree([int]$taskProcessId) {
    foreach ($child in $taskProcesses | Where-Object { $_.ParentProcessId -eq $taskProcessId }) {
        Stop-StudyTree $child.ProcessId
    }
    Stop-Process -Id $taskProcessId -ErrorAction SilentlyContinue
}
$taskProcesses | Where-Object {
    $_.ExecutablePath -eq "$taskRoot\.venv\Scripts\python.exe" -and
    ($_.CommandLine -like '*study.worker*' -or $_.CommandLine -like '*study.api:app*')
} | ForEach-Object { Stop-StudyTree $_.ProcessId }
Write-Host 'Application stopped. Ollama and Docker remain under user control.'
