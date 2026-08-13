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
→ create one with **SSH enabled**, **readonly off**, and its own base directory
(`ks-tg-bot`).

The sub-account is not ceremony. The key has to be passphrase-less because cron
uses it unattended, and it sits on the machine the backups exist to survive — so
whoever takes that machine gets the key. A sub-account confines them to one
directory. The main account hands them every backup on the box, including those
of any other project sharing it.

Two things the console does not tell you:

- **The sub-account has its own hostname.** `u123456-sub1` answers at
  `u123456-sub1.your-storagebox.de`. The main account's hostname is not a
  synonym for it and will refuse the login.
- **There is no SSH-key field for a sub-account.** That field exists only for
  the main account. A sub-account reads its keys from `authorized_keys` inside
  its own directory, on the box.

So: make the key on the server, put the public half on the **main** account in
the console, then use the main account to drop the same public half into the
sub-account's directory. No sub-account password is needed for any of it.

```bash
ssh-keygen -t ed25519 -N "" -C "ks-backup@$(hostname)" -f /root/.ssh/storagebox_ed25519
cat /root/.ssh/storagebox_ed25519.pub    # paste into the MAIN account's SSH keys

printf -- '-mkdir ks-tg-bot/.ssh\nput /root/.ssh/storagebox_ed25519.pub ks-tg-bot/.ssh/authorized_keys\nchmod 700 ks-tg-bot/.ssh\nchmod 600 ks-tg-bot/.ssh/authorized_keys\n' \
  | sftp -b - -P 23 -i /root/.ssh/storagebox_ed25519 u123456@u123456.your-storagebox.de
```

Storage Box speaks SSH/rsync/sftp on **port 23**, not 22. Now check the
sub-account, the way cron will meet it:

```bash
printf 'pwd\nls -1\n' | sftp -b - -P 23 -i /root/.ssh/storagebox_ed25519 \
  -o BatchMode=yes u123456-sub1@u123456-sub1.your-storagebox.de
```

`BatchMode=yes` is the point of that check: it forbids falling back to a
password. A listing means cron will get in. A password prompt means the key is
not installed — and an interactive login would have hidden that by asking you
for the password you happen to know.

Note what `pwd` returns: `/home`. The sub-account sees its own directory as the
root, which is why `BACKUP_REMOTE_DIR` is `.` — naming `ks-tg-bot` again would
nest it inside itself.

Finally, take the key back off the **main** account. It was needed only to reach
into the sub-account's directory, and while it stays there the confinement is
decorative: whoever takes the server logs in as the main account and deletes
every backup on the box.

```bash
: > /tmp/empty
printf 'put /tmp/empty /home/.ssh/authorized_keys\n' \
  | sftp -b - -P 23 -i /root/.ssh/storagebox_ed25519 -o BatchMode=yes \
        u123456@u123456.your-storagebox.de
```

**Do this last, and only once every project sharing the box has its own
sub-account** — installing a sub-account's `authorized_keys` requires main
account access, so removing it first means going back through the console. This
box is shared with `key-api-bot`, which had its own `backup.env` pointing at the
main account; removing the key before moving it would have stopped its backups
silently.

Confirm it took: the main-account login must now answer `Permission denied`,
while both sub-accounts keep working.

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
  echo "0 4 * * 1 /opt/ks-tg-bot/deploy/restore-test.sh"   # weekly drill, Mondays
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
Nothing the bot uses is touched. Run it after setup; the cron above then runs it
weekly, which is what `docs/architecture.md` §10 asks for and costs a download of
a few megabytes. It also warns if the newest archive is over 48h old — the case
where backups stopped and nobody noticed because the old ones are still there.

The drill reports its own failures to the same Telegram chat, naming the stage
it died at. It has to: in cron its stderr goes nowhere, and a weekly check that
cannot speak is indistinguishable from a weekly check that passes.

To confirm that alerting path, force a failure. An environment variable in front
of the command will **not** do it — both scripts source `backup.env` after
reading the environment, so the file wins. Move it aside:

```bash
mv deploy/backup.env /root/backup.env.away
BACKUP_REMOTE=nobody@invalid BACKUP_SSH_KEY=/root/.ssh/storagebox_ed25519 \
    deploy/restore-test.sh                      # expect exit 1 and an alert
mv /root/backup.env.away deploy/backup.env
head -2 deploy/backup.env                       # confirm it is back
```

Retention is 14 archives in each location. Pruning the Storage Box goes through
sftp rather than `rsync --delete`, so an emptied local directory can never
propagate and wipe the off-site history.

### 6.4 Snapshots: the copy the server cannot destroy

Everything above still trusts a key that lives on the production machine. Turn
on **automatic snapshots** on the Storage Box (console → Snapshots; daily,
keeping 10) — the box takes them itself, and `/.zfs` is read-only over SSH, so
the server's key cannot delete or alter them. That is the only layer that
survives the server being compromised, or a bug in our own pruning.

Schedule them **after** the nightly backup lands. The server runs on UTC and
`backup.sh` fires at 03:30, so 05:00 UTC leaves margin; a snapshot at midnight
would every day capture the box as it was before that day's archive arrived.

Restoring from one is a file copy, **not** the console's restore button — that
one rolls the whole box back and deletes every newer snapshot. Read the archive
straight out of the snapshot tree instead:

```bash
sftp -P 23 -i /root/.ssh/storagebox_ed25519 u123456@u123456.your-storagebox.de
sftp> ls /home/.zfs/snapshot
sftp> get /home/.zfs/snapshot/<snap>/ks-tg-bot/bot_data-<stamp>.db.gz
```

That path needs the **main** account: `/home/.zfs` sits above the sub-account's
directory, so the confinement that protects the snapshots also hides them from
the key on the server. And per §6.1 the server holds no main-account credential
at all — deliberately, since a key that can read the snapshots can also delete
them.

So a snapshot restore starts in the console: add a key there for the main
account, pull the file, and take the key off again. That is a worse afternoon
than typing one command, and it is the point — this is the copy that has to
survive the machine being owned.

BX11 allows 10 automatic snapshots and 10 manual ones, counted separately. The
manual slots are what `README.md` means by "know which snapshot you would
restore" before deploying a migration: take one first, and automatic rotation
cannot age it out from under you.

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
