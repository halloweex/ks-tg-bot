# Stack Research

**Domain:** Telegram e-commerce bot (beauty shop) — order tracking, FAQ, live support, broadcasts
**Researched:** 2026-03-02
**Confidence:** HIGH — all versions verified directly from PyPI metadata and dry-run install

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.14.2 (already installed) | Runtime | Already set up; aiogram 3.25 explicitly classifies Python 3.14 as supported |
| aiogram | 3.25.0 | Telegram bot framework | Async-first, FSM built-in, router-based handlers, active maintenance (3.x series), explicit Python 3.14 support confirmed in package metadata; the dominant choice for new Python Telegram bots as of 2025-2026 |
| gspread | 6.2.1 | Google Sheets read/write | The de-facto Python library for Sheets API v4; simple row/cell API, handles auth via google-auth, works with service accounts (no browser needed — critical for server deployment) |
| google-auth | 2.48.0 | Google API authentication | Required by gspread; handles service account JWT flow; Python 3.14 explicitly supported |
| python-dotenv | 1.2.2 | Environment variable loading | Keeps secrets (bot token, service account path) out of source code; zero-dependency pattern |
| loguru | 0.7.3 | Structured logging | Drop-in replacement for stdlib logging with better formatting, file rotation, and async safety; removes boilerplate from logging setup |
| apscheduler | 3.11.2 | Scheduled broadcasts | Runs periodic tasks (broadcast sends, Sheet refresh) inside the async event loop; integrates with asyncio without a separate worker process |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| aiohttp | 3.13.3 | Async HTTP client | Auto-installed as aiogram's transport layer; do not install separately — let aiogram pin its own version (requires <3.14, >=3.9.0) |
| aiofiles | 25.1.0 | Async file I/O | Auto-installed by aiogram; use if reading local FAQ config files asynchronously |
| pydantic | 2.12.5 | Config and data validation | Auto-installed by aiogram; use to model the bot's config (brand name, sheet ID, support chat ID) — validates at startup and gives clear errors |
| cachetools | 7.0.1 | In-memory TTL cache | Cache Google Sheets lookups to avoid per-message API calls; TTLCache with 60-300s TTL prevents rate limit hits without stale data risks |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| ruff | 0.15.4 | Linter + formatter | Replaces flake8 + black + isort in one tool; extremely fast, PyPI version confirmed current |
| pytest | Latest stable | Test runner | Pair with pytest-asyncio for testing aiogram handlers |
| pytest-asyncio | 1.3.0 | Async test support | Required for testing async aiogram handler functions |

---

## Installation

```bash
# Core runtime dependencies
pip install aiogram==3.25.0 gspread==6.2.1 google-auth==2.48.0 python-dotenv==1.2.2 loguru==0.7.3 apscheduler==3.11.2 cachetools==7.0.1

# Dev dependencies
pip install ruff==0.15.4 pytest pytest-asyncio==1.3.0
```

**Confirmed clean install** — `pip install --dry-run aiogram==3.25.0 gspread==6.2.1` resolves with no conflicts on Python 3.14.2.

**Note on aiohttp:** Do not pin aiohttp separately. aiogram 3.25.0 requires `aiohttp<3.14,>=3.9.0` and will pull in 3.13.3 automatically. Pinning to 3.13.x in requirements is fine to lock the version, but let aiogram control the upper bound.

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| aiogram 3.x | python-telegram-bot 22.6 | PTB is better documented for beginners and has built-in job queue via APScheduler; choose it if the team is more comfortable with synchronous patterns or the project grows to need webhook-based deployment on a platform like Railway |
| aiogram 3.x | telebot (pyTelegramBotAPI) | Only for throwaway scripts — no FSM, no async-first design, not suitable for production bots with conversation state |
| gspread | google-api-python-client (raw) | Use raw client only if you need Sheets v4 features not exposed by gspread (batch updates, protected ranges); for this project gspread is sufficient |
| apscheduler 3.x | aiogram-broadcaster 0.6.8 | aiogram-broadcaster is a dedicated broadcast library for aiogram that handles rate limiting and delivery tracking; consider it if broadcast volume grows beyond manual iteration; for MVP, apscheduler is simpler |
| loguru | stdlib logging | Use stdlib logging only if integrating with an existing logging infrastructure (e.g., cloud log aggregator that expects LogRecord format) |
| cachetools (in-process) | Redis + aiocache | Use Redis only when deploying multiple bot instances; single-process deployment (VPS with one worker) does not need Redis |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| aiogram 2.x | EOL; completely different API from 3.x (callback-based vs router-based); migration is a rewrite | aiogram 3.25.0 |
| telebot / pyTelegramBotAPI | No FSM support, polling-only, synchronous internals — breaks under any load; conversation state must be managed manually | aiogram 3.x |
| python-telegram-bot without `[ext]` extras | The base package lacks job queue and persistence; installs feel deceptive | `pip install python-telegram-bot[ext]` — but we're using aiogram anyway |
| gspread 5.x | Major API changes in 6.x (authentication model, worksheet methods); online tutorials mostly show 5.x patterns — follow 6.x docs | gspread 6.2.1 |
| polling in production on shared hosting | Shared hosting kills long-running processes; polling requires a persistent process | VPS or container deployment with long-polling or webhook |
| SQLite / any DB for primary storage | The requirement is explicit: Google Sheets is the data source; adding a DB creates a sync problem | gspread + in-memory cache |
| threading for concurrency | aiogram is async; mixing threads creates race conditions in FSM state | asyncio throughout |

---

## Stack Patterns by Variant

**Deployment: VPS (recommended for this project):**
- Use `asyncio.run(dp.start_polling(bot))` — long-polling, no nginx needed
- Single process, no load balancer
- systemd service or Docker for process management

**Deployment: Webhook (if migrating to serverless later):**
- Use aiogram's `webhook` setup with aiohttp web server
- Requires a public HTTPS URL (Telegram requirement)
- More complex setup; not needed for MVP

**Google Sheets auth: Service Account (recommended):**
- Create a Google Cloud service account, share the Sheet with the service account email
- JSON key file referenced via environment variable
- No browser/OAuth flow needed — works headless on a server

**Google Sheets auth: OAuth user credentials (avoid for this project):**
- Requires browser flow to obtain tokens
- Token expires and must be refreshed
- Not suitable for server-side bots

**Broadcast pattern:**
- Store subscribed user chat_ids in a separate Sheet tab or a simple JSON file
- APScheduler triggers send_message to each chat_id
- Use `asyncio.sleep(0.05)` between sends to stay under Telegram's 30 msg/sec global limit

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| aiogram 3.25.0 | Python 3.10–3.14, aiohttp 3.9–3.13.x, pydantic 2.4–2.12 | Confirmed from package metadata; aiohttp upper bound is <3.14 |
| gspread 6.2.1 | Python >=3.8 (works on 3.14), google-auth >=1.12.0 | Classifiers only list through 3.11, but requires-python is >=3.8 and no C extensions — runs fine on 3.14 |
| google-auth 2.48.0 | Python 3.8–3.14 | Explicitly classifies 3.14 |
| apscheduler 3.11.2 | Python >=3.8; asyncio scheduler requires `asyncio` executor | Use `AsyncIOScheduler` from apscheduler, not `BackgroundScheduler` |
| python-telegram-bot 22.6 | Python 3.10–3.14 | Alternative considered; requires httpcore>=1.0.9 on Python 3.14 specifically |
| pydantic 2.12.5 | Python 3.8–3.14 | Used by aiogram internally; safe to use for config models too |

---

## Sources

- PyPI live metadata — aiogram 3.25.0 wheel METADATA file (verified 2026-03-02): `Requires-Python: <3.15,>=3.10`, classifies Python 3.14
- PyPI live metadata — gspread 6.2.1 wheel METADATA file (verified 2026-03-02): `Requires-Python: >=3.8`
- PyPI live metadata — google-auth 2.48.0 wheel METADATA file (verified 2026-03-02): classifies Python 3.14
- PyPI live metadata — python-telegram-bot 22.6 wheel METADATA file (verified 2026-03-02): classifies Python 3.14, requires httpcore on 3.14
- PyPI live metadata — python-dotenv 1.2.2, loguru 0.7.3, apscheduler 3.11.2, ruff 0.15.4, pytest-asyncio 1.3.0 (all version-checked via `pip index versions` 2026-03-02)
- Dependency resolution — `pip install --dry-run aiogram==3.25.0 gspread==6.2.1` confirmed no conflicts on Python 3.14.2 (run 2026-03-02)
- Training knowledge (MEDIUM confidence) — aiogram ecosystem patterns (FSM, router-based handlers, webhook vs polling trade-offs); flagged where not verified by package metadata

---
*Stack research for: KS Telegram Bot (Telegram e-commerce bot, beauty shop)*
*Researched: 2026-03-02*
