FROM python:3.12-slim

# sqlite3 CLI is used for WAL-safe backups via `docker compose exec`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code and non-secret config.
# Both packages, and this list is the thing to update when a module moves.
# core/ was added the day the first module left bot/ — the image built fine
# without it and the container then died on import, because locally the repo
# root is on sys.path and inside the image it is not.
COPY core/ ./core/
COPY bot/ ./bot/
COPY config.yaml ./config.yaml

# SQLite DB lives on a mounted volume (see docker-compose.yml), not in the image.
ENV BOT_DB_PATH=/app/data/bot_data.db

# Run as an unprivileged user; pre-create the data dir so the named volume
# inherits correct ownership on first run.
RUN useradd --system --uid 1000 ksbot \
    && mkdir -p /app/data \
    && chown -R ksbot:ksbot /app
USER ksbot

CMD ["python", "-m", "bot"]
