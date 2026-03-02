# Project Research Summary

**Project:** KS Telegram Bot (beauty shop customer service bot)
**Domain:** Telegram e-commerce bot — order tracking, FAQ, live support, broadcasts
**Researched:** 2026-03-02
**Confidence:** MEDIUM — stack is HIGH (live-verified from PyPI); features, architecture, and pitfalls are MEDIUM (training knowledge, cutoff August 2025)

## Executive Summary

This is a customer-service Telegram bot for a Ukrainian beauty shop, built to let customers look up their order status, get FAQ answers, and escalate to a human support agent — all without leaving Telegram. The stack is simple and well-established: aiogram 3 as the async Telegram framework, Google Sheets as the data source (orders, subscriber list), and APScheduler for broadcast scheduling. The entire stack is Python-native, runs as a single long-polling process on a VPS, and requires no database. This architecture is appropriate for the user scale (small shop, hundreds of subscribers at most) and the existing data reality (orders already live in Google Sheets).

The recommended approach is to build in strict dependency order: get credentials and config right before writing any code, build the Google Sheets service layer with caching before any feature touches order data, and complete the onboarding flow (phone collection + Sheets lookup) before building any downstream features like order display or support escalation. Every other feature depends on a known, verified phone number — this is the critical path. Broadcast must be implemented last and with rate limiting built in from the start, not added as an afterthought.

The key risks are Google Sheets API quota exhaustion (hit before users notice anything is wrong), Telegram flood control banning the bot mid-broadcast, and credentials being committed to git before .gitignore is configured. All three are foundational problems that are trivial to prevent at setup time but expensive to fix after the fact. A secondary risk specific to this project is Google Sheets silently coercing phone numbers to floats, which causes 100% lookup failures — this must be handled with defensive normalization in the Sheets service layer.

## Key Findings

### Recommended Stack

The stack is fully resolved and verified from live PyPI metadata. All packages install without conflicts on Python 3.14.2 (the version already installed in the project environment). aiogram 3.25.0 is the correct choice: it is the dominant async Telegram framework as of 2025-2026, has FSM and router-based handlers built in, and explicitly supports Python 3.14. gspread 6.2.1 is the correct Sheets client; note that online tutorials frequently show 5.x patterns — the 6.x API has breaking authentication changes, so only follow 6.x docs. APScheduler 3.11.2 handles scheduled broadcasts inside the asyncio event loop without a separate worker.

**Core technologies:**
- Python 3.14.2: runtime — already installed, all packages confirmed compatible
- aiogram 3.25.0: Telegram bot framework — async-first, FSM built-in, router-based handlers, dominant choice for new Python bots
- gspread 6.2.1: Google Sheets read/write — de-facto library for Sheets API v4, handles service account auth headlessly
- google-auth 2.48.0: Google API authentication — required by gspread, handles service account JWT flow
- python-dotenv 1.2.2: environment variable loading — keeps bot token and service account credentials out of source code
- loguru 0.7.3: structured logging — drop-in replacement for stdlib logging with async safety and file rotation
- APScheduler 3.11.2: scheduled broadcasts — runs periodic tasks inside the asyncio event loop, use AsyncIOScheduler
- cachetools 7.0.1: in-memory TTL cache — essential for Sheets API quota management; not optional

**Do not use:** aiogram 2.x (EOL, completely different API), telebot/pyTelegramBotAPI (no FSM, synchronous), gspread 5.x (breaking API changes in 6.x), SQLite or any database (Sheets is the explicit data source), threading (use asyncio throughout).

### Expected Features

The feature set is clearly defined. The project scope is post-purchase customer service only — not a storefront, not an AI assistant, not a multi-agent helpdesk. This constraint rules out a large category of "obvious" enhancements that would double the scope.

**Must have (table stakes) — v1:**
- /start command + welcome message with configurable brand name — entry point
- Phone number collection + Ukrainian format validation (+380...) — identifies the customer
- Google Sheets lookup by phone — links identity to order data
- Order history display (all orders, not just latest) — core value proposition
- Main menu via inline keyboard (My Orders / Support / About) — standard bot UX
- FAQ with button-based navigation — deflects top common questions
- Escalate to human support (forward to support Telegram account) — essential fallback
- /stop and /unsubscribe with persistent opt-out — legal and ethical baseline
- Broadcast to all opted-in users (promos, new arrivals) — core marketing need per PROJECT.md
- All UI text in Ukrainian — non-negotiable for target audience
- Error handling: Sheets API down, phone not found, no orders — users must never get silence

**Should have (differentiators) — v1.x after validation:**
- Personalized greeting with customer name from Sheet — low cost, warm touch
- Conversation context forwarding to support agent — reduces agent confusion
- Targeted broadcast (filter by order status) — higher marketing value
- Order search by order number — fallback when phone lookup fails

**Defer to v2+:**
- Order status change proactive broadcast — requires Sheet change-detection infrastructure; significant added complexity; validate demand first
- Deep link to specific order — low urgency
- Admin broadcast targeting with segmentation — add when marketing sophistication grows

**Do not build:** OTP/SMS verification, payment processing, multi-language support, multiple support agents, web admin dashboard, full product catalog, AI/LLM free-form Q&A, real-time courier tracking, automatic write-back to Sheets.

### Architecture Approach

The system uses a clean handler-service separation: aiogram Routers own all Telegram-specific logic, and a services layer (SheetsService, UserService, SupportService, BroadcastService) owns all business logic with no aiogram imports. Services are instantiated once at startup and injected into handlers via aiogram's dependency injection mechanism. Google Sheets calls are synchronous (gspread) and must be wrapped in asyncio.to_thread() to avoid blocking the event loop — this is mandatory, not optional. All Ukrainian strings live in a single messages.py module; all configuration lives in .env read via pydantic Settings. FSM is used only for conversation flow control (onboarding, support session) — not for data persistence.

**Major components:**
1. Dispatcher + Routers — aiogram entry point; one Router per feature module (onboarding, orders, faq, support, broadcast, common)
2. Config (config.py) — pydantic Settings reading from .env; bot token, sheet ID, support chat ID, brand name
3. Messages (messages.py) — all Ukrainian UI strings with brand variable substitution
4. SheetsService — reads order rows, writes subscriber records; wraps gspread in async; caches with TTL
5. UserService — maps chat_id to phone_number; tracks opt-in/opt-out state; persists to Sheets Subscribers tab
6. SupportService — forwards customer messages to support chat; maintains forwarded_msg_id to customer_chat_id mapping for reply routing
7. BroadcastService — iterates opted-in subscriber list; enforces rate limit; handles TelegramForbiddenError (user blocked bot)
8. FSM States (states.py) — OnboardingStates, SupportStates, BroadcastStates; all in one file to prevent import cycles
9. Keyboards (keyboards/) — pure functions returning InlineKeyboardMarkup; no logic, no Telegram types in service layer

**Recommended project structure:**
```
ks-tg-bot/
├── bot/
│   ├── main.py              # entry point: Bot, Dispatcher, polling
│   ├── config.py            # pydantic Settings from .env
│   ├── states.py            # all FSM StatesGroups
│   ├── messages.py          # all Ukrainian strings
│   ├── handlers/            # one Router per feature
│   ├── services/            # pure Python, no aiogram imports
│   └── keyboards/           # keyboard builder functions
├── .env                     # secrets (never committed)
├── .env.example             # template (committed)
└── requirements.txt
```

### Critical Pitfalls

1. **Google Sheets API quota exhaustion** — implement TTL-cached batch reads (cachetools.TTLCache, 30-120s) in SheetsService before any feature touches order data. Calling the API per user request hits quota in minutes under real traffic. This is foundational infrastructure, not an optimization.

2. **Telegram broadcast flood control banning the bot** — never loop bot.send_message() without rate limiting. Cap at 25 messages/second, catch TelegramRetryAfter and sleep the specified duration, log per-recipient delivery status. Design with rate limiting from the start, not as a retrofit.

3. **Phone numbers stored as floats in Google Sheets** — Sheets silently coerces +380671234567 to 3.8067e+11. Normalize all phone values on read: convert to string, strip whitespace and formatting, re-add + prefix if missing. Normalize user input the same way. Test with raw API response inspection before building lookup logic.

4. **Credentials committed to git** — configure .gitignore to exclude .env, service_account.json, and config.json before the first commit. Use .env.example with placeholder values as the committed reference. Must be done pre-first-commit; retroactive removal from git history is expensive.

5. **Opt-out not enforced in broadcast** — /stop must write opt-out state to persistent storage immediately when the command is handled. Broadcast must filter against this list before sending. Test the full flow: opt out, trigger broadcast, verify no delivery. A missed opt-out is a legal violation, not just a UX issue.

6. **Synchronous gspread calls blocking the event loop** — all gspread calls must be wrapped in asyncio.to_thread(). Calling them directly in async handlers blocks all concurrent users for the duration of every Sheets request. Breaks with 2+ simultaneous users.

7. **FSM state breakage from unhandled entry points** — handle /start as a state-resetting entry point from anywhere; register fallback handlers in every FSM state that re-display the keyboard; never assume a clean linear flow.

## Implications for Roadmap

Based on combined research, the component dependency graph suggests a 5-phase build order. The critical path runs through credentials to Sheets service with cache to onboarding flow to feature modules to broadcast. Nothing downstream of onboarding should be built until phone lookup is confirmed working against real data.

### Phase 1: Project Setup and Credentials

**Rationale:** Prevents the single most expensive pitfall (credentials in git) and establishes the configuration foundation everything else reads. Zero features can be built without bot token and Sheets service account in place. Must be done before any code is committed.
**Delivers:** Working project skeleton with .gitignore, .env, .env.example, pydantic config, loguru logging setup, requirements.txt with pinned versions; bot starts and responds to /start with no features yet.
**Addresses features:** None yet — but prevents credential leak pitfall and establishes configurable brand name foundation.
**Avoids pitfalls:** Credentials in git, no logging (technical debt).

### Phase 2: Google Sheets Service Layer

**Rationale:** SheetsService is a dependency of onboarding, orders, and broadcast. It must be built and tested in isolation before any feature that reads order data. The cache implementation must be part of this phase — not added later — because quota exhaustion starts immediately when real users check orders.
**Delivers:** SheetsService with TTL-cached batch read for orders sheet, subscriber write-back, phone normalization handling float coercion, asyncio.to_thread() wrapper for all sync gspread calls, and a startup health-check verifying Sheets connectivity.
**Implements:** SheetsService + UserService (subscriber registry) + Google Sheets auth via service account.
**Avoids pitfalls:** Quota exhaustion, phone number float coercion, sync call blocking event loop, no error boundary on Sheets failure.

### Phase 3: Core Bot — Onboarding and Main Menu

**Rationale:** Everything else depends on a verified, registered phone number. Onboarding is the gate. Until a user can identify themselves and reach the main menu, none of the feature modules are reachable. FSM state handling, entry point safety, and fallback handlers must be established here.
**Delivers:** /start handler with brand-configurable welcome message, phone collection with Ukrainian format validation, Sheets lookup on phone submission, UserService registration on success, main menu keyboard, /stop and /unsubscribe with persistent opt-out.
**Addresses features:** /start, phone collection, Sheets lookup, main menu, /stop — the P1 table stakes core.
**Avoids pitfalls:** FSM state breakage from unhandled entry points, missing opt-out persistence (foundation for broadcast correctness).

### Phase 4: Feature Modules — Orders, FAQ, Support

**Rationale:** These three features share no dependencies on each other and can be developed in parallel (or in any order) once onboarding is working. They all depend on UserService.get_phone(chat_id) being available, which Phase 3 delivers. Group them in one phase because they collectively complete the core user value proposition.
**Delivers:** Order history display (all orders formatted in Ukrainian with status mapped to Ukrainian strings), FAQ with button-based navigation deflecting top questions, support escalation with context forwarding and agent reply relay, error handling for all Sheets failures with user-facing Ukrainian messages.
**Addresses features:** Order history, FAQ auto-responses, escalate to human, error handling — all P1.
**Uses:** SheetsService (orders), UserService (phone lookup), SupportService (agent session relay).
**Avoids pitfalls:** Raw English status strings shown to users, no context forwarded to support agent, CallbackQuery not answered after inline button tap.

### Phase 5: Broadcast

**Rationale:** Broadcast is last because it depends on a complete subscriber list (populated by Phase 3 onboarding) and must be tested against real subscriber data to verify rate limiting and opt-out enforcement. The blast radius of a misconfigured broadcast (legal violations, bot banning) makes it the highest-risk feature — build it with full infrastructure in place.
**Delivers:** Admin-triggered broadcast via /broadcast command in support chat, rate-limited fan-out (25 msg/s ceiling), TelegramRetryAfter handling, opt-out list enforcement (tested end-to-end), TelegramForbiddenError handling (auto-removes blocked users), delivery confirmation to admin.
**Addresses features:** Broadcast to all opted-in users — P1 per PROJECT.md.
**Avoids pitfalls:** Broadcast flood control, opt-out not enforced.

### Phase Ordering Rationale

- Phases 1 and 2 are pure infrastructure — no user-visible features, but everything downstream requires them. Skipping or deferring this work creates technical debt that is expensive to fix (credentials in git, missing cache causing production quota failures).
- Phase 3 is the critical path. The onboarding flow gates all feature modules — there is no way to test orders, FAQ, or support without a registered user.
- Phase 4 bundles three features that are independent of each other but collectively satisfy the core user value prop. Building them together means a single integration test can cover the full happy path.
- Phase 5 is deliberately last: it has the highest regulatory and operational risk, requires real subscriber data to test, and is not needed to validate the core product with early users.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2 (Sheets Service):** gspread 6.x API differs significantly from 5.x tutorials. Verify worksheet methods, auth flow, and batch read patterns against 6.x docs at https://docs.gspread.org before implementation. Also verify Google Sheets API quota limits at https://developers.google.com/sheets/api/limits — the 60 requests/minute figure is from training data.
- **Phase 5 (Broadcast):** Verify current Telegram flood control limits (30 msg/s global, 1 msg/s per user) at https://core.telegram.org/bots/faq before implementing rate limiter. Evaluate whether aiogram-broadcaster library handles rate limiting better than manual APScheduler implementation.

Phases with standard patterns (skip `/gsd:research-phase`):
- **Phase 1 (Setup):** Standard Python project setup with dotenv and pydantic — well-documented, no surprises.
- **Phase 3 (Onboarding):** aiogram 3 FSM patterns are well-documented; implementation is straightforward following the Router + StatesGroup pattern.
- **Phase 4 (Feature Modules):** All three features follow established patterns documented in ARCHITECTURE.md. Support relay pattern is the most complex but is fully worked out.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified live from PyPI metadata and dry-run install on Python 3.14.2; no conflicts confirmed |
| Features | MEDIUM | Core Telegram bot patterns are stable and well-established; Ukrainian e-commerce market specifics not verified against current market data |
| Architecture | MEDIUM | aiogram 3 patterns from training knowledge; verify at https://docs.aiogram.dev before implementation; gspread 6.x patterns need live docs check |
| Pitfalls | MEDIUM | Rate limits and quota figures from training data (cutoff August 2025); verify current values from official sources before implementing rate limiters |

**Overall confidence:** MEDIUM-HIGH — stack is fully resolved; patterns are well-established; specific API limits need verification against current official docs before each implementation phase.

### Gaps to Address

- **Telegram flood control limits:** The 30 msg/s global and 1 msg/s per-user limits are from training data. Verify at https://core.telegram.org/bots/faq before implementing broadcast rate limiter.
- **Google Sheets API quota:** The 60 reads/minute free-tier limit should be verified at https://developers.google.com/sheets/api/limits before finalizing cache TTL values.
- **gspread 6.x API specifics:** Many tutorials and training data reference 5.x patterns. Read the 6.x changelog and docs at https://docs.gspread.org before writing SheetsService.
- **Ukrainian data protection law (opt-out requirements):** Confidence is LOW on exact legal requirements. Verify commercial messaging opt-out obligations with the shop owner or a legal source before the broadcast phase.
- **Actual Google Sheets data schema:** ARCHITECTURE.md documents the expected schema (phone, name, order_id, product, status, delivery_date) but the real sheet may have different column names or ordering. Inspect the real sheet before writing SheetsService.

## Sources

### Primary (HIGH confidence)
- PyPI live metadata — aiogram 3.25.0, gspread 6.2.1, google-auth 2.48.0, python-dotenv 1.2.2, loguru 0.7.3, apscheduler 3.11.2, ruff 0.15.4, pytest-asyncio 1.3.0 (all verified 2026-03-02 via pip index versions and wheel METADATA)
- Dry-run dependency resolution — pip install --dry-run aiogram==3.25.0 gspread==6.2.1 confirmed no conflicts on Python 3.14.2 (run 2026-03-02)
- PROJECT.md — direct project requirements (HIGH confidence for project constraints)

### Secondary (MEDIUM confidence)
- aiogram 3.x documentation patterns — training knowledge (cutoff August 2025); verify at https://docs.aiogram.dev/en/latest/
- gspread documentation patterns — training knowledge; verify at https://docs.gspread.org
- Telegram Bot API rate limit behavior — training knowledge; verify at https://core.telegram.org/bots/faq
- Google Sheets API quota documentation — training knowledge; verify at https://developers.google.com/sheets/api/limits
- Small e-commerce Telegram bot patterns — training knowledge, widely observed patterns as of August 2025

### Tertiary (LOW confidence)
- Ukrainian e-commerce market specifics (carrier integrations, customer behavior) — training knowledge, no direct market data available; validate with shop owner
- Ukrainian data protection law opt-out requirements — training knowledge; verify with legal counsel before broadcast phase

---
*Research completed: 2026-03-02*
*Ready for roadmap: yes*
