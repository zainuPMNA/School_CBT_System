#!/usr/bin/env bash
# Nightly backup: PostgreSQL dump + question images, keeps 14 days.
# Runs as user "cbt" from cbt-maintenance.service; can also be run by hand:
#   sudo -u cbt bash /opt/cbt/app/deploy/backup.sh
# Restore a dump:
#   sudo -u postgres pg_restore --clean --if-exists -d cbt_db /var/backups/cbt/cbt_db_YYYYmmdd_HHMM.dump
set -euo pipefail

APP_DIR="/opt/cbt/app"
BACKUP_DIR="/var/backups/cbt"
KEEP_DAYS=14
STAMP="$(date +%Y%m%d_%H%M)"

# Read DB settings from the app's .env (KEY=VALUE lines).
get_env() { grep -E "^$1=" "$APP_DIR/.env" | tail -n1 | cut -d= -f2-; }
DB_NAME="$(get_env DB_NAME)"; DB_USER="$(get_env DB_USER)"
DB_HOST="$(get_env DB_HOST)"; DB_PORT="$(get_env DB_PORT)"
export PGPASSWORD="$(get_env DB_PASSWORD)"

mkdir -p "$BACKUP_DIR"
pg_dump -Fc -h "${DB_HOST:-127.0.0.1}" -p "${DB_PORT:-5432}" -U "$DB_USER" -f "$BACKUP_DIR/${DB_NAME}_${STAMP}.dump" "$DB_NAME"
tar -czf "$BACKUP_DIR/media_${STAMP}.tar.gz" -C /var/lib/cbt media

find "$BACKUP_DIR" -type f \( -name '*.dump' -o -name 'media_*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete
echo "Backup written to $BACKUP_DIR (${DB_NAME}_${STAMP}.dump)"
