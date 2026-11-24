<#
  Build an OFFLINE release for the Ubuntu 24.04 lab server — run on this
  Windows PC (needs internet once):

      powershell -ExecutionPolicy Bypass -File deploy\package_for_server.ps1

  Produces  dist\cbt_release.tar.gz  containing:
    - the project code (without your local .env, dev database, logs, uploads)
    - wheelhouse\  = every Python package pre-downloaded as LINUX wheels for
                     Python 3.12 (what Ubuntu 24.04 ships), so the server
                     never needs internet for pip.
#>
param(
    [string]$PythonVersion = "3.12",
    [switch]$SkipWheels
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Dist = Join-Path $Root "dist"
$Wheelhouse = Join-Path $Root "wheelhouse"
New-Item -ItemType Directory -Force $Dist | Out-Null

function Find-Python {
    foreach ($candidate in @("py", "python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "Python not found. Install Python from python.org first."
}

if (-not $SkipWheels) {
    $py = Find-Python
    Write-Host "==> Downloading Linux (manylinux x86_64, CPython $PythonVersion) wheels into wheelhouse\" -ForegroundColor Cyan
    if (Test-Path $Wheelhouse) { Remove-Item -Recurse -Force $Wheelhouse }
    $abi = "cp" + $PythonVersion.Replace(".", "")
    & $py -m pip download -r requirements.txt -d $Wheelhouse `
        --only-binary=:all: --implementation cp --python-version $PythonVersion `
        --abi $abi --abi abi3 --abi none `
        --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 `
        --platform manylinux2014_x86_64 --platform any --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip download failed (see messages above)." }
    $count = (Get-ChildItem $Wheelhouse -Filter *.whl).Count
    Write-Host "    $count wheels downloaded" -ForegroundColor Green
}

# Sanity: the built CSS / vendor JS must be present (server has no internet).
foreach ($f in @("static\css\app.css", "static\vendor\alpinejs\alpine.min.js", "static\vendor\htmx\htmx.min.js", "static\vendor\katex\katex.min.js")) {
    if (-not (Test-Path $f)) { throw "Missing $f - run: bash scripts/bundle_static.sh" }
}

$Archive = Join-Path $Dist "cbt_release.tar.gz"
$Stage = Join-Path $Dist "cbt_release"
if (Test-Path $Archive) { Remove-Item -Force $Archive }
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
Write-Host "==> Staging a clean copy (no .env, dev database, logs, uploads)" -ForegroundColor Cyan
$excludeDirs = @("dist", "bin", "staticfiles", ".claude", ".git", "venv", ".venv", "media\questions", "backups") |
    ForEach-Object { Join-Path $Root $_ }
& robocopy.exe $Root $Stage /E /NFL /NDL /NJH /NJS /NP `
    /XD @excludeDirs "__pycache__" `
    /XF ".env" "*.sqlite3" "*.pyc" "*.log" "*.log.*" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with code $LASTEXITCODE." }

Write-Host "==> Creating $Archive" -ForegroundColor Cyan
# Windows 10/11 ship bsdtar as tar.exe; the archive's top folder is cbt_release/.
& tar.exe -czf $Archive -C $Dist cbt_release
if ($LASTEXITCODE -ne 0) { throw "tar failed." }
Remove-Item -Recurse -Force $Stage

$sizeMb = [math]::Round((Get-Item $Archive).Length / 1MB, 1)
Write-Host "    $Archive ($sizeMb MB)" -ForegroundColor Green
Write-Host ""
Write-Host "Next: copy it to the server (USB stick, or over the LAN):" -ForegroundColor Yellow
Write-Host "    scp dist\cbt_release.tar.gz youruser@SERVER-IP:~/"
Write-Host "Then on the server:"
Write-Host "    tar -xzf cbt_release.tar.gz && cd cbt_release && sudo bash deploy/setup_server.sh --school `"Your School Name`""
