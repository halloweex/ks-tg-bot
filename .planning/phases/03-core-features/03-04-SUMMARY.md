---
phase: 03-core-features
plan: 04
subsystem: settings
tags: [aiogram, fsm, phone-validation, settings]

# Dependency graph
requires:
  - phase: 03-01
    provides: "SettingsAction callback data, keyboards, SettingsStates"
provides:
  - "Phone change FSM flow with format validation"
  - "Language display placeholder (Ukrainian active)"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Reuse PHONE_PATTERN from onboarding for consistent validation"
    - "show_alert=True for popup notification without page change"
    - "save_user INSERT OR REPLACE for phone update"

key-files:
  created:
    - bot/handlers/settings.py
  modified:
    - bot/__main__.py

key-decisions:
  - "Format-only phone validation (no API lookup) — consistent with onboarding decision [02-02]"
  - "Language button uses callback.answer(show_alert=True) popup, no page navigation"
  - "After phone change, user returns to main menu with confirmation"

patterns-established:
  - "PHONE_PATTERN imported from onboarding (single source of truth)"

requirements-completed: [SETT-01, SETT-02]

# Metrics
completed: 2026-03-04
---

# Phase 3 Plan 4: Settings Summary

**Phone change FSM flow with format validation and language display placeholder**

## Accomplishments
- "Change phone" button enters FSM waiting_new_phone state
- User types new phone, validated with same PHONE_PATTERN as onboarding
- Invalid phone shows error, user retries (stays in FSM state)
- Valid phone saved via save_user (INSERT OR REPLACE), confirmation + main menu shown
- "Language" button shows popup "Ukrainian active" without navigating away

## Commits
1. **feat(03-04): add settings handlers for phone change and language display** - `b085cd6`

## Files Created
- `bot/handlers/settings.py` (61 lines) — phone change FSM + language popup

## Verification
- settings_router: 2 callback handlers (phone, language) + 1 message handler (new phone input)
- __main__.py imports verified clean

---
*Phase: 03-core-features*
*Completed: 2026-03-04*
