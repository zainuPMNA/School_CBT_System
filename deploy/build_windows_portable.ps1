<#
  Build a PORTABLE Windows edition of the CBT app: one ZIP that runs on any
  Windows 10/11 (64-bit) PC with NO Python install, NO internet and NO Claude Code.

      powershell -ExecutionPolicy Bypass -File deploy\build_windows_portable.ps1
      powershell -ExecutionPolicy Bypass -File deploy\build_windows_portable.ps1 -IncludeData

  Run it on this PC (needs internet once). Produces:
      dist\CBT_Portable\              the ready-to-run folder
      dist\CBT_Portable_Windows.zip   the same folder zipped, to copy by USB / network

  Layout of the portable folder:
      START_CBT_APP.bat   <- double-click this on the other PC
      HOW_TO_USE.txt
      python\             official Windows embeddable Python + all packages
      app\                the CBT app (code, built CSS/JS, templates)

  -IncludeData  also copies this PC's database, uploaded images and settings
                (students, exams, results, logo). Without it the other PC starts
                fresh and asks you to create its own admin on first start.
#>
param(
    [switch]$IncludeData,
    [string]$PythonVersion = "3.12.10"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Root "dist"
$Out = Join-Path $Dist "CBT_Portable"
$Zip = Join-Path $Dist "CBT_Portable_Windows.zip"
$Cache = Join-Path $Dist ".cache"
$short = ($PythonVersion -split "\.")[0..1] -join ""   # 3.12.10 -> 312

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

$hostPy = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { throw "Python is needed on THIS PC to build the package." }
foreach ($f in @("static\css\app.css", "static\vendor\alpinejs\alpine.min.js", "static\vendor\katex\katex.min.js")) {
    if (-not (Test-Path (Join-Path $Root $f))) { throw "Missing $f - run: bash scripts/bundle_static.sh" }
}

if (Test-Path $Out) {
    try { Remove-Item -Recurse -Force $Out -ErrorAction Stop }
    catch {
        # A window (Explorer / Command Prompt) is open inside the old folder: build next to it instead.
        $Out = Join-Path (Join-Path $Dist ("build_" + (Get-Date -Format "yyyyMMdd_HHmmss"))) "CBT_Portable"
        Write-Host "    old CBT_Portable folder is in use (a window open inside it?) - building in $Out" -ForegroundColor Yellow
    }
}
New-Item -ItemType Directory -Force $Out, $Cache | Out-Null

# 1. Official embeddable Python (portable, no installer)
$embedZip = Join-Path $Cache "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $embedZip)) {
    Step "Downloading Python $PythonVersion (embeddable, 64-bit) from python.org"
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -UseBasicParsing "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $embedZip
}
$PyDir = Join-Path $Out "python"
Expand-Archive -Path $embedZip -DestinationPath $PyDir -Force

# The ._pth file defines the import path of an embedded Python: add the packages and the app.
$pth = Join-Path $PyDir "python$short._pth"
@("python$short.zip", ".", "Lib\site-packages", "..\app", "import site") | Set-Content -Encoding ascii $pth

# 2. All packages as Windows wheels for this exact Python version (offline on the target PC)
Step "Installing the app's packages into the portable Python (Windows, CPython $short)"
$site = Join-Path $PyDir "Lib\site-packages"
& $hostPy -m pip install --quiet --disable-pip-version-check --no-warn-script-location `
    -r (Join-Path $Root "requirements.txt") --target $site `
    --only-binary=:all: --platform win_amd64 --python-version ($PythonVersion -replace "\.\d+$", "") `
    --implementation cp --abi "cp$short" --abi abi3 --abi none
if ($LASTEXITCODE -ne 0) { throw "pip install for the portable Python failed." }
Get-ChildItem $site -Directory -Filter "__pycache__" -Recurse | Remove-Item -Recurse -Force

# 3. The app itself (no developer files, no local secrets)
Step "Copying the app"
$AppDir = Join-Path $Out "app"
$excludeDirs = @(".venv", "venv", "dist", "bin", "wheelhouse", "staticfiles", ".claude", ".git", "logs", "media", "backups") |
    ForEach-Object { Join-Path $Root $_ }
& robocopy.exe $Root $AppDir /E /NFL /NDL /NJH /NJS /NP /XD @excludeDirs "__pycache__" /XF ".env" "*.sqlite3*" "*.pyc" "START_CBT_APP.bat" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)." }
New-Item -ItemType Directory -Force (Join-Path $AppDir "logs"), (Join-Path $AppDir "media") | Out-Null

if ($IncludeData) {
    Step "Including this PC's data (database, uploaded images, settings)"
    foreach ($f in @("dev.sqlite3", ".env")) {
        $src = Join-Path $Root $f
        if (Test-Path $src) { Copy-Item $src (Join-Path $AppDir $f) }
    }
    & robocopy.exe (Join-Path $Root "media") (Join-Path $AppDir "media") /E /NFL /NDL /NJH /NJS /NP | Out-Null
}

# 4. Launcher + instructions at the top level
$bat = @'
@echo off
REM CBT Exam App - portable edition. Double-click to start; close this window to stop.
title CBT Exam App - keep this window open while using the app
cd /d "%~dp0app"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\run_windows.ps1" %*
echo.
echo  The CBT app has stopped. You can close this window.
pause >nul
'@
[IO.File]::WriteAllText((Join-Path $Out "START_CBT_APP.bat"), ($bat -replace "`r?`n", "`r`n"), [Text.Encoding]::ASCII)

$howto = @"
CBT EXAM APP - PORTABLE EDITION (Windows 10/11, 64-bit)
=======================================================

Nothing to install. No internet needed. Works without Claude Code.

START
  1. Copy this whole CBT_Portable folder to the PC (for example to C:\CBT_Portable).
     Keep the folder together - do not move files out of it.
  2. Double-click START_CBT_APP.bat
  3. FIRST START ONLY: the black window asks you to create the administrator
     (username, email can be left empty, password twice).
  4. The browser opens the app. Sign in with that administrator account.
  5. Keep the black window open while the app is in use. Close it to stop the app.

OTHER PCs IN THE LAB (students)
  The black window shows an address like  http://192.168.1.25:8000/
  Students open that address in Chrome / Edge on their PCs (same LAN or Wi-Fi).
  If Windows asks "Allow access?" the first time, click  Allow  (Private networks).

WHERE YOUR DATA IS
  app\dev.sqlite3   students, exams, answers, results
  app\media\        uploaded question images and the school logo
  BACKUP: close the app, then copy these two to a pendrive.

GOOD TO KNOW
  - This Windows edition is for preparing tests and small groups.
    For full-lab exams with 60+ students use the Ubuntu server (see app\DEPLOY.md).
  - If Windows SmartScreen warns about the files, choose "More info" -> "Run anyway",
    or right-click the ZIP -> Properties -> Unblock before unzipping.

Developer: Zainulabid - zainulabidpmna04@gmail.com
"@
Set-Content -Encoding utf8 (Join-Path $Out "HOW_TO_USE.txt") $howto

# 5. Zip it
Step "Creating $Zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
& tar.exe -a -c -f $Zip -C (Split-Path $Out -Parent) "CBT_Portable"
if ($LASTEXITCODE -ne 0) { throw "zip failed." }

$sizeFolder = [math]::Round(((Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 0)
$sizeZip = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
Write-Host ""
Write-Host "  Portable folder: $Out  ($sizeFolder MB)" -ForegroundColor Green
Write-Host "  ZIP to copy:     $Zip  ($sizeZip MB)" -ForegroundColor Green
if ($IncludeData) { Write-Host "  Includes this PC's students, exams, results and settings." -ForegroundColor Yellow }
else { Write-Host "  Fresh copy: the first start on the other PC asks for a new admin account." }
