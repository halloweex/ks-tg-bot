# Plan 04-01 Summary: Opt-out Commands

**Status:** COMPLETE
**Duration:** ~2min

## What was built

- **bot/db.py** — Added `opt_out_user()`, `opt_in_user()`, `is_opted_out()`, `get_broadcast_recipients()` following existing per-operation aiosqlite pattern
- **bot/texts.py** — Added `MSG_OPT_OUT_CONFIRM`, `MSG_OPT_IN_CONFIRM` (Ukrainian)
- **bot/handlers/broadcast.py** — New router with `/stop` and `/unsubscribe` handlers (both map to `cmd_stop`)
- **bot/handlers/common.py** — `/start` now checks `is_opted_out()` and re-subscribes with confirmation message
- **bot/__main__.py** — Registered `broadcast_router` after `common_router`, before `menu_router`

## Verification

All imports pass. Entry point loads cleanly.
