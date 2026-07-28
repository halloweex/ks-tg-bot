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

```bash
chmod +x /opt/ks-tg-bot/deploy/backup.sh

# Run daily at 03:30
( crontab -l 2>/dev/null; \
  echo "30 3 * * * /opt/ks-tg-bot/deploy/backup.sh" ) | crontab -

# Test it now:
/opt/ks-tg-bot/deploy/backup.sh
```

Backups live inside the `botdata` volume at `/app/data/backups` (last 14 kept).
For off-server safety, copy them to the host and pull them down:

```bash
docker compose cp bot:/app/data/backups ./backups
scp -r root@YOUR_SERVER_IP:/opt/ks-tg-bot/backups .
```

---

## Updating to new code

```bash
cd /opt/ks-tg-bot
git pull --ff-only
docker compose up -d --build     # rebuilds image, recreates container, keeps the volume
docker compose logs -f
```

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

```bash
cd /opt/ks-tg-bot
docker compose down
# copy the chosen archive into the running data dir via a throwaway container:
docker compose up -d
docker compose exec -T bot sh -c '
  cd /app/data
  rm -f bot_data.db bot_data.db-wal bot_data.db-shm
  gunzip -c backups/bot_data-YYYYMMDD-HHMMSS.db.gz > bot_data.db
'
docker compose restart
```

## Notes

- **Never bake `.env` into the image** — it's in `.dockerignore`. Secrets are
  injected at runtime via `env_file`.
- **Don't run two instances** against the same bot token (see warning above).
- The named volume survives `docker compose down`. To wipe all data you must
  explicitly run `docker compose down -v` — avoid that in production.
