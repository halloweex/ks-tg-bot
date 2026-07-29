#!/usr/bin/env bash
# Restore drill: proves the off-site backup can actually bring the bot back.
#
# Pulls the newest archive from the Storage Box (NOT the local copy — the point
# is to exercise the copy that survives losing this server), restores it into a
# throwaway file, and checks it is a real, populated, readable database.
#
# Touches nothing the bot uses: the live DB and the botdata volume are read-only
# here, and the restored file lives in the container's /tmp.
#
# Run it after setting backups up, and once a month after that. A backup you
# have never restored is a guess.
set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG="deploy/backup.env"
# shellcheck source=/dev/null
[ -f "$CONFIG" ] && . "$CONFIG"

SSH_PORT="${BACKUP_SSH_PORT:-23}"
SSH_KEY="${BACKUP_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE="${BACKUP_REMOTE:-}"
REMOTE_DIR="${BACKUP_REMOTE_DIR:-ks-tg-bot}"
SSH_OPTS=(-p "$SSH_PORT" -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

if [ -z "$REMOTE" ]; then
  echo "BACKUP_REMOTE is not set — nothing off-site to restore from." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; docker compose exec -T bot rm -f /tmp/restore-test.db 2>/dev/null || true' EXIT

# --- pick the newest off-site archive ---------------------------------------
LATEST="$(printf 'cd %s\nls -1\n' "$REMOTE_DIR" \
  | sftp -b - "${SSH_OPTS[@]}" "$REMOTE" 2>/dev/null \
  | grep -o 'bot_data-[0-9]\{8\}-[0-9]\{6\}\.db\.gz' | sort -r | head -1)"

if [ -z "$LATEST" ]; then
  echo "FAIL: no archives found at $REMOTE:$REMOTE_DIR" >&2
  exit 1
fi
echo "restoring from off-site: $LATEST"

printf 'cd %s\nget %s %s/\n' "$REMOTE_DIR" "$LATEST" "$TMP" \
  | sftp -b - "${SSH_OPTS[@]}" "$REMOTE" >/dev/null

# A backup that stopped happening looks exactly like a backup that works, until
# you need it — so check how old the newest one is. (GNU date; on the server.)
HUMAN="$(echo "$LATEST" | sed -E 's/bot_data-(....)(..)(..)-(..)(..)(..).*/\1-\2-\3 \4:\5:\6/')"
if TAKEN_AT="$(date -d "$HUMAN" +%s 2>/dev/null)"; then
  AGE_HOURS=$(( ( $(date +%s) - TAKEN_AT ) / 3600 ))
  echo "archive age: ${AGE_HOURS}h"
  [ "$AGE_HOURS" -le 48 ] \
    || echo "WARNING: newest off-site backup is over 48h old — is cron still running it?" >&2
fi

gunzip -c "$TMP/$LATEST" > "$TMP/restored.db"

# --- verify it inside the container (that's where sqlite3 lives) ------------
# Piped in rather than `docker compose cp`, which writes as root: sqlite needs
# to create a -shm sidecar next to a WAL database, so the file has to belong to
# the container's unprivileged user.
docker compose exec -T bot sh -c 'cat > /tmp/restore-test.db' < "$TMP/restored.db"

docker compose exec -T bot sh -c '
  set -e
  DB=/tmp/restore-test.db
  LIVE=/app/data/bot_data.db

  CHECK=$(sqlite3 "$DB" "PRAGMA integrity_check;")
  [ "$CHECK" = "ok" ] || { echo "FAIL: integrity_check -> $CHECK"; exit 1; }

  # users and orders carry data that only exists here — losing them loses it.
  for t in users orders; do
    n=$(sqlite3 "$DB" "SELECT count(*) FROM $t;") \
      || { echo "FAIL: table $t missing or unreadable"; exit 1; }
    printf "  %-18s %s rows\n" "$t" "$n"
  done

  # The rest may legitimately be absent in an archive taken before the feature
  # that added them: init_db() recreates empty tables on startup. Report, do
  # not fail — refusing an older-schema backup would deny a real restore.
  for t in opt_out broadcast_jobs broadcast_targets; do
    if n=$(sqlite3 "$DB" "SELECT count(*) FROM $t;" 2>/dev/null); then
      printf "  %-18s %s rows\n" "$t" "$n"
    else
      printf "  %-18s absent (pre-dates the feature; init_db will create it)\n" "$t"
    fi
  done

  # The restored data should look like the live data, not like an empty shell.
  RU=$(sqlite3 "$DB" "SELECT count(*) FROM users;")
  LU=$(sqlite3 "$LIVE" "SELECT count(*) FROM users;")
  echo "  users restored: $RU (live now: $LU)"
  [ "$RU" -gt 0 ] || [ "$LU" -eq 0 ] || { echo "FAIL: restored DB has no users"; exit 1; }

  # The phone is what ties a chat to its orders; a row without one is dead
  # weight after a restore. (length() avoids quote-escaping through two shells.)
  WITH_PHONE=$(sqlite3 "$DB" "SELECT count(*) FROM users WHERE length(phone) > 0;")
  echo "  users with a phone: $WITH_PHONE of $RU"
  [ "$WITH_PHONE" -eq "$RU" ] || echo "  NOTE: some restored users have no phone"

  echo "  newest user row: $(sqlite3 "$DB" "SELECT max(created_at) FROM users;")"
'

echo
echo "PASS — the off-site backup restores to a valid, populated database."
echo "To restore it for real, see 'Restoring from a backup' in deploy/DEPLOY.md."
