param(
    [switch]$NoLaunch,
    [switch]$SkipDownload
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptPath "..")
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Read-MfaaTag {
    $requirements = Join-Path $RepoRoot "requirements.txt"
    foreach ($line in Get-Content -LiteralPath $requirements -Encoding UTF8) {
        if ($line -match '^\s*#\s*MFAA_TAG=(v[\w\.\-]+)') {
            return $Matches[1]
        }
    }
    throw "requirements.txt does not contain # MFAA_TAG=..."
}

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Ensure-Venv {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Step "Create .venv"
        $py310 = Get-Command py -ErrorAction SilentlyContinue
        if ($py310) {
            & py -3.10 -m venv .venv
        } else {
            & python -m venv .venv
        }
    }

    Write-Step "Check Python dependencies"
    $importCheck = & $venvPython -c "import maa, jsonc" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
        & $venvPython -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple maafw==5.9.2
    }

    return (Resolve-Path $venvPython).Path
}

function Ensure-MfaAvalonia {
    param([string]$MfaaTag)

    $cacheRoot = Join-Path $RepoRoot "build\local-ui"
    $mfaRoot = Join-Path $cacheRoot "MFAAvalonia-$MfaaTag-win-x64"
    $mfaExe = Get-ChildItem -LiteralPath $mfaRoot -Recurse -Filter "MFAAvalonia.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($mfaExe) {
        return $mfaExe.DirectoryName
    }

    if ($SkipDownload) {
        throw "Cached MFAAvalonia.exe was not found and -SkipDownload was specified."
    }

    Write-Step "Download MFAAvalonia $MfaaTag"
    New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
    $zipPath = Join-Path $cacheRoot "MFAAvalonia-$MfaaTag-win-x64.zip"
    $url = "https://github.com/MaaXYZ/MFAAvalonia/releases/download/$MfaaTag/MFAAvalonia-$MfaaTag-win-x64.zip"
    Invoke-WebRequest -Uri $url -OutFile $zipPath

    if (Test-Path -LiteralPath $mfaRoot) {
        Remove-Item -LiteralPath $mfaRoot -Recurse -Force
    }
    Expand-Archive -LiteralPath $zipPath -DestinationPath $mfaRoot -Force

    $mfaExe = Get-ChildItem -LiteralPath $mfaRoot -Recurse -Filter "MFAAvalonia.exe" | Select-Object -First 1
    if (-not $mfaExe) {
        throw "MFAAvalonia.exe was not found in the downloaded archive."
    }
    return $mfaExe.DirectoryName
}

function Ensure-CommonAssets {
    $ocrDir = Join-Path $RepoRoot "assets\MaaCommonAssets\OCR"
    if (Test-Path -LiteralPath $ocrDir) {
        return
    }

    Write-Step "Restore MaaCommonAssets"
    $cacheRoot = Join-Path $RepoRoot "build\local-ui"
    $assetsRepo = Join-Path $cacheRoot "MFABD2-Assets"
    if (-not (Test-Path -LiteralPath $assetsRepo)) {
        New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
        & git clone --depth 1 https://github.com/sunyink/MFABD2-Assets.git $assetsRepo
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to clone MFABD2-Assets."
        }
    }

    $source = Join-Path $assetsRepo "MaaCommonAssets"
    if (-not (Test-Path -LiteralPath $source)) {
        throw "MFABD2-Assets does not contain MaaCommonAssets."
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "assets\MaaCommonAssets") | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination (Join-Path $RepoRoot "assets\MaaCommonAssets") -Recurse -Force

    if (-not (Test-Path -LiteralPath $ocrDir)) {
        throw "OCR assets were not restored."
    }
}

function Sync-Install {
    param(
        [string]$VenvPython,
        [string]$MfaDir
    )

    Write-Step "Refresh local assets and agent into install"
    & $VenvPython .\install.py dev-local win 5.9.2
    if ($LASTEXITCODE -ne 0) {
        throw "install.py failed."
    }

    Write-Step "Merge MFAAvalonia UI files"
    Copy-Item -Path (Join-Path $MfaDir "*") -Destination (Join-Path $RepoRoot "install") -Recurse -Force

    Write-Step "Patch local development agent config"
    $interfacePath = Join-Path $RepoRoot "install\interface.json"
    $interface = Get-Content -LiteralPath $interfacePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $interface.agent.child_exec = $VenvPython
    $interface.agent.child_args = @("-u", "-X", "utf8=1", "{PROJECT_DIR}/agent/main.py")
    $interface | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $interfacePath -Encoding UTF8
}

function Assert-LocalConfigFresh {
    Write-Step "Verify install uses current workspace config"
    $pcControllerName = "PC" + [char]0x5BA2 + [char]0x6237 + [char]0x7AEF

    $pairs = @(
        @("assets\resource\pc\pipeline\StartGame.json", "install\resource\pc\pipeline\StartGame.json"),
        @("assets\resource\pc\pipeline\Mail.json", "install\resource\pc\pipeline\Mail.json"),
        @("assets\resource\pc\pipeline\Dummy.json", "install\resource\pc\pipeline\Dummy.json"),
        @("assets\resource\pc\pipeline\Global.json", "install\resource\pc\pipeline\Global.json"),
        @("assets\resource\pc\pipeline\Close.json", "install\resource\pc\pipeline\Close.json"),
        @("agent\action\pc_window.py", "install\agent\action\pc_window.py")
    )

    foreach ($pair in $pairs) {
        $src = Join-Path $RepoRoot $pair[0]
        $dst = Join-Path $RepoRoot $pair[1]
        if (-not (Test-Path -LiteralPath $dst)) {
            throw "install is missing file: $($pair[1])"
        }
        if ((Get-FileSha256 $src) -ne (Get-FileSha256 $dst)) {
            throw "install file is not from the current workspace: $($pair[1])"
        }
    }

    $installInterface = Get-Content -LiteralPath (Join-Path $RepoRoot "install\interface.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $mailTask = $installInterface.task | Where-Object { $_.entry -eq "Mail_HomePage" } | Select-Object -First 1
    if (-not $mailTask) {
        throw "Mail_HomePage task was not found in install\interface.json."
    }
    if (-not ($mailTask.controller -contains $pcControllerName)) {
        throw "Mail task in install\interface.json does not allow PC client."
    }
    if ($installInterface.agent.child_exec -notlike "*\.venv\Scripts\python.exe") {
        throw "install\interface.json does not point to local .venv Python."
    }

    Write-Host "Local config check passed: install uses the current workspace PC MVP config." -ForegroundColor Green
}

$mfaaTag = Read-MfaaTag
$venvPython = Ensure-Venv
$mfaDir = Ensure-MfaAvalonia -MfaaTag $mfaaTag
Ensure-CommonAssets
Sync-Install -VenvPython $venvPython -MfaDir $mfaDir
Assert-LocalConfigFresh

$exePath = Join-Path $RepoRoot "install\MFAAvalonia.exe"
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Missing $exePath"
}

if ($NoLaunch) {
    Write-Host "Setup and verification completed. UI was not launched because -NoLaunch was specified." -ForegroundColor Yellow
} else {
    Write-Step "Launch MFABD2 UI"
    Start-Process -FilePath $exePath -WorkingDirectory (Join-Path $RepoRoot "install")
}
