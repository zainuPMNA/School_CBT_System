<#
  Start the CBT app on THIS Windows PC — for trying it out, preparing exams,
  and small trials with a few PCs on the same LAN / Wi-Fi.
  (Real exams with full labs: use the Ubuntu server, see DEPLOY.md.)

      powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1

  Options:
      -Port 8080      use another port (default 8000)
      -LocalOnly      only this PC can open it (no LAN / Wi-Fi access)
      -NoBrowser      don't open the browser automatically

  Stop it with Ctrl+C.
#>
param(
    [int]$Port = 8000,
    [switch]$LocalOnly,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
# Portable edition: a bundled Python sits next to the app folder (..\python\python.exe).
$PortablePy = Join-Path (Split-Path -Parent $Root) "python\python.exe"
$Portable = Test-Path $PortablePy
if ($Portable) { $Py = $PortablePy }
$AppUrl = "http://localhost:$Port/"
$HealthUrl = "http://127.0.0.1:$Port/healthz/"

function Test-AppRunning {
    try { Invoke-WebRequest -UseBasicParsing $HealthUrl -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

# 0. Already running (e.g. double-clicked twice)? Just open the browser.
if (Test-AppRunning) {
    Write-Host "  The CBT app is already running at $AppUrl" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $AppUrl }
    exit 0
}

# 1. Python environment (first run only; the portable edition already has everything)
if (-not $Portable -and -not (Test-Path $Py)) {
    Write-Host "==> First run: creating .venv and installing packages (needs internet, ~2 min)" -ForegroundColor Cyan
    $launcher = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
    & $launcher -m venv .venv
    & $Py -m pip install -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
}

# 2. Local development settings (first run only). Production uses its own .env on Ubuntu.
if (-not (Test-Path ".env")) {
    @(
        "# LOCAL DEVELOPMENT ONLY (this Windows PC).",
        "DJANGO_DEBUG=True",
        "DB_ENGINE=sqlite",
        "CBT_SCHOOL_NAME=School CBT Portal"
    ) | Set-Content -Encoding utf8 .env
    Write-Host "    created .env for local use" -ForegroundColor Green
}

# 3. Which addresses other devices can use to reach this PC
$ips = @()
if (-not $LocalOnly) {
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Sort-Object -Property InterfaceMetric |
        Select-Object -ExpandProperty IPAddress)
}
$hosts = @("localhost", "127.0.0.1") + $ips
# Real environment variables win over .env, so these apply to this run only.
$env:DJANGO_ALLOWED_HOSTS = $hosts -join ","
$env:DJANGO_CSRF_TRUSTED_ORIGINS = ($hosts | ForEach-Object { "http://${_}:$Port" }) -join ","
$env:CBT_TRUST_X_REAL_IP = "False"   # no Nginx in front here; use the real client IP
$mainUrl = if ($ips.Count) { "http://$($ips[0]):$Port/" } else { "http://localhost:$Port/" }
$env:CBT_SERVER_URL = $mainUrl       # printed on login slips

# 4. Database tables, and an admin account if there is none yet
& $Py manage.py migrate --noinput -v 0
if ($LASTEXITCODE -ne 0) { throw "migrate failed." }
$admins = (& $Py manage.py shell -c "from accounts.models import User; print(User.objects.filter(role='ADMIN').count())" 2>$null | Select-Object -Last 1)
if ($admins -eq "0") {
    Write-Host "==> Create the administrator account" -ForegroundColor Cyan
    & $Py manage.py createsuperuser
}

# 5. Start
$bind = if ($LocalOnly) { "127.0.0.1:$Port" } else { "0.0.0.0:$Port" }
Write-Host ""
Write-Host "  CBT app is starting" -ForegroundColor Green
Write-Host "    On this PC:        http://localhost:$Port/"
foreach ($ip in $ips) { Write-Host "    From other PCs:    http://${ip}:$Port/" }
Write-Host "    Admin dashboard:   http://localhost:$Port/dashboard/"
if (-not $LocalOnly) {
    Write-Host "  If Windows asks 'Allow access?', allow it on Private networks so other PCs can connect." -ForegroundColor Yellow
}
Write-Host "  Stop with Ctrl+C, or close this window."
Write-Host ""
if (-not $NoBrowser) {
    # A hidden helper waits until the server answers, then opens the browser once.
    $opener = "for (`$i = 0; `$i -lt 90; `$i++) { try { Invoke-WebRequest -UseBasicParsing '$HealthUrl' -TimeoutSec 2 | Out-Null; Start-Process '$AppUrl'; break } catch { Start-Sleep -Seconds 1 } }"
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $opener | Out-Null
}
& $Py manage.py runserver $bind
