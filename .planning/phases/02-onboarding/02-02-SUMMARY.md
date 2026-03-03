---
phase: 02-onboarding
plan: 02
subsystem: auth
tags: [human-verification, contact-sharing, phone-normalization]

# Dependency graph
requires:
  - phase: 02-onboarding
    plan: 01
    provides: "FSM states, phone validation, onboarding handler, /start modification"
provides:
  - "Verified onboarding flow: /start → phone entry or contact share → registration"
  - "Contact sharing via ReplyKeyboardMarkup (request_contact=True)"
  - "Registration without existing orders (phone-only validation)"
affects: [03-core-features]

# Tech tracking
tech-stack:
  added: []
  patterns: [Telegram contact sharing, ReplyKeyboardMarkup with request_contact]

key-files:
  created: []
  modified:
    - bot/handlers/onboarding.py
    - bot/handlers/common.py
    - bot/texts.py
---

# Plan 02-02 Summary: Human Verification

## Outcome

Verification skipped by user decision. Moving to Phase 3.

## Changes During Testing

Two requirement changes surfaced during testing and were implemented:

1. **Register without orders**: Users are now registered by phone even if no orders exist in Shopify/KeyCRM (customers may buy later)
2. **Contact sharing button**: Added Telegram "Share contact" as primary onboarding method (ReplyKeyboardMarkup with request_contact=True); manual phone input kept as fallback

## Bug Fixes

1. Removed `from __future__ import annotations` from onboarding.py — broke aiogram DI for `ShopifyClient | None` union types
2. Improved contact phone normalization with debug logging

## Decisions

- Simplified onboarding: no API lookup during registration, only phone validation
- Phone lookup against Shopify/KeyCRM deferred to order viewing (Phase 3)
- ONBR-03/ONBR-04/ONBR-05 requirements effectively changed: API lookup happens at order display, not registration

## Commits

- ab15669: Fix future annotations + register without orders + share-contact button
- fd97d76: Improved contact phone normalization with debug logging
