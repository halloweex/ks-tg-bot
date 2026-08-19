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

# shellcheck source=deploy/notify.sh
. deploy/notify.sh

SSH_PORT="${BACKUP_SSH_PORT:-23}"
SSH_KEY="${BACKUP_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE="${BACKUP_REMOTE:-}"
REMOTE_DIR="${BACKUP_REMOTE_DIR:-ks-tg-bot}"
HOST_DIR="${BACKUP_HOST_DIR:-$PWD/backups}"   # where the pass is recorded
SSH_OPTS=(-p "$SSH_PORT" -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
# sftp takes the port as -P; -p means "preserve permissions". Every sftp call
# here was silently returning nothing, so LATEST came back empty and the drill
# exited 1 without a word.
SFTP_OPTS=(-P "$SSH_PORT" -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

# Deliberately above the alerting trap: backup.sh already reports an
# unconfigured off-site target every night, and its message says what to do.
# A second one calling it a failed restore would describe an attempt that never
# happened, and alerts that misdescribe things teach you to skim them.
if [ -z "$REMOTE" ]; then
  echo "BACKUP_REMOTE is not set — nothing off-site to restore from." >&2
  exit 1
fi

TMP="$(mktemp -d)"
STEP_FILE="$(mktemp)"
step() { printf '%s' "$1" >"$STEP_FILE"; }

# On EXIT rather than ERR: this script fails both by errexit and by an explicit
# `exit 1` after a check, and an ERR trap does not fire on the second kind.
# Without this the drill was silent in cron — no MTA on the box, see notify.sh —
# which is the failure mode it exists to catch, aimed at itself.
on_exit() {
    local rc=$?
    set +e   # cleanup must not be able to change the outcome being reported
    rm -rf "$TMP"
    docker compose exec -T bot rm -f /tmp/restore-test.db 2>/dev/null || true
    if [ "$rc" -ne 0 ]; then
        # Careful about what this claims: the nightly backup may well still be
        # running fine. What just failed is the proof that it comes back.
        notify "$(printf '%s\n\n%s\n\n%s\n\n%s\n\n%s' \
            "❌ Restore drill FAILED — the off-site backup did not come back" \
            "Failed at: $(cat "$STEP_FILE" 2>/dev/null || echo '?')" \
            "Backups may still be running; what is unproven is that they restore." \
            "Run it by hand to see why:  deploy/restore-test.sh" \
            "$(_where)")"
    fi
    rm -f "$STEP_FILE"
    exit "$rc"
}
trap on_exit EXIT

# --- pick the newest off-site archive ---------------------------------------
step "listing archives on the off-site target"
LATEST="$(printf 'cd %s\nls -1\n' "$REMOTE_DIR" \
  | sftp -b - "${SFTP_OPTS[@]}" "$REMOTE" 2>/dev/null \
  | grep -o 'bot_data-[0-9]\{8\}-[0-9]\{6\}\.db\.gz' | sort -r | head -1)"

if [ -z "$LATEST" ]; then
  echo "FAIL: no archives found at $REMOTE:$REMOTE_DIR" >&2
  exit 1
fi
echo "restoring from off-site: $LATEST"

step "downloading $LATEST"
printf 'cd %s\nget %s %s/\n' "$REMOTE_DIR" "$LATEST" "$TMP" \
  | sftp -b - "${SFTP_OPTS[@]}" "$REMOTE" >/dev/null

# A backup that stopped happening looks exactly like a backup that works, until
# you need it — so check how old the newest one is. (GNU date; on the server.)
HUMAN="$(echo "$LATEST" | sed -E 's/bot_data-(....)(..)(..)-(..)(..)(..).*/\1-\2-\3 \4:\5:\6/')"
if TAKEN_AT="$(date -d "$HUMAN" +%s 2>/dev/null)"; then
  AGE_HOURS=$(( ( $(date +%s) - TAKEN_AT ) / 3600 ))
  echo "archive age: ${AGE_HOURS}h"
  [ "$AGE_HOURS" -le 48 ] \
    || echo "WARNING: newest off-site backup is over 48h old — is cron still running it?" >&2
fi

step "unpacking $LATEST"
gunzip -c "$TMP/$LATEST" > "$TMP/restored.db"

# --- verify it inside the container (that's where sqlite3 lives) ------------
# Piped in rather than `docker compose cp`, which writes as root: sqlite needs
# to create a -shm sidecar next to a WAL database, so the file has to belong to
# the container's unprivileged user.
step "restoring and checking the database"
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

# Leave a trace, because passing is otherwise silent and silence is what hides a
# drill that stopped running. backup.sh reads the last line of this file and
# complains once it goes stale; it runs nightly, so it notices within a day.
#
# Appended rather than overwritten: the history is worth a line a week, and it
# answers "was it passing before?" without anyone having kept a log.
#
# Failing to record must not be reported as a failed restore — the restore just
# succeeded. A missed line ages the file, and the nightly check says so itself.
mkdir -p "$HOST_DIR" 2>/dev/null || true
printf '%s %s %s\n' "$(date +%s)" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$LATEST" \
  >> "$HOST_DIR/restore-drill.log" \
  || echo "WARNING: the drill passed but could not record it in $HOST_DIR" >&2

echo
echo "PASS — the off-site backup restores to a valid, populated database."
echo "To restore it for real, see 'Restoring from a backup' in deploy/DEPLOY.md."
