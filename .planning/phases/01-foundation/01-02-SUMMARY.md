---
phase: 01-foundation
plan: 02
subsystem: database, api
tags: [python, aiosqlite, sqlite, httpx, keycrm, shopify, graphql, loguru, dataclasses]

# Dependency graph
requires:
  - phase: 01-01
    provides: "bot/ package skeleton, requirements.txt, loguru and httpx in requirements"
provides:
  - bot/db.py — SQLite schema initialization (users, opt_out tables) and connection helper
  - bot/services/keycrm.py — KeyCRM REST API client with phone normalization
  - bot/services/shopify.py — Shopify GraphQL Admin API client with graceful degradation
affects: [01-03, 02-onboarding, 03-features, 04-production]

# Tech tracking
tech-stack:
  added:
    - aiosqlite==0.22.1 (async SQLite, installed into .venv)
    - loguru==0.7.3 (structured logging, installed into .venv)
    - httpx==0.28.1 (async HTTP client, installed into .venv)
  patterns:
    - "Per-operation DB connections: every caller uses async with get_db() — no shared module-level connection"
    - "Phone normalization before KeyCRM query: normalize_phone_for_keycrm() strips +, spaces, dashes, parens"
    - "Shopify graceful degradation: ShopifyClient only instantiated when api_token is configured"
    - "Error silence pattern: both API clients catch httpx.HTTPError, log it, and return [] (never raise)"

key-files:
  created:
    - bot/db.py
    - bot/services/keycrm.py
    - bot/services/shopify.py
  modified: []

key-decisions:
  - "KeyCRM phone normalization is mandatory: filter[buyer_phone] does exact match, stores mixed formats (380... and +380...)"
  - "Shopify uses GraphQL Admin API (2025-01) via httpx POST — no ShopifyAPI SDK (REST deprecated for new apps)"
  - "Shopify phone query uses WITH + prefix (phone:+380...) — opposite of KeyCRM (no + prefix)"
  - "Per-operation aiosqlite connections (no module-level shared connection) — avoids 'cannot reuse' errors"

patterns-established:
  - "DB pattern: init_db() on startup, get_db() returns new connection each call, caller uses async with"
  - "API client pattern: dataclass result type, module-level helper parser, graceful empty-list on error"
  - "Logging pattern: loguru logger.info for success, logger.error for HTTP failures, logger.warning for rate limits"

requirements-completed: [INFR-01, INFR-02, INFR-03]

# Metrics
duration: 2min
completed: 2026-03-02
---

# Phase 1 Plan 02: Database and API Clients Summary

**aiosqlite SQLite database with users/opt_out schema, KeyCRM REST client with phone normalization, and Shopify GraphQL Admin API client with graceful degradation when token absent**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-02T17:01:22Z
- **Completed:** 2026-03-02T17:03:32Z
- **Tasks:** 3
- **Files modified:** 3 created, 0 modified

## Accomplishments
- Created bot/db.py: async SQLite init with users and opt_out tables, per-operation connection pattern
- Created bot/services/keycrm.py: REST client with phone normalization (strips + for exact-match query), KeyCRMOrder typed dataclass
- Created bot/services/shopify.py: GraphQL client (no SDK, httpx only), ShopifyOrder typed dataclass, handles missing credentials at bot startup

## Task Commits

Each task was committed atomically:

1. **Task 1: Create SQLite database module with schema initialization** - `0f68d63` (feat)
2. **Task 2: Create KeyCRM REST API client with phone normalization** - `c42ee06` (feat)
3. **Task 3: Create Shopify GraphQL API client with graceful degradation** - `6d13024` (feat)

**Plan metadata:** TBD (docs: complete plan)

## Files Created/Modified
- `bot/db.py` - SQLite schema init (users, opt_out tables), init_db(), get_db(), DB_PATH constant
- `bot/services/keycrm.py` - KeyCRMClient, KeyCRMOrder dataclass, normalize_phone_for_keycrm()
- `bot/services/shopify.py` - ShopifyClient, ShopifyOrder dataclass, GRAPHQL_QUERY constant

## Decisions Made
- Per-operation DB connections: no module-level aiosqlite connection (avoids "cannot reuse" error on concurrent requests)
- KeyCRM phone normalization: strip + prefix before querying because filter[buyer_phone] uses exact match and KeyCRM stores phones in both `380...` and `+380...` formats
- Shopify phone format is WITH + prefix (`phone:+380...` query syntax) — opposite of KeyCRM
- GraphQL via httpx POST instead of ShopifyAPI SDK (Shopify deprecated REST for new apps; GraphQL is ~50 lines of code)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed missing loguru dependency**
- **Found during:** Task 1 (db.py implementation)
- **Issue:** loguru not installed in .venv — `from loguru import logger` failed
- **Fix:** Ran `.venv/bin/pip install loguru==0.7.3`
- **Files modified:** None (virtual environment only, requirements.txt already listed it)
- **Verification:** `from bot.db import init_db, DB_PATH` imports successfully
- **Committed in:** `0f68d63` (Task 1 commit)

**2. [Rule 3 - Blocking] Installed missing httpx dependency**
- **Found during:** Task 2 (keycrm.py implementation)
- **Issue:** httpx not installed in .venv — `import httpx` failed
- **Fix:** Ran `.venv/bin/pip install httpx==0.28.1` (also installed anyio, httpcore, h11 as transitive deps)
- **Files modified:** None (virtual environment only, requirements.txt already listed it)
- **Verification:** `from bot.services.keycrm import KeyCRMClient` imports successfully
- **Committed in:** `c42ee06` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 blocking dependency installs)
**Impact on plan:** Both installs necessary for code execution. requirements.txt already listed them — only .venv was incomplete. No scope creep.

## Issues Encountered
- aiosqlite was already installed (pip confirmed it) but loguru and httpx were missing from .venv despite being in requirements.txt. All three installed successfully with pinned versions.

## User Setup Required
None - no external service configuration required at this stage. Shopify API token will be added to .env when the Custom App is created (handled in a future plan).

## Next Phase Readiness
- SQLite database module ready: init_db() can be called at bot startup, get_db() usable from any handler
- KeyCRM client ready: KeyCRMClient(api_key=config.env.keycrm_api_key) can be instantiated in Plan 03
- Shopify client ready for graceful degradation: Plan 03 checks `if config.env.shopify_api_token:` before instantiating
- All data-access components in place — Plan 03 (bot entry point) can wire them together

## Self-Check: PASSED
