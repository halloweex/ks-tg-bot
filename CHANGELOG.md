# Changelog

## 2026-07-29 — Cross-system order dedup + off-site backups

### Duplicate orders (KeyCRM ↔ Shopify)

KeyCRM and Shopify were fetched in parallel and both written to `orders`, keyed
`UNIQUE(source, source_order_id)`. An order placed on the website exists in
**both** systems, so the customer would see it twice — once with the KeyCRM
status, once with the Shopify one.

**Confirmed on live data**, not assumed: over the 250 most recent KeyCRM orders,
**118 (47%) came from the Shopify integration** — i.e. nearly half of all orders
would double up. The overlap is currently latent only because
`SHOPIFY_API_TOKEN` is unset (the bot runs KeyCRM-only and `shopify` is `None`);
it appears the moment Shopify credentials are configured.

The dedup key, verified against the API:

| KeyCRM field | Shopify field | Example |
|---|---|---|
| `global_source_uuid` | numeric tail of the GraphQL `id` | `13025577828684` |
| `source_uuid` | `name` (order number) | `19966` → `#19966` |

Both are `null` for manually created orders (Instagram / Telegram / expo:
`source_id` 1, 2, 5) and set only for `source_id: 4` = *Shopify Integration*,
driver `shopify`, shop `qy2jmd-ui.myshopify.com`. `global_source_uuid` was
unique across the whole sample.

- `orders.external_id` added (+ `ix_orders_external`), populated on both sides;
  `shopify_external_id()` parses the gid defensively.
- **KeyCRM wins the merge** — it is the operational system of record
  (fulfilment status, tracking code, delivery point), and it mirrors the store
  order number, so nothing is lost. The Shopify copy is dropped in-memory in
  `_do_refresh_orders()` before it is ever written.
- `upsert_orders()` also sweeps shadowed rows in SQL on every refresh, and a
  one-time backfill recovers `external_id` for Shopify rows cached before the
  column existed — so duplicates written by earlier versions heal themselves on
  the next refresh instead of lingering forever.
- Orders KeyCRM pulled from Shopify now render as `🌐 Сайт #19966` instead of
  falling back to the Instagram label.

Verified end-to-end against real KeyCRM payloads: a cache seeded the old way
(25 orders + 10 duplicates) collapses to 25 rows after one refresh, KeyCRM
statuses survive, and a second refresh is a no-op.

### Backups are now actually backups

14 archives inside the `botdata` volume die with the volume — one
`docker volume rm`, one lost disk, and the database and every copy of it go at
the same moment.

- `deploy/backup.sh` rewritten: snapshot → **integrity check** (a corrupt or
  suspiciously empty snapshot is deleted, not kept) → copy out of the volume →
  **rsync to a Hetzner Storage Box** over SSH (port 23) → prune all three
  locations to 14. It **exits non-zero until the off-site target is
  configured**, so a half-finished setup surfaces in cron mail.
- Remote pruning goes through `sftp`, deliberately **not** `rsync --delete` —
  an emptied local directory must never be able to erase the off-site history.
- `deploy/restore-test.sh` (new) — the honest drill: pulls the newest archive
  **from the Storage Box**, restores it to a throwaway file, and checks
  integrity, table presence and that the data is populated. Warns if the newest
  archive is over 48h old. Scheduled monthly alongside the daily backup.
- `deploy/backup.env.example` (new) — Storage Box sub-account config.
- Two restore bugs fixed in the runbook: `docker compose cp` writes as **root**,
  which would leave `ksbot` unable to open its own database, and the stale
  `-wal`/`-shm` sidecars must be removed or SQLite replays them over the
  restored file. Restore now pipes through `docker compose run` as the container
  user. Added a from-scratch server rebuild procedure.

### Known follow-ups

- `orders` is still keyed `UNIQUE(source, source_order_id)` without `chat_id`.
  Two Telegram accounts sharing one phone (a family) would move rows between
  each other rather than duplicate them. Needs a table rebuild; not urgent at
  current scale.
- The restore drill validates the archive, not a full bring-up. Once a year,
  restore onto a scratch VPS and watch the bot actually start.

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
