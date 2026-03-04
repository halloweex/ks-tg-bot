---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: in-progress
last_updated: "2026-03-04"
progress:
  total_phases: 3
  completed_phases: 3
  total_plans: 9
  completed_plans: 9
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-02)

**Core value:** Customers can instantly check the status and details of their orders through Telegram without contacting support
**Current focus:** Phase 3 — Core Features

## Current Position

Phase: 3 of 4 (Core Features) — COMPLETE
Plan: 4 of 4 in current phase — All plans complete
Status: Phase 3 complete. All core features implemented.
Last activity: 2026-03-04 — Completed 03-02, 03-03, 03-04

Progress: [█████████░] 90%

## Performance Metrics

**Velocity:**
- Total plans completed: 6
- Average duration: 3min
- Total execution time: 15min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation | 3 | 5min | 2min |
| 02-onboarding | 2 | 8min | 4min |
| 03-core-features | 4 | ~10min | ~2.5min |

**Recent Trend:**
- Last 5 plans: 01-02 (2min), 01-03 (1min), 02-01 (8min), 02-02 (skipped), 03-01 (2min)
- Trend: stable

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Setup]: Data sources are Shopify API + KeyCRM API (not Google Sheets — research file is outdated on this point)
- [Setup]: SQLite for user data, opt-out, settings (not Google Sheets Subscribers tab)
- [Setup]: aiogram 3.x with long-polling mode
- [Setup]: Main menu: 5 buttons (Orders, Find more, Contact support, Website, Settings)
- [01-01]: pydantic-settings BaseSettings for .env loading (not pydantic v1 BaseSettings — pydantic v2 moved it to a separate package)
- [01-01]: Two-file config: .env secrets (pydantic-settings) + config.yaml non-secrets (yaml.safe_load)
- [01-01]: All Ukrainian strings centralized in bot/texts.py as module-level constants
- [01-02]: KeyCRM phone normalization is mandatory — filter[buyer_phone] does exact match, phones stored in mixed formats
- [01-02]: Shopify uses GraphQL Admin API (2025-01) via httpx POST, no ShopifyAPI SDK (REST deprecated for new apps)
- [01-02]: Shopify phone query uses WITH + prefix (phone:+380...) — opposite of KeyCRM (no + prefix)
- [01-02]: Per-operation aiosqlite connections (no module-level shared connection) — avoids "cannot reuse" errors
- [01-03]: dp["shopify"] = None (not omitting key) when creds absent — handlers can check `if shopify:` without KeyError
- [01-03]: @dp.startup() decorator for init_db() (aiogram 3.x startup hook pattern)
- [01-03]: CommandStart() filter (aiogram 3.x) for /start — not commands=["start"] (aiogram 2.x pattern)
- [02-01]: Phone normalization strips spaces/dashes/parens before regex validation — handles common input variations
- [02-01]: asyncio.gather with return_exceptions=True distinguishes API failure from empty results — ERR_API_UNAVAILABLE vs ERR_PHONE_NOT_FOUND
- [02-01]: /start always clears FSM state first — prevents stale state if user restarts mid-onboarding
- [02-02]: Register users by phone even without existing orders (customers may buy later)
- [02-02]: Telegram contact sharing as primary onboarding method (ReplyKeyboardMarkup with request_contact=True)
- [02-02]: Manual phone input kept as fallback alongside contact button
- [02-02]: Simplified onboarding: no API lookup during registration, only phone validation. Lookup deferred to order viewing (Phase 3)
- [03-01]: Website button uses url= parameter (opens in browser), not callback_data — no server-side handler needed
- [03-01]: edit_text with TelegramBadRequest fallback to new message for stale inline keyboards
- [03-01]: Two-message pattern for returning users: first ReplyKeyboardRemove, second InlineKeyboardMarkup
- [03-01]: Router order: common -> menu -> onboarding (commands first, callbacks second, FSM catch-all last)
- [03-02]: asyncio.gather with return_exceptions=True for parallel order fetch; parse_mode=None for order display to avoid HTML issues
- [03-02]: 3800 char soft limit with truncation marker for long order lists
- [03-03]: Support relay uses chat_id metadata for privacy-safe routing (forward_from may be None)
- [03-03]: Three-message pattern to admin: metadata line, forwarded message, reply instruction
- [03-04]: Phone change uses format-only validation (same PHONE_PATTERN as onboarding, no API lookup)
- [03-04]: Language button uses show_alert=True popup, stays on settings menu

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: KeyCRM API — verify authentication method and phone lookup endpoint before implementing INFR-03
- [Phase 4]: Verify current Telegram flood control limits (30 msg/s figure is from training data, needs confirmation before implementing rate limiter)
- [Phase 2→3]: ONBR-03/ONBR-04/ONBR-05 requirements changed — API lookup now happens at order display (Phase 3), not during registration

## Session Continuity

Last session: 2026-03-04
Stopped at: Phase 3 complete. Ready for Phase 4 (Production Hardening).
Resume file: None
