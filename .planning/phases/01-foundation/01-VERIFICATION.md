---
phase: 01-foundation
verified: 2026-03-02T21:22:00Z
status: passed
score: 12/12 must-haves verified
re_verification: false
human_verification:
  - test: "Start bot with `python -m bot` and send /start in Telegram"
    expected: "Bot replies with Ukrainian greeting containing 'Korean Story', bot_data.db is created, log shows KeyCRM-only mode warning"
    why_human: "Requires live Telegram connection; already approved in Plan 03 Task 3 human checkpoint"
---

# Phase 1: Foundation Verification Report

**Phase Goal:** The project runs, credentials are safe, both APIs are reachable, and SQLite is initialized — no features yet, but nothing downstream is blocked
**Verified:** 2026-03-02T21:22:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | Credentials (.env) are excluded from git tracking | VERIFIED | `.gitignore` line 2: `.env`; `git ls-files .env` returns empty — untracked via `git rm --cached` |
| 2  | Bot token, API keys load from environment variables via pydantic Settings | VERIFIED | `bot/config.py` `EnvSettings(BaseSettings)` with `model_config = {"env_file": ".env"}`; `load_config()` runs, returns `bot_token` and `keycrm_api_key` |
| 3  | Brand name, website URL, support chat ID load from config.yaml | VERIFIED | `load_config()` returns `brand=Korean Story, url=https://koreanstory.com.ua, support=129462784` — confirmed by live execution |
| 4  | All user-facing strings are in Ukrainian in a single texts module | VERIFIED | `bot/texts.py` — 15 constants, all in Ukrainian (GREETING, 4 ERR_, 5 BTN_, 3 MSG_); no English user-facing strings |
| 5  | Info page placeholders are configurable in config.yaml | VERIFIED | `config.yaml` has `about_text`, `contacts_text`, `payment_text`, `delivery_text`; `AppConfig` maps them |
| 6  | SQLite database file is created on startup with users and opt_out tables | VERIFIED | `init_db()` executed: creates `bot_data.db` with tables `['users', 'opt_out']`; confirmed by asyncio run |
| 7  | KeyCRM client looks up orders by phone with normalization (strip +, spaces, dashes) | VERIFIED | `normalize_phone_for_keycrm('+380671234567') == '380671234567'`; all 3 normalization cases pass |
| 8  | Shopify client uses GraphQL with + prefix phone format | VERIFIED | `GRAPHQL_QUERY` defined; `ShopifyClient._endpoint` contains `graphql.json`; phone sent as `phone:{phone}` |
| 9  | Both API clients return typed dataclass results, not raw dicts | VERIFIED | `KeyCRMOrder` dataclass in keycrm.py; `ShopifyOrder` dataclass in shopify.py; both exported |
| 10 | Both clients handle errors gracefully (log and return empty list, never crash) | VERIFIED | Both wrap requests in `try/except httpx.HTTPError`; log via loguru; return `[]` |
| 11 | Bot starts with `python -m bot` and connects to Telegram via long-polling | VERIFIED | `bot/__main__.py` contains `await dp.start_polling(bot)`; all imports resolve; human checkpoint approved |
| 12 | Bot responds to /start with a brand-configurable Ukrainian welcome message | VERIFIED | `cmd_start` uses `texts.GREETING.format(brand_name=config.brand_name)`; 1 handler registered on `Router` |

**Score:** 12/12 truths verified

---

### Required Artifacts

| Artifact | Provides | Exists | Substantive | Wired | Status |
|----------|----------|--------|-------------|-------|--------|
| `.gitignore` | Git exclusion rules for secrets, IDE, DB, Python artifacts | Yes | Yes — 31 lines, `.env` on line 2, full exclusions | N/A (config file) | VERIFIED |
| `requirements.txt` | Pinned dependencies for the project | Yes | Yes — 7 deps: aiogram==3.25.0, aiosqlite==0.22.1, python-dotenv==1.2.2, pyyaml==6.0.3, httpx==0.28.1, loguru==0.7.3, pydantic-settings>=2.0.0 | N/A (manifest) | VERIFIED |
| `bot/config.py` | Two-file config loading: .env secrets + config.yaml settings | Yes | Yes — 70 lines; `EnvSettings`, `AppConfig`, `load_config()` all defined | Imported by `bot/__main__.py`, `bot/handlers/common.py` | VERIFIED |
| `bot/texts.py` | All Ukrainian UI strings as module-level constants | Yes | Yes — 44 lines; 15 constants; `GREETING` with `{brand_name}` placeholder | Imported by `bot/handlers/common.py` | VERIFIED |
| `config.yaml` | Non-secret settings: brand_name, website_url, support_chat_id, info page texts | Yes | Yes — `brand_name: "Korean Story"`, `website_url`, `support_chat_id`, 4 info texts | Loaded via `yaml.safe_load` in `load_config()` | VERIFIED |
| `bot/db.py` | SQLite schema initialization and connection helper | Yes | Yes — 42 lines; `init_db()`, `get_db()`, `DB_PATH` all exported | Called in `bot/__main__.py` `@dp.startup()` hook | VERIFIED |
| `bot/services/keycrm.py` | KeyCRM REST API client with phone normalization | Yes | Yes — 97 lines; `KeyCRMClient`, `KeyCRMOrder`, `normalize_phone_for_keycrm` | Injected into `dp["keycrm"]` in `bot/__main__.py` | VERIFIED |
| `bot/services/shopify.py` | Shopify GraphQL Admin API client | Yes | Yes — 129 lines; `ShopifyClient`, `ShopifyOrder`, `GRAPHQL_QUERY` | Conditionally injected into `dp["shopify"]` in `bot/__main__.py` | VERIFIED |
| `bot/__main__.py` | Entry point: config loading, DI setup, startup hooks, polling | Yes | Yes — 66 lines; all 7 structural checks pass | Root entry point — not imported, executed | VERIFIED |
| `bot/handlers/common.py` | /start command handler with branded greeting | Yes | Yes — 19 lines; `CommandStart()` filter; `config` DI; `GREETING.format()` | Included via `dp.include_router(common_router)` | VERIFIED |
| `bot/__init__.py` | Python package marker | Yes | Present | N/A | VERIFIED |
| `bot/handlers/__init__.py` | Package marker | Yes | Present | N/A | VERIFIED |
| `bot/services/__init__.py` | Package marker | Yes | Present | N/A | VERIFIED |
| `.env.example` | Template with placeholder values | Yes | Present — BOT_TOKEN, KEYCRM_API_KEY, SHOPIFY_*, ADMIN_USER_IDS | N/A (template) | VERIFIED |

---

### Key Link Verification

| From | To | Via | Pattern Found | Status |
|------|----|-----|---------------|--------|
| `bot/config.py` | `.env` | pydantic Settings `env_file` | `"env_file": ".env"` at line 29 | WIRED |
| `bot/config.py` | `config.yaml` | `yaml.safe_load` | `yaml.safe_load(f)` at line 58 | WIRED |
| `bot/services/keycrm.py` | `https://openapi.keycrm.app/v1/order` | `httpx.AsyncClient GET with Bearer token` | `BASE_URL = "https://openapi.keycrm.app/v1"` at line 9 | WIRED |
| `bot/services/shopify.py` | Shopify GraphQL endpoint | `httpx.AsyncClient POST with X-Shopify-Access-Token` | `graphql.json` in `_endpoint` at line 87 | WIRED |
| `bot/db.py` | `bot_data.db` | `aiosqlite.connect` | `aiosqlite.connect(DB_PATH)` at lines 32, 41 | WIRED |
| `bot/__main__.py` | `bot/config.py` | `load_config()` | `from bot.config import load_config` + `config = load_config()` | WIRED |
| `bot/__main__.py` | `bot/db.py` | `init_db()` in `@dp.startup()` | `await init_db()` inside `on_startup()` at line 53 | WIRED |
| `bot/__main__.py` | `bot/services/keycrm.py` | `KeyCRMClient` injected into `dp` | `dp["keycrm"] = KeyCRMClient(...)` at line 35 | WIRED |
| `bot/__main__.py` | `bot/services/shopify.py` | `ShopifyClient` conditionally injected into `dp` | `dp["shopify"] = ShopifyClient(...)` / `dp["shopify"] = None` at lines 39, 45 | WIRED |
| `bot/handlers/common.py` | `bot/texts.py` | `GREETING.format(brand_name=...)` | `texts.GREETING.format(brand_name=config.brand_name)` at line 17 | WIRED |

All 10 key links: WIRED.

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| CONF-01 | 01-01 | All bot UI text is in Ukrainian | SATISFIED | `bot/texts.py` — 15 constants all in Ukrainian; no English user-facing strings in handlers |
| CONF-02 | 01-01 | Brand name and greeting text configurable via config file | SATISFIED | `brand_name` in `config.yaml`; `GREETING.format(brand_name=config.brand_name)` in handler |
| CONF-03 | 01-01 | Shopify and KeyCRM API credentials stored in environment variables | SATISFIED | `EnvSettings` loads `BOT_TOKEN`, `KEYCRM_API_KEY`, `SHOPIFY_API_TOKEN` from `.env` |
| CONF-04 | 01-01 | Support person Telegram chat ID configurable | SATISFIED | `support_chat_id: 129462784` in `config.yaml`; mapped to `AppConfig.support_chat_id` |
| CONF-05 | 01-01 | Shop website URL configurable | SATISFIED | `website_url: "https://koreanstory.com.ua"` in `config.yaml`; mapped to `AppConfig.website_url` |
| INFR-01 | 01-02 | SQLite database stores user data, opt-out status, and settings | SATISFIED | `bot/db.py` creates `users` and `opt_out` tables; `init_db()` verified with live run |
| INFR-02 | 01-02 | Shopify API service with phone-based order lookup | SATISFIED | `ShopifyClient.get_orders_by_phone()` uses GraphQL; `phone:+380...` query format; typed `ShopifyOrder` return |
| INFR-03 | 01-02 | KeyCRM API service with phone-based order lookup | SATISFIED | `KeyCRMClient.get_orders_by_phone()` with `normalize_phone_for_keycrm()`; typed `KeyCRMOrder` return |
| INFR-04 | 01-03 | aiogram 3.x bot framework with long-polling mode | SATISFIED | `aiogram==3.25.0` in requirements; `dp.start_polling(bot)` in `__main__.py`; `CommandStart()` filter (aiogram 3.x) |

All 9 requirements: SATISFIED. No orphaned requirements.

---

### Anti-Patterns Found

| File | Pattern | Severity | Assessment |
|------|---------|----------|------------|
| `bot/texts.py` lines 11, 22 | String `+380XXXXXXXXX` matched "placeholder" grep | Info | **False positive** — this is a Ukrainian phone format instruction for the user, intentional content |
| `bot/services/shopify.py` lines 116, 120, 128 | `return []` | Info | **False positive** — these are the graceful error handling returns inside `except httpx.HTTPError` blocks, correct by design |
| `bot/services/keycrm.py` line 96 | `return []` | Info | **False positive** — same pattern, correct graceful degradation |
| `bot/config.py` line 25 | `return []` | Info | **False positive** — `admin_ids` property returns empty list when `admin_user_ids` is empty string |

No genuine anti-patterns. No stubs. No TODO/FIXME comments. No hardcoded English user-facing strings in handlers.

---

### Human Verification Required

#### 1. Live Bot Startup and /start Response

**Test:** Run `source .venv/bin/activate && python -m bot` in the project directory, then send `/start` to @koreanstory_bot in Telegram.
**Expected:** Bot replies with "Вітаємо у Korean Story! 🌸" greeting; logs show "Shopify credentials not configured — running in KeyCRM-only mode"; `bot_data.db` is created.
**Why human:** Requires live Telegram connection and active bot token.

**Note:** This checkpoint was already completed and approved during Plan 03 Task 3 execution (2026-03-02T17:07:03Z). Human verifier confirmed all expected behaviors.

---

### Gaps Summary

No gaps. All must-haves from all three plans (01-01, 01-02, 01-03) are verified in the actual codebase.

The phase achieves its goal completely:
- **Credentials safe:** `.env` is in `.gitignore` and untracked from git; pydantic-settings loads secrets at runtime only.
- **Both APIs reachable:** `KeyCRMClient` points to `openapi.keycrm.app` with Bearer auth; `ShopifyClient` points to `graphql.json` endpoint with `X-Shopify-Access-Token`. Phone normalization is implemented correctly (KeyCRM strips `+`; Shopify keeps `+`).
- **SQLite initialized:** `init_db()` creates `users` and `opt_out` tables on startup via `@dp.startup()` hook.
- **Project runs:** `python -m bot` starts long-polling; `/start` returns branded Ukrainian greeting; Shopify absence is handled gracefully.
- **Nothing downstream blocked:** All exported interfaces (`load_config`, `init_db`, `get_db`, `KeyCRMClient`, `ShopifyClient`, `AppConfig`, all `texts.*` constants) are substantive and correctly wired.

---

_Verified: 2026-03-02T21:22:00Z_
_Verifier: Claude (gsd-verifier)_
