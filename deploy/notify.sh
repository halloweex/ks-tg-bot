#!/usr/bin/env bash
# Telegram alerting for the backup scripts. Sourced, never executed.
#
# This channel exists because the box has no MTA: cron's stderr goes nowhere,
# so an `exit 1` on its own is completely silent — and silence from a backup
# script is indistinguishable from success. Every scheduled job under deploy/
# that can fail unattended has to be able to say so here.
#
# Both callers `cd` to the repo root before sourcing this, so `.env` resolves.
# Credentials are read from `.env` rather than duplicated into `backup.env`,
# which keeps the token in exactly one place and out of the backup config.

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
# state the data is in, why, and what to do — not carry a log tail the reader
# has to parse. English, like the rest of the operational surface. Every one of
# them ends with this, so "which box, when" is never a guess.
_where() { printf '%s · %s' "$(hostname)" "$(date '+%F %H:%M')"; }
