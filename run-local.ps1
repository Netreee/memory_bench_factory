$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Python environment is missing. Run: py -3.12 -m venv venv; then venv\Scripts\python.exe -m pip install -r requirements-minimal.txt.'
}
Push-Location $PSScriptRoot
try {
    & $taskPython -X utf8 -m pipeline.factory @args
    $taskExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $taskExitCode
