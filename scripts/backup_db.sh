#!/usr/bin/env bash
# Dump the compose Postgres into backups/content_zavod_<date>.sql.gz and keep the newest N
# (first argument, default 14). Run from anywhere; it works relative to the repo root.
#
# Cron example (every night at 03:00, keep 14):
#   0 3 * * * /opt/content-zavod/scripts/backup_db.sh 14 >> /var/log/content-zavod-backup.log 2>&1
# (the log is outside backups/: cron opens it before this script gets to create that directory)
set -euo pipefail
# Dumps hold every Plan, Article and stored token: readable by the owner only.
umask 077

keep="${1:-14}"
if ! [[ "$keep" =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: $0 [N]  (N = how many newest backups to keep, positive integer)" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
mkdir -p backups

file="backups/content_zavod_$(date +%Y-%m-%d_%H%M%S).sql.gz"
tmp="$file.partial"
trap 'rm -f "$tmp"' EXIT

docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$tmp"
mv "$tmp" "$file"
echo "backup written: $file"

# Rotation: only files matching our own name pattern, newest first, drop everything past N.
ls -1t backups/content_zavod_*.sql.gz | tail -n +"$((keep + 1))" | while read -r old; do
  rm -f -- "$old"
  echo "old backup removed: $old"
done
