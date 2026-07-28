#!/usr/bin/env bash
# WAL-safe daily backup of the bot's SQLite database, run inside the container.
# sqlite3 ".backup" takes a consistent snapshot even while the bot writes and
# correctly folds in the -wal file. Keeps the 14 most recent archives in the
# botdata volume (/app/data/backups). Run from anywhere; cd's to the repo root.
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root (where docker-compose.yml lives)

STAMP="$(date +%Y%m%d-%H%M%S)"

docker compose exec -T bot sh -c "
  set -e
  mkdir -p /app/data/backups
  sqlite3 /app/data/bot_data.db \".backup '/app/data/backups/bot_data-$STAMP.db'\"
  gzip -f /app/data/backups/bot_data-$STAMP.db
  ls -1t /app/data/backups/bot_data-*.db.gz | tail -n +15 | xargs -r rm -f
"

echo "Backup done: bot_data-$STAMP.db.gz (inside the botdata volume)"
echo "To copy it to the host:  docker compose cp bot:/app/data/backups ./backups"
