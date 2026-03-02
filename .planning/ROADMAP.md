# Roadmap: KS Telegram Bot

## Overview

Build a customer-service Telegram bot for a Ukrainian beauty shop in four phases: foundation first (project skeleton, API integrations, SQLite), then onboarding (the critical path gate — phone lookup against Shopify + KeyCRM), then core user-facing features (navigation, order display, info pages, support, settings), and finally broadcast (highest-risk feature, needs real subscriber data to test safely). Every downstream feature depends on a verified phone number, so onboarding is the gate. Broadcast is last because a misconfigured blast can get the bot banned or create legal exposure.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** - Project skeleton, API clients, SQLite, config — everything downstream depends on this (completed 2026-03-02)
- [ ] **Phase 2: Onboarding** - /start, phone collection, dual API lookup (Shopify + KeyCRM), SQLite user storage
- [ ] **Phase 3: Core Features** - Navigation menus, order display, info pages, support escalation, settings
- [ ] **Phase 4: Broadcast** - Admin broadcast command with rate limiting and opt-out enforcement

## Phase Details

### Phase 1: Foundation
**Goal**: The project runs, credentials are safe, both APIs are reachable, and SQLite is initialized — no features yet, but nothing downstream is blocked
**Depends on**: Nothing (first phase)
**Requirements**: INFR-01, INFR-02, INFR-03, INFR-04, CONF-01, CONF-02, CONF-03, CONF-04, CONF-05
**Success Criteria** (what must be TRUE):
  1. Bot starts with `python -m bot` and responds to /start with a brand-configurable welcome message
  2. Shopify API client can look up a phone number and return orders (verified with a test phone)
  3. KeyCRM API client can look up a phone number and return orders (verified with a test phone)
  4. SQLite database file is created on startup with the correct schema (users, opt-out tables)
  5. Credentials (bot token, API keys) are loaded from environment variables, never hardcoded; .gitignore excludes .env
**Plans**: 3 plans
- [x] 01-01-PLAN.md — Project skeleton, config system (.env + config.yaml), Ukrainian texts
- [x] 01-02-PLAN.md — SQLite database, KeyCRM client, Shopify GraphQL client
- [x] 01-03-PLAN.md — Bot entry point, /start handler, full wiring + human verification

### Phase 2: Onboarding
**Goal**: A new user can identify themselves by phone number, the bot finds their orders in both systems, and the identity is persisted for all future sessions
**Depends on**: Phase 1
**Requirements**: ONBR-01, ONBR-02, ONBR-03, ONBR-04, ONBR-05, ONBR-06
**Success Criteria** (what must be TRUE):
  1. User sends /start and sees a branded welcome message with a prompt to enter their phone number
  2. User enters a phone in +380... format and the bot accepts it; invalid formats are rejected with a Ukrainian error message
  3. If the phone matches orders in Shopify or KeyCRM (or both), the user proceeds to the main menu
  4. If the phone is not found in either system, the user sees a friendly Ukrainian message explaining next steps
  5. After successful identification, the chat_id-to-phone mapping is stored in SQLite so the user does not re-enter their phone next time
**Plans**: TBD

### Phase 3: Core Features
**Goal**: Identified users can view their full order history, navigate info pages, reach live support, and manage their settings — the complete user-facing product
**Depends on**: Phase 2
**Requirements**: NAVG-01, NAVG-02, NAVG-03, NAVG-04, NAVG-05, ORDR-01, ORDR-02, ORDR-03, ORDR-04, INFO-01, INFO-02, INFO-03, INFO-04, SUPP-01, SUPP-02, SETT-01, SETT-02
**Success Criteria** (what must be TRUE):
  1. Main menu shows 5 buttons (Orders, Find more, Contact support, Website, Settings); "Find more" and "Settings" open submenus with Back buttons that return to parent
  2. User taps Orders and sees their full order history merged from Shopify and KeyCRM, with status, product names, amounts, delivery date, and source (web / Instagram) for each order
  3. User taps "Find more" and can read About us, Contacts, Payment, and Delivery pages — all in Ukrainian
  4. User taps "Contact support" and their message is forwarded to the support person; support person's reply reaches the user back through the bot
  5. User taps Settings, can change their linked phone number (re-validates against both APIs), and sees Language option showing Ukrainian as active
**Plans**: TBD

### Phase 4: Broadcast
**Goal**: Admin can send broadcast messages to all opted-in users; users can opt out at any time and will not receive further messages
**Depends on**: Phase 3
**Requirements**: BRDC-01, BRDC-02
**Success Criteria** (what must be TRUE):
  1. User sends /stop or /unsubscribe and immediately stops receiving any bot-initiated messages; opt-out status is persisted in SQLite
  2. Admin sends a broadcast command and all opted-in users receive the message; opted-out users receive nothing
  3. Broadcast does not trigger Telegram flood control (rate limiting enforced); bot remains operational after a full broadcast run
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 3/3 | Complete   | 2026-03-02 |
| 2. Onboarding | 0/TBD | Not started | - |
| 3. Core Features | 0/TBD | Not started | - |
| 4. Broadcast | 0/TBD | Not started | - |
