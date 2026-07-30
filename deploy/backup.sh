#!/usr/bin/env bash
# Daily backup of the bot's SQLite database.
#
#   1. WAL-safe snapshot inside the container (sqlite3 ".backup" folds in -wal)
#   2. integrity check on the snapshot — a corrupt copy is deleted, not kept
#   3. copy out of the Docker volume onto the host filesystem
#   4. push off-site to a Hetzner Storage Box
#   5. prune all three locations to BACKUP_RETAIN archives
#
# Any failure — including step 4 not being configured — sends a Telegram message
# to the main admin. That channel exists because this box has no MTA: cron's
# stderr goes nowhere, so an `exit 1` on its own is silent. Worse, steps 1-3
# succeed first, so fresh files in the host directory look like working backups
# while the only copy that survives losing this server is never written.
#
# Config: deploy/backup.env (git-ignored, see backup.env.example).
#
# -E matters: without errtrace the ERR trap is not inherited by functions, so a
# command failing inside run_backup would abort the shell without ever firing
# the alert.
set -Eeuo pipefail

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

# Off-site not configured: a real failure, but a different one from "the backup
# did not happen", and the alert wording depends on telling them apart.
EX_UNCONFIGURED=78

LOG="$(mktemp)"
STEP_FILE="$(mktemp)"
trap 'rm -f "$LOG" "$STEP_FILE"' EXIT

# run_backup's output is captured into $LOG, and the ERR trap fires while that
# redirection is still in effect — so the handler must write to the real stdout
# and stderr, kept here as fds 3 and 4, or its output disappears into the log it
# is trying to report.
exec 3>&1 4>&2

step() { printf '%s' "$1" >"$STEP_FILE"; }

# --- failure alerting -------------------------------------------------------
# Credentials are read straight from .env rather than duplicated into
# backup.env, and the notification runs outside the logged section so the token
# never reaches the log file.

_env_value() { grep -m1 "^$1=" .env 2>/dev/null | cut -d= -f2- | tr -d '\r'; }

alert_chat_id() {
    # The main admin: an explicit override, else the first id in ADMIN_USER_IDS.
    if [ -n "${BACKUP_ALERT_CHAT_ID:-}" ]; then
        printf '%s' "$BACKUP_ALERT_CHAT_ID"
        return
    fi
    _env_value ADMIN_USER_IDS | cut -d, -f1 | tr -d '[:space:]'
}

notify() {
    local text="$1" token chat
    token="$(_env_value BOT_TOKEN)"
    chat="$(alert_chat_id)"
    if [ -z "$token" ] || [ -z "$chat" ]; then
        echo "cannot alert: BOT_TOKEN or ADMIN_USER_IDS missing from .env" >&2
        return 0
    fi
    # Never let a failed notification change the script's own outcome.
    curl -sS -m 15 -o /dev/null \
        --data-urlencode "chat_id=$chat" \
        --data-urlencode "text=$text" \
        "https://api.telegram.org/bot${token}/sendMessage" || \
        echo "alert delivery failed" >&2
}

# An alert is read on a phone. It has to answer three things immediately: what
# state the data is in, why, and what to do — not carry a log tail the reader has
# to parse. English, like the rest of the operational surface.

_where() { printf '%s · %s' "$(hostname)" "$(date '+%F %H:%M')"; }

# Meaningful lines only: transport chatter ("Copying …/Copied …") and the
# snapshot's own success line say nothing about why a later step failed.
_error_lines() {
    grep -vE '(Copying|Copied|users in snapshot)' "$LOG" 2>/dev/null \
        | grep -v '^[[:space:]]*$' | tail -n 4
}

notify_offsite_unconfigured() {
    notify "$(printf '%s\n\n%s\n%s\n\n%s\n\n%s\n%s\n\n%s' \
        "⚠️ Backup: off-site copy is not set up" \
        "Snapshot taken and verified — it is on this server only:" \
        "  $HOST_DIR/$NAME" \
        "/opt and the Docker volume are one disk, so losing that disk loses the database and every copy of it." \
        "To fix, on the server:" \
        "  cp deploy/backup.env.example deploy/backup.env, fill in BACKUP_REMOTE and BACKUP_SSH_KEY, then run deploy/backup.sh" \
        "$(_where)")"
}

notify_failure() {
    local step errors
    step="$(cat "$STEP_FILE" 2>/dev/null || echo '?')"
    errors="$(_error_lines)"
    notify "$(printf '%s\n\n%s\n%s\n\n%s\n\n%s' \
        "❌ Backup FAILED — no usable copy was made" \
        "Failed at: $step" \
        "${errors:-(no error output captured)}" \
        "The database was NOT backed up. Check: docker compose logs bot" \
        "$(_where)")"
}

# --- the actual work --------------------------------------------------------
# Runs with output captured so a failure alert can carry the real error text.

run_backup() {
    step "snapshot + integrity check"
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

    step "copy out of the volume"
    mkdir -p "$HOST_DIR"
    docker compose --progress quiet cp "bot:/app/data/backups/$NAME" "$HOST_DIR/$NAME"
    ls -1t "$HOST_DIR"/bot_data-*.db.gz | tail -n +$((RETAIN + 1)) | xargs -r rm -f
    echo "local copy: $HOST_DIR/$NAME"

    step "off-site push"
    if [ -z "$REMOTE" ]; then
        echo "BACKUP_REMOTE is not set — this backup exists only on this server." >&2
        # Distinct code so the alert can say "snapshot fine, shipping not set up"
        # instead of "backup failed", which would be false and teach the reader
        # to ignore these messages.
        return "$EX_UNCONFIGURED"
    fi

    rsync --archive --quiet \
        -e "ssh ${SSH_OPTS[*]}" \
        "$HOST_DIR/$NAME" "$REMOTE:$REMOTE_DIR/"

    step "prune off-site"
    # Storage Box offers no usable remote shell, so this goes through sftp;
    # deliberately NOT `rsync --delete`, which would mirror a wiped local
    # directory onto the off-site copy and erase the whole history.
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
}

# Deliberately NOT `if run_backup; then`: bash disables errexit for the whole
# body of a function invoked in a condition, so a failed snapshot would carry on
# to the next step and the alert would name the wrong one — or none at all.
# An ERR trap keeps fail-fast semantics inside run_backup.
on_error() {
    local rc=$?
    # Disarm first: with errtrace inherited, any non-zero command inside this
    # handler would re-enter it and spin forever.
    trap - ERR
    set +e
    {
        cat "$LOG"
        if [ "$rc" -eq "$EX_UNCONFIGURED" ]; then
            notify_offsite_unconfigured
        else
            notify_failure
        fi
    } >&4 2>&4
    exit "$rc"
}
trap on_error ERR

run_backup >"$LOG" 2>&1

cat "$LOG"
echo "Backup done: $NAME"
echo "Verify it can actually be restored:  deploy/restore-test.sh"
