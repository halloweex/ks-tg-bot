---
phase: 01-foundation
plan: 03
subsystem: infra
tags: [python, aiogram, dispatcher, dependency-injection, long-polling, startup-hook]

# Dependency graph
requires:
  - phase: 01-01
    provides: "bot/ package skeleton, config.py, texts.py with Ukrainian strings"
  - phase: 01-02
    provides: "bot/db.py (init_db), bot/services/keycrm.py (KeyCRMClient), bot/services/shopify.py (ShopifyClient)"
provides:
  - bot/__main__.py — entry point: config loading, DI setup, startup hooks, long-polling
  - bot/handlers/common.py — /start command handler with branded Ukrainian greeting
  - Full Phase 1 foundation wired and operational
affects: [02-onboarding, 03-features, 04-production]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "DI pattern: dp[key] = value in __main__.py, handler params auto-injected by aiogram 3 DI"
    - "Shopify graceful degradation: dp['shopify'] = None when credentials absent (not raising)"
    - "Startup hook pattern: @dp.startup() decorator for one-time async init (init_db)"
    - "aiogram 3 router pattern: module-level router = Router(), handlers registered via decorator"

key-files:
  created:
    - bot/__main__.py
    - bot/handlers/common.py
  modified: []

key-decisions:
  - "dp['shopify'] = None (not omitting key) when Shopify creds absent — handlers can safely check `if shopify:` without KeyError"
  - "Startup hook via @dp.startup() decorator (aiogram 3.x) for init_db() — not lifecycle middleware"
  - "CommandStart() filter (aiogram 3.x) for /start handler — not commands=['start'] (aiogram 2.x pattern)"

patterns-established:
  - "Entry point pattern: load_config → Bot → Dispatcher → DI injection → startup hook → include_router → start_polling"
  - "Handler DI pattern: declare config/keycrm/shopify as function parameters, aiogram injects from dp workflow_data"

requirements-completed: [INFR-04]

# Metrics
duration: 1min
completed: 2026-03-02
---

# Phase 1 Plan 03: Bot Entry Point and /start Handler Summary

**aiogram 3 bot entry point wiring all Phase 1 components (config, SQLite, KeyCRM/Shopify clients) into a long-polling bot with a branded Ukrainian /start greeting via DI**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-02T17:05:51Z
- **Completed:** 2026-03-02T17:07:03Z
- **Tasks:** 3 completed (2 auto + 1 human-verify approved)
- **Files modified:** 2 created, 0 modified

## Accomplishments
- Created bot/__main__.py: full entry point wiring config loading, Bot creation (HTML parse mode), DI injection, startup hook (init_db), router registration, and long-polling
- Created bot/handlers/common.py: /start command handler using aiogram 3 Router + CommandStart() filter with config DI and branded greeting from texts.GREETING
- Shopify graceful degradation: dp["shopify"] = None when credentials absent — warning logged, bot continues in KeyCRM-only mode

## Task Commits

Each task was committed atomically:

1. **Task 1: Create bot entry point with startup wiring and DI** - `09743de` (feat)
2. **Task 2: Create /start command handler with branded Ukrainian greeting** - `61c95f5` (feat)
3. **Task 3: Verify bot starts and responds to /start** - human-verify approved

**Plan metadata:** `8bce003` (docs: complete bot entry point and /start handler plan)

## Files Created/Modified
- `bot/__main__.py` - Entry point: config loading, Bot (HTML), Dispatcher, DI (config/keycrm/shopify), @dp.startup() for init_db, common_router inclusion, dp.start_polling(bot)
- `bot/handlers/common.py` - /start handler: Router, CommandStart() filter, AppConfig DI, texts.GREETING.format(brand_name=...)

## Decisions Made
- `dp["shopify"] = None` (not omitting the key) when Shopify credentials absent — handlers can safely do `if shopify:` without risking KeyError on the dp key
- Used `@dp.startup()` decorator (aiogram 3.x) instead of `on_startup` parameter — cleaner, co-located with dispatcher
- `/start` handler uses `CommandStart()` filter from aiogram.filters (aiogram 3.x) — not `commands=["start"]` (aiogram 2.x)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- None

## User Setup Required
None - all tasks complete including human verification.

Human verification (Task 3) passed:
- Bot started with `python -m bot` without errors
- Bot responded to /start with Ukrainian greeting containing "Korean Story"
- bot_data.db was created on startup
- Bot ran in KeyCRM-only mode (Shopify warning logged, no crash)

## Next Phase Readiness
- Full Phase 1 foundation operational: project skeleton + config + SQLite + API clients + bot entry point + /start handler
- Phase 2 (onboarding) can add phone collection handler to bot/handlers/common.py or a new handler module
- Dispatcher DI pattern established — all handlers can receive config, keycrm, shopify as parameters

## Self-Check: PASSED
