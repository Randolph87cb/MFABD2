[CmdletBinding()]
param(
    [ValidateRange(1, 900)]
    [int]$Timeout = 90
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Project Python environment not found: $PythonPath. See README.md for setup."
}

& $PythonPath (Join-Path $PSScriptRoot 'open_game.py') --timeout $Timeout
exit $LASTEXITCODE
