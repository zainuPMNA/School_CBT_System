#!/usr/bin/env bash
# ===========================================================================
#  CBT lab server — one-command install / update for Ubuntu Server 24.04 LTS
#
#  First install AND every later update use the same command, run from the
#  unpacked release folder:
#
#      sudo bash deploy/setup_server.sh --school "Springfield Public School"
#
#  Options
#    --school "Name"     School name printed on slips/scorecards (first install)
#    --lan-cidr LIST     Networks allowed through the firewall, comma-separated:
#                        the wired labs AND any Wi-Fi network, e.g.
#                        --lan-cidr 192.168.1.0/24,192.168.50.0/24
#                        (default: every subnet the server itself is on)
#    --no-same-ip-relogin
#                        Turn OFF "re-login from the same IP replaces the old
#                        session". Use when students connect through a Wi-Fi
#                        router that does NAT (all devices share one IP).
#    --skip-apt          Don't install OS packages (already installed / offline)
#    --skip-firewall     Don't touch ufw
#
#  Layout created
#    /opt/cbt/app        code (read-only for the service)
#    /opt/cbt/app/.env   production settings + secrets (generated once)
#    /opt/cbt/venv       Python virtualenv
#    /var/lib/cbt/media  uploaded question images
#    /var/log/cbt        Django log file
#    /var/backups/cbt    nightly DB + media backups (14 days)
# ===========================================================================
set -euo pipefail

APP_ROOT=/opt/cbt
APP_DIR=$APP_ROOT/app
VENV=$APP_ROOT/venv
DATA_DIR=/var/lib/cbt
LOG_DIR=/var/log/cbt
BACKUP_DIR=/var/backups/cbt
SERVICE_USER=cbt
SERVICE_GROUP=www-data
DB_NAME=cbt_db
DB_USER=cbt_user

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHOOL_NAME="School CBT Portal"
LAN_CIDR=""
SKIP_APT=0
SKIP_FIREWALL=0
SAME_IP_RELOGIN=""

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --school)        SCHOOL_NAME="${2:?}"; shift 2 ;;
    --lan-cidr)      LAN_CIDR="${2:?}"; shift 2 ;;
    --no-same-ip-relogin) SAME_IP_RELOGIN="False"; shift ;;
    --skip-apt)      SKIP_APT=1; shift ;;
    --skip-firewall) SKIP_FIREWALL=1; shift ;;
    -h|--help)       sed -n '2,32p' "$0"; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run with sudo:  sudo bash deploy/setup_server.sh"
[[ -f "$SRC_DIR/manage.py" ]] || die "Run this from the unpacked release folder (manage.py not found in $SRC_DIR)."
if ! grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null; then
  warn "This script is written for Ubuntu 24.04 LTS; continuing anyway."
fi

# ---------------------------------------------------------------------------
log "1/10  Operating-system packages"
# ---------------------------------------------------------------------------
PACKAGES=(python3-venv python3-pip postgresql postgresql-contrib nginx ufw rsync curl)
if [[ $SKIP_APT -eq 0 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  if ! apt-get update -qq || ! apt-get install -y -qq "${PACKAGES[@]}"; then
    die "apt could not install: ${PACKAGES[*]}
       The server needs these Ubuntu packages once. Either connect it to the
       internet for this step, or install the .deb files from USB (see
       DEPLOY.md, 'Offline OS packages'), then re-run with --skip-apt."
  fi
fi
for cmd in python3 psql nginx rsync curl; do
  command -v "$cmd" >/dev/null || die "'$cmd' is missing. Install: ${PACKAGES[*]}"
done
python3 -c 'import sys; assert sys.version_info >= (3, 12)' || die "Python 3.12+ required."
python3 -m venv --help >/dev/null 2>&1 || die "python3-venv is missing."
ok "python3 $(python3 -c 'import platform; print(platform.python_version())'), $(psql --version | awk '{print $1, $3}'), nginx present"

# ---------------------------------------------------------------------------
log "2/10  Service user and directories"
# ---------------------------------------------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_ROOT" --no-create-home --shell /usr/sbin/nologin \
          --gid "$SERVICE_GROUP" "$SERVICE_USER"
  ok "created system user '$SERVICE_USER'"
fi
install -d -m 0755 -o root -g root "$APP_ROOT" "$APP_DIR"
install -d -m 2750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$DATA_DIR" "$DATA_DIR/media"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$LOG_DIR" "$BACKUP_DIR"
chmod 2755 "$DATA_DIR/media" && chmod 0755 "$DATA_DIR"   # nginx reads images
ok "directories ready"

# ---------------------------------------------------------------------------
log "3/10  Application code -> $APP_DIR"
# ---------------------------------------------------------------------------
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  rsync -a --delete \
    --exclude '.env' --exclude '.env.*.bak' --exclude '.git/' --exclude '.claude/' \
    --exclude '__pycache__/' --exclude '*.pyc' --exclude 'dev.sqlite3' \
    --exclude 'bin/' --exclude 'wheelhouse/' --exclude 'staticfiles/' \
    --exclude 'media/' --exclude 'backups/' --exclude 'logs/' --exclude 'venv/' --exclude '.venv/' \
    "$SRC_DIR/" "$APP_DIR/"
  ok "code synced from $SRC_DIR"
fi
chown -R root:root "$APP_DIR"
find "$APP_DIR" -type d -exec chmod 0755 {} +
find "$APP_DIR" -type f -exec chmod 0644 {} +
# collectstatic (run as the service user) must own everything it rewrites.
mkdir -p "$APP_DIR/staticfiles" "$APP_DIR/logs"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$APP_DIR/staticfiles" "$APP_DIR/logs"

# ---------------------------------------------------------------------------
log "4/10  Python virtualenv and packages"
# ---------------------------------------------------------------------------
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
if [[ -d "$SRC_DIR/wheelhouse" ]] && ls "$SRC_DIR"/wheelhouse/*.whl >/dev/null 2>&1; then
  "$VENV/bin/pip" install --quiet --no-index --find-links "$SRC_DIR/wheelhouse" -r "$APP_DIR/requirements.txt"
  ok "installed from bundled wheelhouse (offline)"
else
  warn "no wheelhouse/ in the release — downloading from PyPI (needs internet)"
  "$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
fi
"$VENV/bin/python" -c "import django, psycopg, gunicorn; print('    Django', django.get_version())"

# ---------------------------------------------------------------------------
log "5/10  Production settings (.env)"
# ---------------------------------------------------------------------------
ENV_FILE="$APP_DIR/.env"
# The release's own .env is never copied (rsync excludes it). If a development
# .env (DEBUG / SQLite) was placed here by hand, set it aside and regenerate.
if [[ -f "$ENV_FILE" ]] && grep -qE '^(DJANGO_DEBUG=True|DB_ENGINE=sqlite)' "$ENV_FILE"; then
  mv "$ENV_FILE" "$ENV_FILE.dev.$(date +%s).bak"
  warn "moved a development .env out of the way"
fi
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$SERVER_IP" ]] || SERVER_IP="127.0.0.1"
if [[ ! -f "$ENV_FILE" ]]; then
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
  DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
  cat > "$ENV_FILE" <<EOF
# Generated by setup_server.sh on $(date -Is). Keep secret.
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=$SECRET_KEY
DJANGO_ALLOWED_HOSTS=$SERVER_IP,$(hostname),$(hostname).local,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://$SERVER_IP,http://$(hostname),http://$(hostname).local
DJANGO_TIME_ZONE=Asia/Kolkata
DJANGO_MEDIA_ROOT=$DATA_DIR/media
DJANGO_LOG_DIR=$LOG_DIR
USE_HTTPS=False

DB_ENGINE=postgresql
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_HOST=127.0.0.1
DB_PORT=5432
DB_POOL=True
DB_POOL_MIN=2
DB_POOL_MAX=6

SESSION_COOKIE_AGE=21600
CBT_SCHOOL_NAME=$SCHOOL_NAME
CBT_SERVER_URL=http://$SERVER_IP/
CBT_SINGLE_SESSION_MODE=BLOCK
CBT_ALLOW_SAME_IP_RELOGIN=True
CBT_REQUIRE_FULLSCREEN=True
CBT_MAX_VIOLATIONS=0
CBT_TRUST_X_REAL_IP=True
CBT_PASSWORD_HASH_ITERATIONS=100000
EOF
  ok "generated $ENV_FILE (server address http://$SERVER_IP/)"
else
  ok "keeping existing $ENV_FILE"
fi
# Every IPv4 address the server has (wired + Wi-Fi) must be accepted by Django,
# so the app answers on whichever network a student uses. Added, never removed.
ALL_IPS="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | paste -sd, -)"
python3 - "$ENV_FILE" "$ALL_IPS" "$SAME_IP_RELOGIN" <<'PY'
import sys
path, ips, relogin = sys.argv[1], [i for i in sys.argv[2].split(",") if i], sys.argv[3]
lines = open(path, encoding="utf-8").read().splitlines()
def merge(key, values):
    for n, line in enumerate(lines):
        if line.startswith(key + "="):
            have = [v for v in line.split("=", 1)[1].split(",") if v]
            added = [v for v in values if v not in have]
            if added:
                lines[n] = key + "=" + ",".join(have + added)
                print(f"    + {key}: {', '.join(added)}")
            return
    lines.append(key + "=" + ",".join(values))
def set_value(key, value):
    for n, line in enumerate(lines):
        if line.startswith(key + "="):
            if line != f"{key}={value}":
                lines[n] = f"{key}={value}"
                print(f"    + {key}={value}")
            return
    lines.append(f"{key}={value}")
    print(f"    + {key}={value}")
merge("DJANGO_ALLOWED_HOSTS", ips)
merge("DJANGO_CSRF_TRUSTED_ORIGINS", [f"http://{ip}" for ip in ips])
if relogin:
    set_value("CBT_ALLOW_SAME_IP_RELOGIN", relogin)
open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
chown "$SERVICE_USER:$SERVICE_GROUP" "$ENV_FILE"
# Backups made from the admin "Backup" page (and nightly by cbt_backup) live here, never under /media.
grep -q '^CBT_BACKUP_DIR=' "$ENV_FILE" || echo "CBT_BACKUP_DIR=$BACKUP_DIR/app" >> "$ENV_FILE"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$BACKUP_DIR/app"
chmod 0600 "$ENV_FILE"
DB_PASSWORD="$(grep -E '^DB_PASSWORD=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
[[ -n "$DB_PASSWORD" ]] || die "DB_PASSWORD missing in $ENV_FILE"

# ---------------------------------------------------------------------------
log "6/10  PostgreSQL: tuning, role and database"
# ---------------------------------------------------------------------------
systemctl enable --now postgresql >/dev/null
PG_MAIN="$(ls -d /etc/postgresql/*/main 2>/dev/null | sort -V | tail -n1)"
[[ -n "$PG_MAIN" ]] || die "PostgreSQL config directory not found under /etc/postgresql"
RAM_MB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
SHARED=$(( RAM_MB / 4 ));  (( SHARED > 4096 )) && SHARED=4096
CACHE=$(( RAM_MB * 7 / 10 ))
MAINT=$(( RAM_MB / 16 ));  (( MAINT > 1024 )) && MAINT=1024; (( MAINT < 64 )) && MAINT=64
install -d "$PG_MAIN/conf.d"
sed -e "s/@SHARED_BUFFERS@/${SHARED}MB/" -e "s/@EFFECTIVE_CACHE@/${CACHE}MB/" -e "s/@MAINT_WORK_MEM@/${MAINT}MB/" \
    "$APP_DIR/deploy/postgresql/cbt-tuning.conf" > "$PG_MAIN/conf.d/cbt-tuning.conf"
grep -qE "^\s*include_dir\s*=\s*'conf.d'" "$PG_MAIN/postgresql.conf" || echo "include_dir = 'conf.d'" >> "$PG_MAIN/postgresql.conf"
systemctl restart postgresql
for _ in $(seq 1 20); do sudo -u postgres pg_isready -q && break; sleep 1; done
sudo -u postgres psql -q -v ON_ERROR_STOP=1 --set=dbuser="$DB_USER" --set=dbname="$DB_NAME" --set=pw="$DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'dbuser') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'dbuser')\gexec
ALTER ROLE :"dbuser" WITH LOGIN PASSWORD :'pw';
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8'' TEMPLATE template0', :'dbname', :'dbuser')
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'dbname')\gexec
ALTER DATABASE :"dbname" OWNER TO :"dbuser";
SQL
ok "PostgreSQL $(basename "$(dirname "$PG_MAIN")"): shared_buffers=${SHARED}MB, effective_cache_size=${CACHE}MB (RAM ${RAM_MB}MB)"

# ---------------------------------------------------------------------------
log "7/10  Django: checks, migrations, static files"
# ---------------------------------------------------------------------------
manage() { (cd "$APP_DIR" && sudo -u "$SERVICE_USER" -H env PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" manage.py "$@"); }
manage check --fail-level ERROR
manage migrate --noinput
manage collectstatic --noinput --clear -v 0
ok "database migrated, static files collected"
manage check --deploy --fail-level CRITICAL 2>&1 | sed 's/^/    /' | grep -vE 'HSTS|SSL_REDIRECT|COOKIE_SECURE|^\s*$' || true

# ---------------------------------------------------------------------------
log "8/10  systemd services"
# ---------------------------------------------------------------------------
install -m 0644 "$APP_DIR"/deploy/systemd/*.service "$APP_DIR"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet cbt.service cbt-autosubmit.timer cbt-maintenance.timer
systemctl restart cbt.service
systemctl restart cbt-autosubmit.timer cbt-maintenance.timer
for _ in $(seq 1 20); do [[ -S /run/cbt/gunicorn.sock ]] && break; sleep 1; done
systemctl is-active --quiet cbt.service || { journalctl -u cbt -n 40 --no-pager; die "cbt.service failed to start"; }
ok "cbt.service running ($(pgrep -fc 'gunicorn.*cbt_project' || true) gunicorn processes)"

# ---------------------------------------------------------------------------
log "9/10  Nginx"
# ---------------------------------------------------------------------------
install -m 0644 "$APP_DIR/deploy/nginx/cbt.conf" /etc/nginx/sites-available/cbt
ln -sf /etc/nginx/sites-available/cbt /etc/nginx/sites-enabled/cbt
rm -f /etc/nginx/sites-enabled/default
nginx -t -q || die "nginx configuration test failed"
systemctl enable --now nginx >/dev/null
systemctl reload nginx
ok "nginx serving http://$SERVER_IP/"

# ---------------------------------------------------------------------------
log "10/10 Firewall (LAN only)"
# ---------------------------------------------------------------------------
if [[ $SKIP_FIREWALL -eq 0 ]]; then
  if [[ -z "$LAN_CIDR" ]]; then
    # Every subnet the server is on (wired NIC and, if present, its Wi-Fi NIC).
    LAN_CIDR="$(ip -o -f inet addr show scope global | awk '{print $4}' \
      | python3 -c "import ipaddress,sys; print(','.join(sorted({str(ipaddress.ip_interface(l.strip()).network) for l in sys.stdin if l.strip()})))")"
  fi
  if [[ -z "$LAN_CIDR" ]]; then
    warn "could not detect the LAN subnet — firewall left unchanged (use --lan-cidr)"
  else
    IFS=',' read -r -a NETWORKS <<< "$LAN_CIDR"
    for net in "${NETWORKS[@]}"; do
      python3 -c "import ipaddress,sys; ipaddress.ip_network(sys.argv[1], strict=False)" "$net" 2>/dev/null \
        || die "--lan-cidr: '$net' is not a valid network (example: 192.168.1.0/24)"
    done
    ufw --force default deny incoming >/dev/null
    ufw --force default allow outgoing >/dev/null
    for net in "${NETWORKS[@]}"; do
      ufw allow from "$net" to any port 80 proto tcp comment 'CBT web' >/dev/null
      ufw allow from "$net" to any port 22 proto tcp comment 'SSH from LAN' >/dev/null
    done
    ufw --force enable >/dev/null
    ok "ufw: HTTP (80) and SSH (22) allowed from ${LAN_CIDR//,/, } only; everything else blocked"
  fi
else
  warn "firewall skipped (--skip-firewall)"
fi

# ---------------------------------------------------------------------------
log "Health check"
# ---------------------------------------------------------------------------
if curl -fsS --max-time 10 http://127.0.0.1/healthz/ >/dev/null; then
  ok "http://127.0.0.1/healthz/ OK (app + database)"
else
  warn "health check failed — see: journalctl -u cbt -n 50   and   tail /var/log/nginx/cbt.error.log"
fi

ADMIN_COUNT="$(manage shell -c "from accounts.models import User; print(User.objects.filter(role='ADMIN').count())" 2>/dev/null | tail -n1)"
if [[ "$ADMIN_COUNT" == "0" ]]; then
  if [[ -t 0 ]]; then
    log "Create the first administrator account"
    manage createsuperuser
  else
    warn "No admin account yet. Create one with:"
    echo "      cd $APP_DIR && sudo -u $SERVICE_USER $VENV/bin/python manage.py createsuperuser"
  fi
fi

cat <<EOF

  ┌──────────────────────────────────────────────────────────────┐
     CBT server is ready:   http://$SERVER_IP/
     Admin dashboard:       http://$SERVER_IP/dashboard/
     Settings file:         $ENV_FILE
     Logs:                  journalctl -u cbt -f
     Backups (19:00 daily): $BACKUP_DIR
  └──────────────────────────────────────────────────────────────┘
  Give the server a FIXED IP (see DEPLOY.md) so login slips stay valid.
EOF
