#!/usr/bin/env bash
# Daily backup of the bot's SQLite database.
#
#   1. WAL-safe snapshot inside the container (sqlite3 ".backup" folds in -wal)
#   2. integrity check on the snapshot — a corrupt copy is deleted, not kept
#   3. copy out of the Docker volume onto the host filesystem
#   4. push off-site to a Hetzner Storage Box
#   5. prune all three locations to BACKUP_RETAIN archives
#
# Copies inside the botdata volume are not backups: `docker volume rm`, a wiped
# server or a lost disk takes them with the database. Step 4 is the one that
# matters, so the script FAILS (loudly, via cron mail) until it is configured.
#
# Config: deploy/backup.env (git-ignored, see backup.env.example).
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root (where docker-compose.yml lives)

CONFIG="deploy/backup.env"
# shellcheck source=/dev/null
[ -f "$CONFIG" ] && . "$CONFIG"

RETAIN="${BACKUP_RETAIN:-14}"
HOST_DIR="${BACKUP_HOST_DIR:-$PWD/backups}"
SSH_PORT="${BACKUP_SSH_PORT:-23}"          # Hetzner Storage Box speaks SSH on 23
SSH_KEY="${BACKUP_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE="${BACKUP_REMOTE:-}"                # u123456@u123456.your-storagebox.de
REMOTE_DIR="${BACKUP_REMOTE_DIR:-ks-tg-bot}"

STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="bot_data-$STAMP.db.gz"
SSH_OPTS=(-p "$SSH_PORT" -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

# --- 1-2. snapshot, verify, compress, prune (inside the container) -----------
docker compose exec -T bot sh -c "
  set -e
  mkdir -p /app/data/backups
  SNAP=/app/data/backups/bot_data-$STAMP.db
  sqlite3 /app/data/bot_data.db \".backup '\$SNAP'\"

  # A snapshot that fails its own integrity check is worse than no snapshot,
  # because it looks like protection. Refuse to keep it.
  CHECK=\$(sqlite3 \"\$SNAP\" 'PRAGMA integrity_check;')
  if [ \"\$CHECK\" != 'ok' ]; then
    echo \"integrity check FAILED: \$CHECK\" >&2
    rm -f \"\$SNAP\"
    exit 1
  fi

  # Sanity-check contents too: a technically valid but empty snapshot of a
  # non-empty database means the backup is not backing anything up.
  USERS=\$(sqlite3 \"\$SNAP\" 'SELECT count(*) FROM users;')
  LIVE=\$(sqlite3 /app/data/bot_data.db 'SELECT count(*) FROM users;')
  echo \"users in snapshot: \$USERS (live: \$LIVE)\"
  if [ \"\$LIVE\" -gt 0 ] && [ \"\$USERS\" -eq 0 ]; then
    echo 'snapshot has no users but the live DB does — refusing to keep it' >&2
    rm -f \"\$SNAP\"
    exit 1
  fi

  gzip -f \"\$SNAP\"
  ls -1t /app/data/backups/bot_data-*.db.gz | tail -n +$((RETAIN + 1)) | xargs -r rm -f
"

# --- 3. out of the volume, onto the host ------------------------------------
mkdir -p "$HOST_DIR"
docker compose cp "bot:/app/data/backups/$NAME" "$HOST_DIR/$NAME"
ls -1t "$HOST_DIR"/bot_data-*.db.gz | tail -n +$((RETAIN + 1)) | xargs -r rm -f
echo "local copy: $HOST_DIR/$NAME"

# --- 4. off-site ------------------------------------------------------------
if [ -z "$REMOTE" ]; then
  cat >&2 <<'EOF'

BACKUP_REMOTE is not set — this backup exists only on this server.
A lost volume, a wrong `docker volume rm`, or a dead disk destroys the database
and every copy of it at the same moment. Configure deploy/backup.env
(see deploy/backup.env.example) and re-run.
EOF
  exit 1
fi

rsync --archive --quiet \
  -e "ssh ${SSH_OPTS[*]}" \
  "$HOST_DIR/$NAME" "$REMOTE:$REMOTE_DIR/"

# Prune the remote by count. Storage Box offers no usable remote shell, so this
# goes through sftp; deliberately NOT `rsync --delete`, which would mirror a
# wiped local directory onto the off-site copy and erase the whole history.
REMOTE_FILES="$(printf 'cd %s\nls -1\n' "$REMOTE_DIR" \
  | sftp -b - "${SSH_OPTS[@]}" "$REMOTE" 2>/dev/null \
  | grep -o 'bot_data-[0-9]\{8\}-[0-9]\{6\}\.db\.gz' | sort -r)"

OLD="$(printf '%s\n' "$REMOTE_FILES" | tail -n +$((RETAIN + 1)))"
if [ -n "$OLD" ]; then
  { printf 'cd %s\n' "$REMOTE_DIR"; printf 'rm %s\n' $OLD; } \
    | sftp -b - "${SSH_OPTS[@]}" "$REMOTE" >/dev/null
fi

SEEN="$(printf '%s\n' "$REMOTE_FILES" | grep -c . || true)"
KEPT=$(( SEEN < RETAIN ? SEEN : RETAIN ))
echo "off-site: $REMOTE:$REMOTE_DIR/$NAME ($KEPT archives held, keeping $RETAIN)"
echo "Backup done: $NAME"
echo "Verify it can actually be restored:  deploy/restore-test.sh"
