param(
    [switch]$NoLaunch,
    [switch]$SkipDownload,
    [switch]$ForceUiMerge,
    [switch]$DebugLogs,
    [ValidateSet("Keep", "CursorPos", "WindowPos")]
    [string]$PcInput = "Keep"
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
        [string]$MfaDir,
        [string]$MfaaTag
    )

    Write-Step "Refresh local assets and agent into install"
    & $VenvPython .\install.py dev-local win 5.9.2
    if ($LASTEXITCODE -ne 0) {
        throw "install.py failed."
    }

    $installDir = Join-Path $RepoRoot "install"
    $installedExe = Join-Path $installDir "MFAAvalonia.exe"
    $uiTagPath = Join-Path $installDir ".mfaa-ui-tag"
    $installedTag = ""
    if (Test-Path -LiteralPath $uiTagPath) {
        $installedTag = (Get-Content -LiteralPath $uiTagPath -Raw -Encoding UTF8).Trim()
    }

    $needsUiMerge = $false
    if ($ForceUiMerge) {
        $needsUiMerge = $true
    } elseif (-not (Test-Path -LiteralPath $installedExe)) {
        $needsUiMerge = $true
    } elseif ($installedTag.Length -gt 0) {
        $needsUiMerge = ($installedTag -ne $MfaaTag)
    } else {
        $cachedExe = Join-Path $MfaDir "MFAAvalonia.exe"
        if ((Test-Path -LiteralPath $cachedExe) -and ((Get-FileSha256 $installedExe) -eq (Get-FileSha256 $cachedExe))) {
            Set-Content -LiteralPath $uiTagPath -Value $MfaaTag -Encoding UTF8
            $installedTag = $MfaaTag
        } else {
            $needsUiMerge = $true
        }
    }

    if ($needsUiMerge) {
        Write-Step "Merge MFAAvalonia UI files"
        try {
            Copy-Item -Path (Join-Path $MfaDir "*") -Destination $installDir -Recurse -Force
            Set-Content -LiteralPath $uiTagPath -Value $MfaaTag -Encoding UTF8
        } catch {
            throw "Failed to merge MFAAvalonia UI files. Close the running MFABD2 UI if install DLLs are locked, or rerun without -ForceUiMerge when the installed UI tag is already current. Original error: $($_.Exception.Message)"
        }
    } else {
        Write-Step "Skip MFAAvalonia UI files"
        Write-Host "Installed UI already matches $MfaaTag. Use -ForceUiMerge to copy UI files again." -ForegroundColor Green
    }

    Write-Step "Patch local development agent config"
    $interfacePath = Join-Path $RepoRoot "install\interface.json"
    $interface = Get-Content -LiteralPath $interfacePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $interface.agent.child_exec = $VenvPython
    $interface.agent.child_args = @("-u", "-X", "utf8=1", "{PROJECT_DIR}/agent/main.py")
    $interface | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $interfacePath -Encoding UTF8
}

function Set-DebugLogOptions {
    if (-not $DebugLogs) {
        return
    }

    Write-Step "Enable local debug logging"
    $debugDir = Join-Path $RepoRoot "install\debug"
    $configDir = Join-Path $RepoRoot "install\config"
    New-Item -ItemType Directory -Force -Path $debugDir | Out-Null
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null

    $maaOptionPath = Join-Path $configDir "maa_option.json"
    $options = @{}
    if (Test-Path -LiteralPath $maaOptionPath) {
        $raw = Get-Content -LiteralPath $maaOptionPath -Raw -Encoding UTF8
        if ($raw.Trim().Length -gt 0) {
            $json = $raw | ConvertFrom-Json
            foreach ($prop in $json.PSObject.Properties) {
                $options[$prop.Name] = $prop.Value
            }
        }
    }

    $options["draw_quality"] = 85
    $options["logging"] = $true
    $options["save_draw"] = $true
    $options["save_on_error"] = $true
    $options["stdout_level"] = 7

    $options | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $maaOptionPath -Encoding UTF8
    $env:RDD_DEBUG_DIR = $debugDir

    Write-Host "Debug logs enabled:" -ForegroundColor Green
    Write-Host "  UI log:       install\logs\log-*.log"
    Write-Host "  Maa log:      install\debug\maa.log"
    Write-Host "  Draw images:  install\debug\vision"
    Write-Host "  Error images: install\debug\on_error"
    Write-Host "  Red dot:      install\debug\RedDotDetector"
}

function Set-PcInputHarness {
    if ($PcInput -eq "Keep") {
        return
    }

    Write-Step "Apply PC input harness: $PcInput"
    $controllerName = "PC" + [char]0x5BA2 + [char]0x6237 + [char]0x7AEF
    if ($PcInput -eq "CursorPos") {
        $controllerName = $controllerName + "(CursorPos)"
        $mouseType = "PostMessageWithCursorPos"
        $keyboardType = "PostMessageWithCursorPos"
    } else {
        $mouseType = "PostMessageWithWindowPos"
        $keyboardType = "PostMessageWithWindowPos"
    }

    $instancesDir = Join-Path $RepoRoot "install\config\instances"
    if (-not (Test-Path -LiteralPath $instancesDir)) {
        Write-Host "No install instance config found yet. The controller is available in the UI after launch." -ForegroundColor Yellow
        return
    }

    $instanceFiles = Get-ChildItem -LiteralPath $instancesDir -Filter "*.json" -File
    foreach ($file in $instanceFiles) {
        $instance = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $resource = ""
        $currentController = ""
        $currentControllerName = ""
        if ($instance.PSObject.Properties.Name -contains "Resource") {
            $resource = [string]$instance.Resource
        }
        if ($instance.PSObject.Properties.Name -contains "CurrentController") {
            $currentController = [string]$instance.CurrentController
        }
        if ($instance.PSObject.Properties.Name -contains "CurrentControllerName") {
            $currentControllerName = [string]$instance.CurrentControllerName
        }

        if ($resource -eq "PC" -or $currentController -eq "Win32" -or $currentControllerName -like "PC*") {
            $instance.CurrentControllerName = $controllerName
            $instance.CurrentController = "Win32"
            $instance.Win32ControlMouseType = $mouseType
            $instance.Win32ControlKeyboardType = $keyboardType
            $instance | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $file.FullName -Encoding UTF8
            Write-Host "Updated instance $($file.BaseName): $mouseType" -ForegroundColor Green
        }
    }
}

function Set-JsonProperty {
    param(
        [Parameter(Mandatory=$true)]$Object,
        [Parameter(Mandatory=$true)][string]$Name,
        $Value
    )

    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    } else {
        $Object | Add-Member -MemberType NoteProperty -Name $Name -Value $Value
    }
}

function Sync-InstanceTaskMetadata {
    Write-Step "Sync instance task metadata from local interface"

    $interfacePath = Join-Path $RepoRoot "install\interface.json"
    $instancesDir = Join-Path $RepoRoot "install\config\instances"
    if (-not (Test-Path -LiteralPath $instancesDir)) {
        Write-Host "No install instance config found yet. Task metadata will be current for new instances." -ForegroundColor Yellow
        return
    }

    $interface = Get-Content -LiteralPath $interfacePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $tasksByEntry = @{}
    foreach ($task in $interface.task) {
        if ($task.PSObject.Properties.Name -contains "entry") {
            $tasksByEntry[[string]$task.entry] = $task
        }
    }

    $instanceFiles = Get-ChildItem -LiteralPath $instancesDir -Filter "*.json" -File
    foreach ($file in $instanceFiles) {
        $instance = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not ($instance.PSObject.Properties.Name -contains "TaskItems")) {
            continue
        }

        $changed = $false
        foreach ($item in $instance.TaskItems) {
            if (-not ($item.PSObject.Properties.Name -contains "entry")) {
                continue
            }
            $entry = [string]$item.entry
            if (-not $tasksByEntry.ContainsKey($entry)) {
                continue
            }

            $source = $tasksByEntry[$entry]
            foreach ($field in @("name", "default_check", "description", "controller")) {
                if ($source.PSObject.Properties.Name -contains $field) {
                    Set-JsonProperty -Object $item -Name $field -Value $source.$field
                    $changed = $true
                }
            }
        }

        if ($changed) {
            $instance | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $file.FullName -Encoding UTF8
            Write-Host "Synced task metadata for instance $($file.BaseName)." -ForegroundColor Green
        }
    }
}

function Assert-LocalConfigFresh {
    Write-Step "Verify install uses current workspace config"
    $pcControllerName = "PC" + [char]0x5BA2 + [char]0x6237 + [char]0x7AEF
    $pcCursorControllerName = $pcControllerName + "(CursorPos)"

    $pairs = @(
        @("assets\resource\pc\pipeline\StartGame.json", "install\resource\pc\pipeline\StartGame.json"),
        @("assets\resource\pc\pipeline\Battle.json", "install\resource\pc\pipeline\Battle.json"),
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
    $controllerNames = @($installInterface.controller | ForEach-Object { $_.name })
    if (-not ($controllerNames -contains $pcControllerName)) {
        throw "install\interface.json is missing the PC client controller."
    }
    if (-not ($controllerNames -contains $pcCursorControllerName)) {
        throw "install\interface.json is missing the CursorPos PC client controller."
    }

    $pcResource = $installInterface.resource | Where-Object { $_.name -eq "PC" } | Select-Object -First 1
    if (-not $pcResource) {
        throw "PC resource was not found in install\interface.json."
    }
    if (-not ($pcResource.controller -contains $pcCursorControllerName)) {
        throw "PC resource in install\interface.json does not allow CursorPos PC client."
    }

    $mailTask = $installInterface.task | Where-Object { $_.entry -eq "Mail_HomePage" } | Select-Object -First 1
    if (-not $mailTask) {
        throw "Mail_HomePage task was not found in install\interface.json."
    }
    if (-not ($mailTask.controller -contains $pcControllerName)) {
        throw "Mail task in install\interface.json does not allow PC client."
    }
    if (-not ($mailTask.controller -contains $pcCursorControllerName)) {
        throw "Mail task in install\interface.json does not allow CursorPos PC client."
    }
    $quickHuntTask = $installInterface.task | Where-Object { $_.entry -eq "QuickHunt_Start" } | Select-Object -First 1
    if (-not $quickHuntTask) {
        throw "QuickHunt_Start task was not found in install\interface.json."
    }
    if (-not ($quickHuntTask.controller -contains $pcControllerName)) {
        throw "QuickHunt task in install\interface.json does not allow PC client."
    }
    if (-not ($quickHuntTask.controller -contains $pcCursorControllerName)) {
        throw "QuickHunt task in install\interface.json does not allow CursorPos PC client."
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
Sync-Install -VenvPython $venvPython -MfaDir $mfaDir -MfaaTag $mfaaTag
Set-DebugLogOptions
Set-PcInputHarness
Sync-InstanceTaskMetadata
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
