---
phase: 03-core-features
plan: 03
subsystem: info-support
tags: [aiogram, info-pages, support-relay, forwarding]

# Dependency graph
requires:
  - phase: 03-01
    provides: "InfoAction callback data, MenuAction, keyboards"
provides:
  - "Info page handlers for About, Contacts, Payment, Delivery"
  - "Bidirectional support relay (user->admin, admin->user)"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Config-driven info pages (text from config.yaml, not hardcoded)"
    - "Metadata message pattern for privacy-safe support relay"
    - "bot.forward_message for preserving original message format"

key-files:
  created:
    - bot/handlers/info.py
    - bot/handlers/support.py
  modified:
    - bot/texts.py

key-decisions:
  - "Info page Back button returns to info submenu (not main menu)"
  - "Support relay uses chat_id metadata for privacy-safe routing"
  - "Admin reply routing: try forward_from first, fallback to chat_id regex parse"
  - "Three-message pattern to admin: metadata, forward, instruction"

patterns-established:
  - "_show_info_page() shared helper for info page display"
  - "chat_id metadata for cross-chat message routing"

requirements-completed: [INFO-01, INFO-02, INFO-03, INFO-04, SUPP-01, SUPP-02]

# Metrics
completed: 2026-03-04
---

# Phase 3 Plan 3: Info Pages + Support Relay Summary

**Four config-driven info pages and bidirectional support relay with privacy-safe routing**

## Accomplishments
- Info handlers display About, Contacts, Payment, Delivery text from config.yaml
- Each info page has Back button returning to info submenu
- Support button enters FSM waiting_message state with prompt
- User message forwarded to admin with metadata line containing chat_id
- Admin replies to forwarded message, bot routes reply back to user
- Privacy-safe: uses chat_id metadata when forward_from is hidden

## Commits
1. **feat(03-03): add info pages and bidirectional support relay** - `17a8f42`

## Files Created
- `bot/handlers/info.py` (56 lines) — 4 info page callback handlers
- `bot/handlers/support.py` (96 lines) — support entry, forwarding, admin reply

## Verification
- info_router: 4 callback handlers (about, contacts, payment, delivery)
- support_router: 1 callback handler + 2 message handlers (forward + admin reply)
- Metadata format verified with chat_id substitution

---
*Phase: 03-core-features*
*Completed: 2026-03-04*
