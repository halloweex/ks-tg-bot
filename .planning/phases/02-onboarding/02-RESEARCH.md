# Phase 2: Onboarding - Research

**Researched:** 2026-03-02
**Domain:** Telegram bot onboarding flow -- aiogram 3 FSM, phone validation, dual-API customer lookup, SQLite user persistence
**Confidence:** HIGH -- aiogram FSM patterns verified from official docs + installed package; phone regex tested; DB patterns confirmed from Phase 1 implementation; API clients already built and tested

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ONBR-01 | User receives welcome message with configurable brand name on /start | Modify existing `/start` handler in `bot/handlers/common.py`; check returning user in SQLite first, show greeting with phone prompt for new users, show main menu for returning users |
| ONBR-02 | User can enter phone number in +380... format with validation | Regex `^\+380\d{9}$` tested against 11 cases (all pass); aiogram FSM `StatesGroup` + `State` for `waiting_phone` state; `ERR_INVALID_PHONE` text already exists in `texts.py` |
| ONBR-03 | Bot looks up phone in Shopify API to find web orders | `ShopifyClient.get_orders_by_phone(phone)` already implemented in `bot/services/shopify.py`; phone passed with `+` prefix; client may be `None` (graceful skip) |
| ONBR-04 | Bot looks up phone in KeyCRM API to find Instagram orders | `KeyCRMClient.get_orders_by_phone(phone)` already implemented in `bot/services/keycrm.py`; phone normalized (strip `+`); always available |
| ONBR-05 | User sees helpful message if phone not found in either system | `ERR_PHONE_NOT_FOUND` text already exists in `texts.py`; check combined result length == 0; keep user in `waiting_phone` state to allow retry |
| ONBR-06 | Bot stores Telegram chat_id-to-phone mapping in SQLite | `users` table already created by `init_db()` with `chat_id INTEGER PRIMARY KEY, phone TEXT NOT NULL`; use `INSERT OR REPLACE` for upsert |
</phase_requirements>

---

## Summary

Phase 2 implements the onboarding conversation flow: when a user sends /start, they see a branded welcome message and are prompted for their phone number. The phone is validated (must match `+380XXXXXXXXX`), then looked up in both Shopify and KeyCRM APIs concurrently. If orders are found in either system, the user is considered identified and proceeds forward; their chat_id-to-phone mapping is persisted in SQLite so they never re-enter their phone. If no orders are found, the user sees a friendly Ukrainian error and can retry.

The entire Phase 1 foundation is already built: the bot skeleton runs, API clients for Shopify and KeyCRM exist with phone-based lookup, the SQLite database schema includes a `users` table, and Ukrainian text constants are defined in `texts.py`. Phase 2 adds only the conversation logic layer on top.

The core technical mechanism is aiogram 3's built-in FSM (Finite State Machine). A `StatesGroup` with a `waiting_phone` state manages the phone input flow. The `/start` handler checks SQLite for returning users (skip to main menu) or sets the FSM state to `waiting_phone` (new users). A second handler filtered on `waiting_phone` validates the phone, calls both APIs in parallel via `asyncio.gather`, and either saves the user or prompts for retry.

**Primary recommendation:** Use aiogram 3 FSM with a single `OnboardingStates.waiting_phone` state. The `/start` handler does a SQLite lookup first -- returning users skip phone entry entirely. New users enter `waiting_phone` state. Phone validation is a strict regex (`^\+380\d{9}$`). API lookups run in parallel. The flow is simple, linear, and has no branching states.

---

## Standard Stack

### Core (already installed from Phase 1)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| aiogram | 3.25.0 | Telegram bot framework + FSM | Built-in `StatesGroup`/`State`/`FSMContext`; MemoryStorage is default; no extra install needed |
| aiosqlite | 0.22.1 | Async SQLite for user persistence | Per-operation connections (Phase 1 pattern); `users` table already created |
| httpx | 0.28.1 | Async HTTP for Shopify/KeyCRM | API clients already built in `bot/services/` |

### Supporting (no new dependencies needed)

| Library | Purpose | Note |
|---------|---------|------|
| `re` (stdlib) | Phone number regex validation | `^\+380\d{9}$` -- no external library needed |
| `asyncio` (stdlib) | `asyncio.gather` for parallel API calls | Concurrent Shopify + KeyCRM lookup |

### New Dependencies

**None.** Phase 2 requires zero new packages. Everything needed is already installed.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Strict regex `^\+380\d{9}$` | `phonenumbers` library (Google) | Overkill -- we only accept one format (+380XXXXXXXXX); regex is 15 chars and has zero false positives for the Ukrainian format |
| MemoryStorage for FSM | RedisStorage | MemoryStorage is fine for this scale; FSM state is transient (onboarding takes seconds); if bot restarts mid-onboarding, user just sends /start again |
| `asyncio.gather` for parallel lookup | Sequential calls | Parallel saves ~5-10 seconds of latency (both APIs have 10s timeout); trivial to implement |

---

## Architecture Patterns

### Recommended File Changes

```
bot/
├── handlers/
│   ├── common.py         # MODIFY: /start handler becomes returning-user-aware
│   └── onboarding.py     # NEW: phone input handler with FSM state filter
├── states.py             # NEW: OnboardingStates(StatesGroup) definition
├── db.py                 # ADD: save_user() and get_user_phone() functions
├── texts.py              # ADD: MSG_PHONE_PROMPT and any missing text constants
└── __main__.py           # MODIFY: register onboarding router
```

### Pattern 1: FSM State Definition

**What:** Define a `StatesGroup` with a single `waiting_phone` state in a dedicated `bot/states.py` module. Keeps states separate from handlers for clarity.

**When to use:** Always. This is the standard aiogram 3 pattern for conversational flows.

**Example:**
```python
# bot/states.py
# Source: https://docs.aiogram.dev/en/latest/dispatcher/finite_state_machine/index.html
from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    waiting_phone = State()
```

**Why a separate file:** As the bot grows (Phase 3 settings, etc.), multiple `StatesGroup` classes will be needed. A central `states.py` avoids circular imports between handler modules.

### Pattern 2: Returning User Check in /start

**What:** The `/start` handler checks SQLite before prompting for phone. If the user's `chat_id` already has a phone mapping, skip onboarding entirely.

**When to use:** Every time `/start` is called.

**Example:**
```python
# bot/handlers/common.py (modified)
from aiogram.fsm.context import FSMContext
from bot.db import get_user_phone
from bot.states import OnboardingStates

@router.message(CommandStart())
async def cmd_start(message: Message, config: AppConfig, state: FSMContext) -> None:
    phone = await get_user_phone(message.chat.id)
    if phone:
        # Returning user — skip onboarding, show main menu
        await message.answer("Welcome back!")  # placeholder for Phase 3 main menu
        return
    # New user — show greeting and ask for phone
    greeting = texts.GREETING.format(brand_name=config.brand_name)
    await message.answer(greeting)
    await state.set_state(OnboardingStates.waiting_phone)
```

### Pattern 3: Phone Validation + Dual API Lookup

**What:** Handler filtered on `OnboardingStates.waiting_phone` validates phone format, then queries both APIs in parallel. If orders found, saves user and proceeds. If not, shows error and stays in `waiting_phone` state.

**When to use:** When user sends any message while in `waiting_phone` state.

**Example:**
```python
# bot/handlers/onboarding.py
import asyncio
import re

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot import texts
from bot.db import save_user
from bot.services.keycrm import KeyCRMClient
from bot.services.shopify import ShopifyClient
from bot.states import OnboardingStates

router = Router()

PHONE_PATTERN = re.compile(r"^\+380\d{9}$")


@router.message(OnboardingStates.waiting_phone)
async def process_phone(
    message: Message,
    state: FSMContext,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
) -> None:
    phone = message.text.strip() if message.text else ""

    if not PHONE_PATTERN.match(phone):
        await message.answer(texts.ERR_INVALID_PHONE)
        return  # stay in waiting_phone state

    await message.answer(texts.MSG_PHONE_ACCEPTED)

    # Parallel API lookup
    tasks = [keycrm.get_orders_by_phone(phone)]
    if shopify:
        tasks.append(shopify.get_orders_by_phone(phone))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect non-exception results
    found = False
    for result in results:
        if isinstance(result, list) and len(result) > 0:
            found = True
            break

    if not found:
        await message.answer(texts.ERR_PHONE_NOT_FOUND)
        return  # stay in waiting_phone for retry

    # Save user and proceed
    await save_user(message.chat.id, phone)
    await state.clear()
    await message.answer("Phone verified!")  # placeholder for Phase 3 main menu
```

### Pattern 4: SQLite User Persistence Functions

**What:** Two new functions in `bot/db.py`: `save_user(chat_id, phone)` for upserting the mapping, and `get_user_phone(chat_id)` for checking returning users.

**When to use:** `get_user_phone` on every `/start`; `save_user` after successful phone verification.

**Example:**
```python
# Added to bot/db.py
async def save_user(chat_id: int, phone: str) -> None:
    """Save or update the chat_id -> phone mapping."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (chat_id, phone) VALUES (?, ?)",
            (chat_id, phone),
        )
        await db.commit()


async def get_user_phone(chat_id: int) -> str | None:
    """Return the phone for a chat_id, or None if not registered."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT phone FROM users WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None
```

### Pattern 5: Router Registration Order

**What:** Register the onboarding router in `__main__.py` alongside the common router. Order matters: common router first (has /start with CommandStart filter), then onboarding router (has FSM state filter).

**Why order matters:** aiogram 3 processes routers in registration order. The `/start` command has a specific `CommandStart()` filter, so it matches before the `waiting_phone` state handler. No conflict because `/start` uses `CommandStart()` filter while the phone handler uses `OnboardingStates.waiting_phone` state filter -- they are mutually exclusive.

**Example:**
```python
# bot/__main__.py (modified)
from bot.handlers.onboarding import router as onboarding_router

dp.include_router(common_router)
dp.include_router(onboarding_router)
```

### Anti-Patterns to Avoid

- **Skipping returning user check:** Without SQLite lookup in `/start`, users must re-enter their phone on every bot restart or after clearing chat. The DB check is mandatory for ONBR-06.
- **Sequential API calls:** Calling Shopify, waiting, then calling KeyCRM doubles latency. Use `asyncio.gather` for parallel execution.
- **Raising exceptions on API failure:** Both API clients already return empty lists on error (never raise). Keep this pattern -- a failed API call should not prevent the other from succeeding.
- **Using FSM data to store phone temporarily:** Not needed. The phone comes from `message.text` in the same handler that validates and saves it. No need for `state.update_data(phone=...)` since there is no multi-step form.
- **Forgetting to clear FSM state after success:** Always call `await state.clear()` after saving the user. Otherwise the user stays in `waiting_phone` state and their next message will hit the phone handler instead of menu handlers.
- **Hardcoding Ukrainian text in handlers:** All strings must come from `bot/texts.py`. The existing GREETING, ERR_INVALID_PHONE, ERR_PHONE_NOT_FOUND, and MSG_PHONE_ACCEPTED constants already cover most needs.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Conversation state management | Custom state tracking in DB/dict | aiogram FSM (`StatesGroup`/`State`/`FSMContext`) | Built-in, handles per-user isolation, integrates with handler filters, no boilerplate |
| Phone number validation | Custom character-by-character parser | `re.compile(r"^\+380\d{9}$")` | One regex covers the exact requirement; `phonenumbers` library is overkill for single-format validation |
| Parallel async execution | Manual task management | `asyncio.gather(*tasks, return_exceptions=True)` | Standard library; handles exceptions cleanly; collects all results |
| User upsert in SQLite | Manual SELECT-then-INSERT logic | `INSERT OR REPLACE INTO users (chat_id, phone) VALUES (?, ?)` | SQLite handles atomicity; chat_id is PRIMARY KEY so REPLACE works correctly |

**Key insight:** Phase 2's complexity is purely conversational flow logic. Every infrastructure component (FSM, DB, API clients, text constants) is already available. The implementation is glue code between existing pieces.

---

## Common Pitfalls

### Pitfall 1: /start During Active FSM State

**What goes wrong:** User is in `waiting_phone` state, sends /start again. Without handling this, the user enters a second onboarding flow on top of the first, potentially causing confusing double-prompts.

**Why it happens:** The `CommandStart()` filter takes priority over the `waiting_phone` state filter. The handler runs, but the FSM state is still `waiting_phone` from the first attempt.

**How to avoid:** The `/start` handler should clear any existing FSM state before proceeding. Call `await state.clear()` at the beginning of `cmd_start`. This resets the flow cleanly.

**Warning signs:** User reports seeing the greeting message twice or phone prompt appearing after they already entered a phone.

### Pitfall 2: Non-Text Messages in waiting_phone State

**What goes wrong:** User sends a sticker, photo, or voice message while in `waiting_phone` state. The handler tries `message.text.strip()` and gets `None`, causing `AttributeError`.

**Why it happens:** `message.text` is `None` for non-text messages (stickers, photos, contacts, locations, etc.).

**How to avoid:** Guard with `phone = message.text.strip() if message.text else ""` before regex matching. The empty string will fail the regex, and the user sees `ERR_INVALID_PHONE`.

**Warning signs:** Unhandled exception logs mentioning `NoneType has no attribute 'strip'`.

### Pitfall 3: Phone Format -- Spaces and Dashes in User Input

**What goes wrong:** User enters `+380 67 123 4567` or `+380-67-123-4567`. The strict regex rejects it. User thinks the bot is broken.

**Why it happens:** Users naturally add formatting characters to phone numbers.

**How to avoid:** Strip spaces, dashes, and parentheses from user input BEFORE regex matching. Apply a normalization step: `phone = re.sub(r"[\s\-\(\)]", "", message.text)`. The regex then validates the cleaned number. The requirement says "+380... format" -- the bot accepts variations but stores canonical form.

**Warning signs:** High rejection rate on phone input from real users.

### Pitfall 4: API Timeout Perceived as "Phone Not Found"

**What goes wrong:** Both Shopify and KeyCRM time out (10s each, but parallel so ~10s total). The user sees "phone not found" when in reality the APIs were unreachable.

**Why it happens:** Both clients return empty lists on error. Empty list == "no orders found" in the naive check.

**How to avoid:** Distinguish between "API returned empty results" (genuine not-found) and "API raised an exception" (temporary error). Use `asyncio.gather(return_exceptions=True)` and check if all results are exceptions. If all APIs errored, show `ERR_API_UNAVAILABLE` instead of `ERR_PHONE_NOT_FOUND`. If at least one API returned a real result (even empty), that counts as a valid lookup.

**Warning signs:** "Phone not found" messages spike when an API is down.

### Pitfall 5: MemoryStorage Lost on Bot Restart

**What goes wrong:** User enters phone, bot restarts before lookup completes. User is stuck -- not in `waiting_phone` state anymore, not saved in DB. They send random messages that hit no handler.

**Why it happens:** `MemoryStorage` (default FSM storage) is ephemeral. All states are lost on restart.

**How to avoid:** This is acceptable for onboarding. The flow takes seconds; restart during it is extremely rare. User can always send `/start` again to restart the flow. No need to add Redis or persistent FSM storage for this use case.

**Warning signs:** None in practice -- this is an edge case with a trivial recovery (send /start).

---

## Code Examples

Verified patterns from official sources and Phase 1 codebase:

### Complete Onboarding Flow (aiogram 3 FSM)

```python
# Source: https://docs.aiogram.dev/en/latest/dispatcher/finite_state_machine/index.html
# Adapted for this project's onboarding requirements

# Step 1: Define states
from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    waiting_phone = State()

# Step 2: /start handler (in common.py)
@router.message(CommandStart())
async def cmd_start(message: Message, config: AppConfig, state: FSMContext) -> None:
    await state.clear()  # reset any active FSM state
    phone = await get_user_phone(message.chat.id)
    if phone:
        await message.answer("Welcome back!")  # Phase 3: show main menu
        return
    greeting = texts.GREETING.format(brand_name=config.brand_name)
    await message.answer(greeting)
    await state.set_state(OnboardingStates.waiting_phone)

# Step 3: Phone handler (in onboarding.py)
PHONE_PATTERN = re.compile(r"^\+380\d{9}$")

@router.message(OnboardingStates.waiting_phone)
async def process_phone(
    message: Message,
    state: FSMContext,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
) -> None:
    raw = message.text or ""
    phone = re.sub(r"[\s\-\(\)]", "", raw.strip())

    if not PHONE_PATTERN.match(phone):
        await message.answer(texts.ERR_INVALID_PHONE)
        return

    await message.answer(texts.MSG_PHONE_ACCEPTED)

    # Parallel lookup
    tasks = [keycrm.get_orders_by_phone(phone)]
    if shopify:
        tasks.append(shopify.get_orders_by_phone(phone))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Analyze results
    has_orders = False
    all_failed = True
    for r in results:
        if isinstance(r, list):
            all_failed = False
            if len(r) > 0:
                has_orders = True

    if all_failed:
        await message.answer(texts.ERR_API_UNAVAILABLE)
        return

    if not has_orders:
        await message.answer(texts.ERR_PHONE_NOT_FOUND)
        return

    await save_user(message.chat.id, phone)
    await state.clear()
    await message.answer("Phone verified!")  # Phase 3: main menu
```

### SQLite User Functions

```python
# Added to bot/db.py
# Source: Phase 1 per-operation connection pattern (from STATE.md decision [01-02])

async def save_user(chat_id: int, phone: str) -> None:
    """Upsert chat_id -> phone mapping. Uses INSERT OR REPLACE since chat_id is PRIMARY KEY."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (chat_id, phone) VALUES (?, ?)",
            (chat_id, phone),
        )
        await db.commit()

async def get_user_phone(chat_id: int) -> str | None:
    """Return stored phone for chat_id, or None if not registered."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT phone FROM users WHERE chat_id = ?", (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None
```

### Phone Normalization Before Validation

```python
# Strip formatting characters users commonly add
import re

raw_input = "+380 67 123-4567"
cleaned = re.sub(r"[\s\-\(\)]", "", raw_input.strip())
# Result: "+380671234567" -- now passes regex validation

# The regex validates the canonical form
PHONE_PATTERN = re.compile(r"^\+380\d{9}$")
assert PHONE_PATTERN.match(cleaned)  # True
```

### asyncio.gather with Exception Handling

```python
# Source: Python stdlib asyncio documentation
# return_exceptions=True prevents one failed API from canceling the other

import asyncio

async def lookup_both(phone, keycrm, shopify):
    tasks = [keycrm.get_orders_by_phone(phone)]
    if shopify:
        tasks.append(shopify.get_orders_by_phone(phone))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    # results[i] is either list[Order] or an Exception object
    # Check: isinstance(r, Exception) to detect failures
    return results
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| aiogram 2.x `Dispatcher.register_*` | aiogram 3.x `Router` + decorator-based filters | 2023 | Handlers use `@router.message(State)` filter syntax |
| Manual state tracking in DB | aiogram FSM `StatesGroup`/`FSMContext` | aiogram 3.0 (2023) | Per-user state isolation, auto-cleanup, handler filtering |
| `commands=["start"]` filter | `CommandStart()` filter class | aiogram 3.0 (2023) | Type-safe, supports deep links |
| Manual `.env` parsing | pydantic-settings `BaseSettings` | pydantic v2 (2023) | Validated config at startup; already used in Phase 1 |

**Deprecated/outdated:**
- `aiogram.contrib.fsm_storage.*`: aiogram 2.x storage path. In aiogram 3.x, use `aiogram.fsm.storage.*`
- `dp.register_message_handler(callback, state=State)`: aiogram 2.x registration pattern. Use decorator `@router.message(State)` in aiogram 3.x

---

## Open Questions

1. **What should the "proceed" message show after successful phone verification?**
   - What we know: Phase 3 will implement the main menu with 5 buttons (NAVG-01). Phase 2 only needs to confirm the phone was accepted.
   - What's unclear: Whether Phase 2 should show a simple text confirmation or a placeholder menu.
   - Recommendation: Show a simple "phone verified" text message. Phase 3 will replace this with the actual ReplyKeyboardMarkup main menu. No need to build menu UI in Phase 2.

2. **Should returning users who send /start see just a "welcome back" or the main menu?**
   - What we know: ONBR-06 says "the user does not re-enter their phone next time." The main menu is Phase 3 scope.
   - What's unclear: What the returning user sees in Phase 2 (before Phase 3 is built).
   - Recommendation: Show a simple "welcome back" text. Phase 3 will add the main menu ReplyKeyboardMarkup to this response.

3. **Should the bot accept phones that exist in neither system (no orders)?**
   - What we know: ONBR-05 says "user sees helpful message if phone not found in either system." The success criteria say "If the phone matches orders in Shopify or KeyCRM (or both), the user proceeds."
   - What's unclear: Whether "not found" means the user must retry or can still proceed with a warning.
   - Recommendation: Per the success criteria, treat "no orders in either system" as rejection. User stays in `waiting_phone` state and can retry with a different phone. The `ERR_PHONE_NOT_FOUND` message already suggests checking the number or contacting support.

---

## Sources

### Primary (HIGH confidence)
- aiogram 3.25.0 FSM documentation: https://docs.aiogram.dev/en/latest/dispatcher/finite_state_machine/index.html -- StatesGroup, State, FSMContext, MemoryStorage, state filter syntax
- aiogram 3 FSM example (official repo): https://github.com/aiogram/aiogram/blob/dev-3.x/examples/finite_state_machine.py -- complete working example of state transitions, data management, cancel handling
- Installed aiogram 3.25.0 package inspection (2026-03-02): `from aiogram.fsm.state import State, StatesGroup` and `from aiogram.fsm.context import FSMContext` confirmed importable; MemoryStorage is default Dispatcher storage
- Phase 1 codebase (live, 2026-03-02): `bot/handlers/common.py`, `bot/db.py`, `bot/texts.py`, `bot/services/shopify.py`, `bot/services/keycrm.py` -- all components verified working
- Phone regex tested (2026-03-02): `^\+380\d{9}$` validated against 11 test cases (valid, too short, too long, missing +, wrong country code, local format, with spaces, with letters, plain text) -- all pass correctly

### Secondary (MEDIUM confidence)
- aiosqlite `INSERT OR REPLACE` pattern: standard SQLite upsert behavior; verified by aiosqlite 0.22.1 documentation alignment with sqlite3 stdlib
- `asyncio.gather(return_exceptions=True)` pattern: Python stdlib documentation; standard pattern for parallel async tasks with independent failure handling

### Tertiary (LOW confidence)
- None. All findings verified from primary or secondary sources.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies; all libraries verified from Phase 1
- Architecture (FSM pattern): HIGH -- verified from official aiogram docs + installed package + official example
- Phone validation: HIGH -- regex tested with comprehensive test cases
- DB operations: HIGH -- follows established Phase 1 per-operation connection pattern
- Pitfalls: HIGH -- derived from real scenarios identified during Phase 1 and aiogram docs

**Research date:** 2026-03-02
**Valid until:** 2026-06-01 (90 days -- stack is stable; no version changes expected)
