# Deploy and rollback

Runbook for the server. Setup from scratch is in `DEPLOY.md`; this file is what
you need when something is already running.

## How a deploy works

A push to `master` (excluding `**.md` and `.planning/**`) runs
`.github/workflows/deploy.yml`:

1. **build** — GitHub Actions builds the image and pushes it to
   `ghcr.io/halloweex/ks-tg-bot` under two tags: `sha-<short>` and `latest`.
2. **deploy** — rsyncs `docker-compose.yml` and `deploy/` to `/opt/ks-tg-bot/`,
   then over ssh: `docker login ghcr.io`, `docker pull` the sha tag, write
   `IMAGE_TAG=<tag>` into `/opt/ks-tg-bot/.env`, `docker compose up -d`.
3. **health check** — the container must be `running` five seconds later *and*
   the log must contain `Bot started successfully`. Status alone is not enough:
   a bot that dies on a failed migration is `running` for a few seconds first.

Nothing is built on the production host. A broken build now fails in Actions
and leaves the running container untouched.

## Roll back

The image you were running before is still in the local store on the server, so
a rollback needs no registry access.

```sh
cd /opt/ks-tg-bot
docker images ghcr.io/halloweex/ks-tg-bot --format '{{.Tag}}\t{{.CreatedSince}}'
IMAGE_TAG=sha-abc1234 docker compose up -d
```

That is the whole procedure. `IMAGE_TAG` is also written into `.env`, so the
version sticks across a reboot and across anyone running a bare
`docker compose up -d` later — to make a rollback permanent, edit that line too:

```sh
sed -i 's|^IMAGE_TAG=.*|IMAGE_TAG=sha-abc1234|' .env
```

Otherwise the next deploy overwrites it, which is usually what you want.

### What a rollback does not undo

**Database migrations.** `init_db()` runs on every start and moves the schema
forward, never back (`bot/db.py`, `_MIGRATIONS`). Rolling the image back to a
build that predates a migration leaves the schema ahead of the code. Whether
that is survivable depends on the migration; additive ones are, a table rebuild
is not.

So: before deploying anything that adds a migration, take a backup and know
which snapshot you would restore. `deploy/backup.sh` runs nightly and
`deploy/restore-test.sh` is the rehearsal.

## Check what is running

```sh
cd /opt/ks-tg-bot
grep '^IMAGE_TAG=' .env                      # what the deploy pinned
docker inspect -f '{{.Config.Image}}' ks-tg-bot   # what is actually running
docker compose logs --tail 50
```

The two should agree. If they do not, someone ran compose by hand with a
different tag.

## Deploy by hand

Same thing without Actions, when you already know the tag:

```sh
cd /opt/ks-tg-bot
docker pull ghcr.io/halloweex/ks-tg-bot:sha-abc1234
IMAGE_TAG=sha-abc1234 docker compose up -d
```

If the pull is refused, the stored GHCR credential has expired — log in again
with a personal access token that has `read:packages`:

```sh
echo "$GITHUB_PAT" | docker login ghcr.io -u halloweex --password-stdin
```

## Never run two instances

`docker compose up -d --scale bot=2` breaks the bot in two ways at once: two
long-pollers on one token means Telegram gives the second `409 Conflict`, which
nothing in the code handles, and FSM state lives in process memory
(`bot/__main__.py:49`), so half of each conversation lands in a process that
does not know about it. The compose file has one replica by design.

This is also why the deploy restarts sequentially rather than starting the new
container before stopping the old one.
