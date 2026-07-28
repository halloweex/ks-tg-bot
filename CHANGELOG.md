# Changelog

## 2026-07-28 — Delivery, deployment, and production hardening

This session shipped the delivery feature, put the bot on a server with
CI/CD, and closed a set of correctness/security/reliability gaps found while
reasoning about real scale (10k+ users, promo-push bursts).

### Features

- **Nova Poshta delivery tracking** (`feat(05)`) — new `NovaPoshtaClient` +
  `delivery` handler showing per-order TTN status, with a CRM-data fallback
  when the NP key is absent. Conditionally injected; KeyCRM-only mode preserved.
- **Broadcast confirmation buttons** — inline ✅ Так / ❌ Ні under the confirm
  prompt (typed так/yes/да kept as a fallback).
- **International phone entry** — a shared `normalize_phone()` accepts numbers
  from any country, with or without a leading `+` (Ukrainian local formats
  still map to +380).

### Reliability / scale

- **SQLite WAL + busy_timeout** via a shared `_connect()` helper, plus
  `ix_orders_chat_id` — concurrent order-refresh writers no longer hit
  "database is locked" during activity bursts.
- **Bounded background refresh** — a 5-min freshness TTL (`get_last_sync_time`)
  and a `Semaphore(10)` stop a post-broadcast burst from hammering
  KeyCRM/Shopify.
- **Durable, resumable broadcasts** — `broadcast_jobs` + `broadcast_targets`
  tables track per-recipient status; `resume_broadcasts()` finishes any job
  interrupted by a restart/redeploy, sending only still-pending recipients.
  `403 Forbidden` → mark blocked + `opt_out` (prunes dead chat_ids);
  `429` → honour `retry_after`.
- **Background-task tracking** (`bot/tasks.py`) — `spawn()` keeps a strong
  reference and logs exceptions (raw `asyncio.create_task` could be GC'd
  mid-request and swallowed errors); `drain()` runs on shutdown.

### Security

- **Phone spoofing / IDOR fixed** — a user could bind *any* phone (typed, or a
  forwarded contact card) to their chat and read another person's orders +
  delivery address. Now the phone is set only from the user's **own** contact,
  shared via `request_contact` and verified with
  `contact.user_id == from_user.id`. Manual entry is refused in onboarding
  **and** settings. See `own_contact_phone()` / `share_phone_kb()`.

### Deployment & CI/CD

- **Docker** — `Dockerfile` (python:3.12-slim, non-root, sqlite3 for backups),
  `docker-compose.yml` (`restart: always`, `env_file`, named volume `botdata`
  for the SQLite DB), `BOT_DB_PATH` so the DB lives on the volume.
- **Hetzner VPS** (`89.167.20.30`, Ubuntu 24.04) — long-polling, no inbound
  ports needed. Runbook in `deploy/DEPLOY.md`; WAL-safe daily backups via
  `deploy/backup.sh` (cron).
- **GitHub Actions auto-deploy** (`.github/workflows/deploy.yml`) — every push
  to `master` rsyncs code to the server (excluding `.env`/DB/volume) and runs
  `docker compose up -d --build` + a health check. Secrets: `DEPLOY_SSH_KEY`,
  `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_KNOWN_HOSTS`.

### Verified but unchanged

- **Support relay** stores its reply-target (`chat_id`) in the forwarded
  message text in the support chat, not in process memory — it already
  survives restarts.

### Known follow-ups (not blocking)

- FSM state is in-memory (`MemoryStorage`): an *unconfirmed* broadcast draft is
  lost on redeploy (the running job is durable). Move FSM to Redis if needed.
- Per-service httpx clients are created per call; `NovaPoshtaClient.track_many`
  is sequential — reuse a pooled client and `gather` when traffic grows.
- Broadcast still sends in-process (~20 msg/sec, single coroutine). Fine now;
  consider a dedicated worker past ~50k recipients.
- Support-reply UX: managers should reply to the metadata note (has `chat_id`);
  replying to the forwarded message relies on `forward_from`, which is `None`
  under Telegram forward-privacy.
