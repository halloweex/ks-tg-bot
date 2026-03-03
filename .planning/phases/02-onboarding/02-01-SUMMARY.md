---
phase: 02-onboarding
plan: 01
subsystem: auth
tags: [aiogram, fsm, sqlite, aiosqlite, phone-validation, asyncio-gather]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: "bot scaffold, db.py init_db/get_db, texts.py constants, keycrm/shopify service clients, __main__.py wiring"
provides:
  - "OnboardingStates FSM state group (bot/states.py)"
  - "save_user() and get_user_phone() DB helpers (bot/db.py)"
  - "Phone validation handler with dual-API lookup (bot/handlers/onboarding.py)"
  - "Returning user detection in /start (bot/handlers/common.py)"
affects: [03-orders, 04-settings, 05-support]

# Tech tracking
tech-stack:
  added: []
  patterns: [FSM state filtering, per-operation aiosqlite connections, asyncio.gather with return_exceptions, input normalization before validation]

key-files:
  created:
    - bot/states.py
    - bot/handlers/onboarding.py
  modified:
    - bot/db.py
    - bot/texts.py
    - bot/handlers/common.py
    - bot/__main__.py

key-decisions:
  - "Phone normalization strips spaces/dashes/parens before regex validation -- handles common input variations"
  - "asyncio.gather with return_exceptions=True distinguishes API failure from empty results -- shows ERR_API_UNAVAILABLE vs ERR_PHONE_NOT_FOUND"
  - "/start always clears FSM state first -- prevents stale state if user restarts mid-onboarding"

patterns-established:
  - "FSM state group per feature domain: OnboardingStates in bot/states.py (will grow with settings states in Phase 3)"
  - "Handler returns on validation failure (stays in same FSM state for retry)"
  - "Conditional Shopify task: only added to gather list if shopify is not None"

requirements-completed: [ONBR-01, ONBR-02, ONBR-03, ONBR-04, ONBR-05, ONBR-06]

# Metrics
duration: 8min
completed: 2026-03-03
---

# Phase 2 Plan 1: Onboarding Flow Summary

**FSM-driven phone onboarding with +380 validation, parallel Shopify+KeyCRM lookup, and SQLite user persistence**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-03T04:14:54Z
- **Completed:** 2026-03-03T04:23:30Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments
- OnboardingStates FSM state group with waiting_phone state for phone input flow
- Phone validation with +380XXXXXXXXX regex and input normalization (strips spaces, dashes, parentheses)
- Parallel dual-API lookup via asyncio.gather distinguishing API failures from empty results
- Returning user detection in /start handler -- verified users skip phone entry entirely
- SQLite persistence of chat_id-to-phone mapping via save_user/get_user_phone

## Task Commits

Each task was committed atomically:

1. **Task 1: Create FSM states and add DB helper functions** - `346ea0c` (feat)
2. **Task 2: Implement onboarding handler and modify /start** - `7b81732` (feat)
3. **Task 3: Register onboarding router and verify bot starts** - `02f325f` (feat)

## Files Created/Modified
- `bot/states.py` - FSM state group with OnboardingStates.waiting_phone
- `bot/db.py` - Added save_user() and get_user_phone() using per-operation aiosqlite connections
- `bot/texts.py` - Added MSG_WELCOME_BACK and MSG_PHONE_VERIFIED constants
- `bot/handlers/onboarding.py` - Phone input handler with validation, normalization, parallel API lookup
- `bot/handlers/common.py` - Modified /start to clear FSM state, detect returning users, set waiting_phone for new users
- `bot/__main__.py` - Registered onboarding_router after common_router

## Decisions Made
- Phone normalization strips spaces/dashes/parens before regex validation -- handles common input variations like "+380 67 123-4567"
- asyncio.gather with return_exceptions=True distinguishes API failure (exception) from empty results (empty list) -- different error messages for each case
- /start always clears FSM state first -- prevents stale state if user sends /start mid-onboarding
- Onboarding handler stays in waiting_phone state on validation failure or API error -- user can retry without restarting

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Onboarding flow complete -- all downstream features can rely on verified phone numbers in SQLite
- /start handler has placeholder for main menu (Phase 3 will replace MSG_WELCOME_BACK with keyboard)
- Phone verification is the gate for orders, settings, and support features

## Self-Check: PASSED

All 7 files verified present. All 3 task commits verified in git log.

---
*Phase: 02-onboarding*
*Completed: 2026-03-03*
