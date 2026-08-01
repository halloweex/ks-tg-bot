# Deploying ks-tg-bot with Docker on a Hetzner VPS

The bot uses Telegram **long-polling**, so no domain, open ports, or TLS are
needed — only outbound HTTPS. A single small VPS (e.g. Hetzner **CX22**) is
plenty for the current scale.

> **One instance only.** Telegram allows a single `getUpdates` poller per bot
> token. Never run the bot on the server *and* your laptop at the same time —
> you'll get `409 Conflict`. Stop the local one before the server takes over.

**What persists:** the SQLite database lives in a Docker **named volume**
(`botdata`), mounted at `/app/data`. Rebuilding or updating the image does **not**
touch it. The image itself is stateless.

---

## 1. Create the server

- Hetzner Cloud → new server → **Ubuntu 24.04**, type **CX22**.
- Add your SSH key. Note the IP.

## 2. Install Docker (as root)

```bash
ssh root@YOUR_SERVER_IP

apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh      # installs Docker Engine + compose plugin
docker --version && docker compose version   # sanity check
```

## 3. Get the code

The repo is private — give the server read access with a **read-only deploy key**:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

Add that public key in GitHub → repo **Settings → Deploy keys → Add** (read-only),
then clone:

```bash
cd /opt
git clone git@github.com:halloweex/ks-tg-bot.git ks-tg-bot
cd ks-tg-bot
```

## 4. Configuration

`config.yaml` is baked into the image. Create the secrets file `.env` (git-ignored,
read by `docker compose` via `env_file`):

```bash
cp .env.example .env
nano .env      # fill BOT_TOKEN, KEYCRM_API_KEY, ADMIN_USER_IDS, and optionally
               # SHOPIFY_* / NOVAPOSHTA_API_KEY
chmod 600 .env
```

## 5. Build and start

```bash
docker compose up -d --build

docker compose ps               # State should be "running"
docker compose logs -f          # expect "Bot started successfully"; Ctrl-C to detach
```

`restart: always` means Docker restarts the bot on crash and on server reboot.

## 6. Daily database backups

> **Copies next to the database are not backups.** Archives inside the `botdata`
> volume die with the volume — one `docker volume rm` in the wrong terminal, one
> failed disk, one rebuilt server, and the database and all 14 "backups" are
> gone together. The off-site copy is the backup; the rest is convenience.

`deploy/backup.sh` snapshots the DB (WAL-safe), **verifies the snapshot**,
copies it out of the volume onto the host, and pushes it to a Hetzner Storage
Box. It exits non-zero until the off-site target is configured, and messages the
main admin on Telegram on any failure — see 6.2 — so a half-finished setup
cannot sit there unnoticed.

### 6.1 Create the Storage Box target

In the Hetzner console: **Storage Box** (BX11, ~1 EUR/month) → **Sub-accounts**
→ create one with **SSH enabled**, access **Read/Write**, its own directory.

On the server, make a passphrase-less key for cron and upload the public half to
that sub-account:

```bash
ssh-keygen -t ed25519 -N "" -f /root/.ssh/storagebox_ed25519
cat /root/.ssh/storagebox_ed25519.pub     # paste into the sub-account's SSH keys
```

Storage Box uses **port 23** for SSH/rsync/sftp. Check the login works:

```bash
sftp -P 23 -i /root/.ssh/storagebox_ed25519 u123456-sub1@u123456.your-storagebox.de
```

### 6.2 Configure and schedule

```bash
cd /opt/ks-tg-bot
cp deploy/backup.env.example deploy/backup.env
nano deploy/backup.env          # BACKUP_REMOTE, BACKUP_SSH_KEY, …
chmod 600 deploy/backup.env
chmod +x deploy/backup.sh deploy/restore-test.sh

deploy/backup.sh                # run it now — must end with "Backup done"
```

```bash
( crontab -l 2>/dev/null
  echo "30 3 * * * /opt/ks-tg-bot/deploy/backup.sh"
  echo "0 4 1 * * /opt/ks-tg-bot/deploy/restore-test.sh"   # monthly drill
) | crontab -
```

**Failures are reported over Telegram, not mail.** This box has no MTA, so cron's
stderr goes nowhere — an `exit 1` on its own is completely silent, and because
the local steps succeed first, fresh files in `backups/` look like working
backups. On any failure the script messages the main admin (the first id in
`ADMIN_USER_IDS`, override with `BACKUP_ALERT_CHAT_ID`) using `BOT_TOKEN` from
`.env`. Until the off-site target is configured that message arrives every night
— which is the point.

### 6.3 Prove it restores

```bash
deploy/restore-test.sh
```

It pulls the **newest off-site archive** (not the local copy — the point is to
exercise what survives losing this server), restores it to a throwaway file,
and checks integrity, table presence, and that the data is actually populated.
Nothing the bot uses is touched. Run it after setup and monthly thereafter;
it also warns if the newest archive is over 48h old.

To confirm the alerting path itself works, run the backup with a deliberately
wrong target — `BACKUP_REMOTE=nobody@invalid deploy/backup.sh` — and check the
Telegram message arrives.

Retention is 14 archives in each location. Pruning the Storage Box goes through
sftp rather than `rsync --delete`, so an emptied local directory can never
propagate and wipe the off-site history.

---

## Updating to new code

**Automatic.** A push to `master` builds the image in GitHub Actions, pushes it
to GHCR and makes the server pull it. Nothing is built on this machine any more.
The runbook — including how to roll back in one command — is in
[README.md](README.md).

The instructions that used to be here (`git pull --ff-only` followed by
`docker compose up -d --build`) are not just outdated, they now break things:
the compose file names a registry image pinned by `IMAGE_TAG`, so `--build`
would put a locally built image under a tag that means something else, and the
working tree on the server is no longer the source of what runs.

## Common operations

| Task | Command (run from /opt/ks-tg-bot) |
|------|-----------------------------------|
| Live logs | `docker compose logs -f` |
| Last 100 lines | `docker compose logs --tail 100` |
| Restart | `docker compose restart` |
| Stop | `docker compose down` (keeps the volume) |
| Start | `docker compose up -d` |
| Status | `docker compose ps` |
| Shell in container | `docker compose exec bot sh` |

## Restoring from a backup

Stop the bot first — replacing the file under a running poller corrupts it.

```bash
cd /opt/ks-tg-bot
. deploy/backup.env

# 1. fetch the archive you want (skip if restoring from the host copy)
sftp -P "$BACKUP_SSH_PORT" -i "$BACKUP_SSH_KEY" "$BACKUP_REMOTE" <<'EOF'
cd ks-tg-bot
ls -1
EOF
sftp -P "$BACKUP_SSH_PORT" -i "$BACKUP_SSH_KEY" "$BACKUP_REMOTE":ks-tg-bot/bot_data-YYYYMMDD-HHMMSS.db.gz .

# 2. put it in place with the bot stopped
docker compose stop bot
gunzip -c bot_data-YYYYMMDD-HHMMSS.db.gz \
  | docker compose run --rm --no-deps -T --entrypoint sh bot -c \
      'cat > /app/data/bot_data.db && rm -f /app/data/bot_data.db-wal /app/data/bot_data.db-shm'

# 3. back up
docker compose start bot
docker compose logs -f          # expect "Database initialized" + "Bot started successfully"
```

Two details that decide whether this works:

- **Remove the `-wal`/`-shm` sidecars.** They belong to the *old* database file
  and SQLite would replay them over the restored one.
- **Write the file from inside the container, not with `docker compose cp`.**
  `cp` writes as root; the bot runs as the unprivileged `ksbot` and would be
  unable to open its own database for writing.

### Rebuilding the server from scratch

Nothing here depends on the old machine — that is the point of the off-site
copy. New VPS → steps 1-5 → step 6.1/6.2 with the *same* Storage Box
sub-account → the restore above. `.env` is the only thing not in the backup;
keep those secrets somewhere you can reach without this server.

## Notes

- **Never bake `.env` into the image** — it's in `.dockerignore`. Secrets are
  injected at runtime via `env_file`.
- **Don't run two instances** against the same bot token (see warning above).
- The named volume survives `docker compose down`. To wipe all data you must
  explicitly run `docker compose down -v` — avoid that in production.
