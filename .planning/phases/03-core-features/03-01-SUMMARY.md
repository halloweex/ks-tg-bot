---
phase: 03-core-features
plan: 01
subsystem: ui
tags: [aiogram, inline-keyboard, callback-data, fsm, navigation]

# Dependency graph
requires:
  - phase: 02-onboarding
    provides: "User registration flow, /start handler, FSM states"
provides:
  - "CallbackData factory classes (MenuAction, InfoAction, SettingsAction)"
  - "Inline keyboard builders (main_menu_kb, info_menu_kb, settings_menu_kb)"
  - "Menu navigation handlers with in-place edit_text transitions"
  - "SupportStates and SettingsStates FSM groups"
  - "Menu text constants (MSG_MAIN_MENU, MSG_INFO_MENU, MSG_SETTINGS_MENU)"
affects: [03-02, 03-03, 03-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CallbackData factories with prefix for type-safe inline callbacks"
    - "InlineKeyboardBuilder with adjust() for row layout control"
    - "edit_text for in-place menu transitions with TelegramBadRequest fallback"
    - "Two-message pattern: ReplyKeyboardRemove + InlineKeyboardMarkup"

key-files:
  created:
    - bot/callbacks.py
    - bot/keyboards.py
    - bot/handlers/menu.py
  modified:
    - bot/states.py
    - bot/texts.py
    - bot/handlers/common.py
    - bot/handlers/onboarding.py
    - bot/__main__.py

key-decisions:
  - "Website button uses url= parameter (browser opens link), not callback_data"
  - "edit_text with TelegramBadRequest fallback to message.answer for stale keyboards"
  - "Two-message pattern for /start and onboarding: first ReplyKeyboardRemove, second inline menu"
  - "Router order: common -> menu -> onboarding (commands first, callbacks second, FSM catch-all last)"

patterns-established:
  - "CallbackData prefix convention: menu, info, sett for menu tree levels"
  - "Shared _show_main_menu helper to DRY back-button handlers"
  - "Keyboard builders in bot/keyboards.py accept config values as parameters"

requirements-completed: [NAVG-01, NAVG-02, NAVG-03, NAVG-04, NAVG-05]

# Metrics
duration: 2min
completed: 2026-03-03
---

# Phase 3 Plan 1: Navigation Infrastructure Summary

**Inline menu system with CallbackData factories, 3 keyboard builders, and 5 callback handlers for main/sub menu navigation**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-03T11:30:05Z
- **Completed:** 2026-03-03T11:32:14Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- CallbackData factory classes (MenuAction, InfoAction, SettingsAction) providing type-safe inline button callbacks
- Three keyboard builders producing correct InlineKeyboardMarkup with proper button layouts (1-per-row, 2+2+1, URL button)
- Five callback handlers for menu navigation with in-place edit_text transitions and TelegramBadRequest fallback
- /start and onboarding completion now display inline main menu to users

## Task Commits

Each task was committed atomically:

1. **Task 1: Create callback data classes, keyboard builders, FSM states, and new texts** - `258390b` (feat)
2. **Task 2: Create menu navigation handlers, update /start and onboarding, wire routers** - `a3f224a` (feat)

## Files Created/Modified
- `bot/callbacks.py` - CallbackData factory classes (MenuAction, InfoAction, SettingsAction)
- `bot/keyboards.py` - Inline keyboard builder functions (main_menu_kb, info_menu_kb, settings_menu_kb)
- `bot/handlers/menu.py` - Callback query handlers for menu navigation (5 handlers)
- `bot/states.py` - Added SupportStates and SettingsStates FSM groups
- `bot/texts.py` - Added menu header and content text constants
- `bot/handlers/common.py` - /start now shows inline main menu for returning users
- `bot/handlers/onboarding.py` - Registration completion now shows inline main menu
- `bot/__main__.py` - Registered menu_router between common and onboarding routers

## Decisions Made
- Website button uses `url=` parameter (opens in browser) rather than callback_data -- no server-side handler needed
- edit_text wrapped in TelegramBadRequest try/except with fallback to new message -- handles stale inline keyboards gracefully
- Two-message pattern for returning users and post-onboarding: first message removes ReplyKeyboard, second sends InlineKeyboard
- Router registration order: common (commands) -> menu (callbacks) -> onboarding (FSM catch-all)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All subsequent plans (03-02 orders, 03-03 info/support, 03-04 settings) can now import CallbackData classes and keyboard builders
- Menu navigation infrastructure tested and working
- Placeholder callback handlers for "orders" and "support" MenuActions not yet registered (will be handled in their respective plans)

## Self-Check: PASSED

- All 3 created files exist on disk
- All 5 modified files verified
- Both task commits (258390b, a3f224a) found in git log
- All verifications pass (imports, keyboard assertions, router order)

---
*Phase: 03-core-features*
*Completed: 2026-03-03*
