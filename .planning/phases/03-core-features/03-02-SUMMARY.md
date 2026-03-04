---
phase: 03-core-features
plan: 02
subsystem: orders
tags: [aiogram, asyncio, shopify, keycrm, order-display]

# Dependency graph
requires:
  - phase: 03-01
    provides: "MenuAction callback data, keyboard builders"
provides:
  - "Order display handler with merged Shopify + KeyCRM results"
  - "Parallel API fetch via asyncio.gather with return_exceptions"
  - "Source-tagged formatting (web/Instagram)"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "asyncio.gather with return_exceptions for parallel API calls"
    - "Graceful degradation when Shopify is None"
    - "Message truncation to respect Telegram 4096 char limit"

key-files:
  created:
    - bot/handlers/orders.py
  modified: []

key-decisions:
  - "parse_mode=None for order display to avoid HTML issues with user data"
  - "Shopify=None handled via async noop coroutine returning empty list"
  - "Orders sorted by date descending (newest first)"
  - "3800 char soft limit with truncation marker for long lists"

patterns-established:
  - "_back_to_menu_kb() pattern for single Back button to main menu"
  - "isinstance(result, Exception) pattern for gather error checking"

requirements-completed: [ORDR-01, ORDR-02, ORDR-03, ORDR-04]

# Metrics
completed: 2026-03-04
---

# Phase 3 Plan 2: Order Display Summary

**Merged order display from Shopify + KeyCRM with parallel fetch, source tagging, and date-sorted formatting**

## Accomplishments
- Orders fetched in parallel from KeyCRM and Shopify via asyncio.gather
- Shopify orders tagged "web", KeyCRM orders tagged "Instagram"
- Each order shows: status, products (name x qty), total amount, date (DD.MM.YYYY)
- Merged list sorted by date descending (newest first)
- Handles: no phone, no orders, API errors, both APIs failing, mixed results
- Message truncation at 3800 chars with "...та інші замовлення" marker

## Commits
1. **feat(03-02): add order display with merged Shopify + KeyCRM results** - `e7f52b3`

## Files Created
- `bot/handlers/orders.py` (200 lines) — order formatting utilities + show_orders callback handler

## Verification
- All order handler tests passed (KeyCRM formatting, Shopify formatting, empty results, error handling)
- Router has 1 callback_query handler
- Imports verified clean

---
*Phase: 03-core-features*
*Completed: 2026-03-04*
