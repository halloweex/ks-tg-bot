# Architecture Research

**Domain:** Telegram e-commerce bot with Google Sheets backend
**Researched:** 2026-03-02
**Confidence:** MEDIUM — based on training knowledge of aiogram 3 patterns; external verification tools unavailable. Core patterns are stable and well-established.

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        TELEGRAM PLATFORM                          │
│                  (sends updates via polling/webhook)              │
└───────────────────────────────┬──────────────────────────────────┘
                                │ Updates (messages, callbacks)
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                        BOT PROCESS                                │
│                                                                   │
│  ┌──────────────┐   ┌──────────────────────────────────────┐    │
│  │  Dispatcher  │   │             Routers                   │    │
│  │  (aiogram)   │──▶│  ┌──────────┐  ┌───────────────────┐ │    │
│  └──────────────┘   │  │ onboard  │  │    orders         │ │    │
│                      │  │ router   │  │    router         │ │    │
│  ┌──────────────┐   │  └──────────┘  └───────────────────┘ │    │
│  │    Config    │   │  ┌──────────┐  ┌───────────────────┐ │    │
│  │  (.env/yaml) │   │  │   faq    │  │    support        │ │    │
│  └──────────────┘   │  │  router  │  │    router         │ │    │
│                      │  └──────────┘  └───────────────────┘ │    │
│  ┌──────────────┐   │  ┌──────────┐  ┌───────────────────┐ │    │
│  │    State     │   │  │broadcast │  │    admin          │ │    │
│  │   Storage    │   │  │  router  │  │    router         │ │    │
│  │ (FSM: mem/   │   │  └──────────┘  └───────────────────┘ │    │
│  │  Redis)      │   └──────────────────────────────────────┘    │
│  └──────────────┘                                                 │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                     Services Layer                         │   │
│  │  ┌──────────────────┐   ┌────────────────────────────┐   │   │
│  │  │  SheetsService   │   │     UserService             │   │   │
│  │  │  (read orders,   │   │  (phone lookup, subscriber  │   │   │
│  │  │   write sub list)│   │   registry, opt-out)        │   │   │
│  │  └──────────────────┘   └────────────────────────────┘   │   │
│  │  ┌──────────────────┐   ┌────────────────────────────┐   │   │
│  │  │  BroadcastService│   │     SupportService          │   │   │
│  │  │  (send to all    │   │  (forward to agent,         │   │   │
│  │  │   subscribers)   │   │   relay replies back)       │   │   │
│  │  └──────────────────┘   └────────────────────────────┘   │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                                │
              ┌─────────────────┼──────────────────┐
              ▼                 ▼                  ▼
┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  Google Sheets  │  │  Support Agent   │  │  Subscribers     │
│  (order data,   │  │  Telegram Chat   │  │  JSON/Sheet      │
│   subscriber    │  │  (one person)    │  │  (opt-in list)   │
│   opt-in list)  │  └──────────────────┘  └──────────────────┘
└─────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Dispatcher | Entry point; routes updates to routers; manages middleware | `aiogram.Dispatcher` with `MemoryStorage` or `RedisStorage` |
| Routers | Group related handlers by feature; register with Dispatcher | `aiogram.Router` instances, one per feature module |
| FSM States | Define conversation states (e.g., awaiting phone number) | `aiogram.fsm.state.StatesGroup` subclasses |
| Config | Holds bot token, sheet ID, admin chat IDs, brand text | `pydantic.BaseSettings` reading from `.env` file |
| SheetsService | Reads order rows, writes subscriber records to Google Sheets | `gspread` or `google-api-python-client` with service account |
| UserService | Maps `chat_id → phone_number`; tracks opt-in/opt-out status | In-memory dict + Sheets write-back (small scale) |
| SupportService | Forwards customer messages to support chat; relays replies | Uses `forward_message` and stores `customer_chat_id` mapping |
| BroadcastService | Sends a message to all opted-in subscribers | Iterates subscriber list, calls `bot.send_message` per user |
| Messages module | All Ukrainian text strings; brand-configurable variables | Python module with constants or a YAML file |

## Recommended Project Structure

```
ks-tg-bot/
├── bot/
│   ├── __init__.py
│   ├── main.py              # Entry point: create Bot, Dispatcher, start polling
│   ├── config.py            # Pydantic Settings — reads .env
│   ├── states.py            # All FSM StatesGroups in one place
│   ├── messages.py          # All Ukrainian UI strings, brand variables
│   │
│   ├── handlers/            # One module per feature, each exports a Router
│   │   ├── __init__.py
│   │   ├── onboarding.py    # /start, phone collection, phone validation
│   │   ├── orders.py        # "My Orders" button, order list, order detail
│   │   ├── faq.py           # FAQ button, keyword matching, auto-responses
│   │   ├── support.py       # Escalation to agent, relay loop, /close
│   │   ├── broadcast.py     # Admin command to send broadcast message
│   │   └── common.py        # /stop, /unsubscribe, fallback handler
│   │
│   ├── services/            # Business logic, no Telegram types here
│   │   ├── __init__.py
│   │   ├── sheets.py        # Google Sheets read/write via gspread
│   │   ├── users.py         # Phone→chat_id registry, opt-in/opt-out
│   │   ├── support.py       # Support session tracking (customer↔agent mapping)
│   │   └── broadcast.py     # Fan-out logic, skip opted-out users
│   │
│   └── keyboards/           # Inline and reply keyboard builders
│       ├── __init__.py
│       ├── main_menu.py     # Main menu keyboard (My Orders, FAQ, Support)
│       └── order.py         # Order detail navigation if multi-order
│
├── .env                     # BOT_TOKEN, SHEET_ID, SUPPORT_CHAT_ID, BRAND_NAME
├── .env.example             # Template committed to repo
├── requirements.txt         # Pinned dependencies
└── README.md
```

### Structure Rationale

- **handlers/**: Each file is one feature, exports one `Router`. This means you can add or disable a feature by including or excluding its router in `main.py`. No feature knows about another feature's handlers.
- **services/**: Pure Python classes with no aiogram imports. Handlers call services; services do not call handlers. This boundary makes services independently testable.
- **messages.py**: All Ukrainian strings live in one place. To change brand name or any copy, edit one file. This satisfies the "configurable brand name" requirement without scattered string literals.
- **keyboards/**: Keyboard builders separated from handlers. Handlers stay focused on logic, not layout.
- **states.py**: All `StatesGroup` subclasses in one file prevents import cycles between handler modules.

## Architectural Patterns

### Pattern 1: Handler-Router Decomposition (aiogram 3)

**What:** Each feature module creates its own `Router` instance, registers handlers on it, and the `main.py` includes all routers into the Dispatcher. Handlers are decorated directly on the Router, not on a global bot/dispatcher object.

**When to use:** Always in aiogram 3. This is the canonical pattern replacing the single-dispatcher approach from aiogram 2.

**Trade-offs:** Slightly more boilerplate than aiogram 2, but enables clean module boundaries and handler isolation.

**Example:**
```python
# handlers/orders.py
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from bot.services.sheets import SheetsService

router = Router()

@router.message(F.text == "Мої замовлення")
async def show_orders(message: Message, sheets: SheetsService):
    orders = await sheets.get_orders_by_phone(message.chat.id)
    # format and send

# main.py
from aiogram import Dispatcher
from bot.handlers import onboarding, orders, faq, support

dp = Dispatcher()
dp.include_router(onboarding.router)
dp.include_router(orders.router)
dp.include_router(faq.router)
dp.include_router(support.router)
```

### Pattern 2: FSM for Multi-Step Conversations

**What:** Finite State Machine tracks where each user is in a conversation. Used for phone number collection (onboarding) and support escalation. aiogram 3 has built-in FSM with `MemoryStorage` (default) or `RedisStorage` (for persistence across restarts).

**When to use:** Any time the bot needs more than one message from the user in sequence. Phone collection requires: (1) ask for phone, (2) receive phone, (3) validate, (4) confirm. Support escalation requires: (1) enter support mode, (2) relay messages until agent closes session.

**Trade-offs:** `MemoryStorage` is lost on bot restart — users mid-onboarding lose state. For a small bot with few users, this is acceptable. `RedisStorage` persists state but requires a Redis instance.

**Example:**
```python
# states.py
from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    waiting_for_phone = State()

class SupportStates(StatesGroup):
    in_support_session = State()

# handlers/onboarding.py
from aiogram.fsm.context import FSMContext
from bot.states import OnboardingStates

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.set_state(OnboardingStates.waiting_for_phone)
    await message.answer("Будь ласка, введіть ваш номер телефону у форматі +380...")

@router.message(OnboardingStates.waiting_for_phone)
async def receive_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not is_valid_ua_phone(phone):
        await message.answer("Невірний формат. Спробуйте ще раз: +380...")
        return
    await state.clear()
    # proceed to main menu
```

### Pattern 3: Service Injection via Middleware

**What:** Services (SheetsService, UserService) are created once at startup and injected into handlers via aiogram's dependency injection — passing them as keyword arguments to handlers through middleware or dispatcher data.

**When to use:** Any service that should be a singleton (one Google Sheets connection, one user registry). Avoids creating a new Sheets API connection per update.

**Trade-offs:** Requires understanding aiogram 3's DI mechanism. The alternative (global variables) works but is harder to test.

**Example:**
```python
# main.py
from bot.services.sheets import SheetsService
from bot.services.users import UserService

sheets = SheetsService(sheet_id=config.SHEET_ID)
users = UserService()

dp = Dispatcher()
dp["sheets"] = sheets   # injected into handlers as `sheets: SheetsService`
dp["users"] = users

# handlers/orders.py
@router.message(F.text == "Мої замовлення")
async def show_orders(message: Message, sheets: SheetsService, users: UserService):
    phone = users.get_phone(message.from_user.id)
    orders = await sheets.get_orders_by_phone(phone)
```

### Pattern 4: Support Session Relay

**What:** When a customer enters support mode, all their subsequent messages are forwarded to a support agent's chat. The agent's replies (in the same support chat) are forwarded back to the customer. A mapping `{agent_message_id: customer_chat_id}` enables routing replies correctly.

**When to use:** This exact pattern — single support agent in a dedicated Telegram chat/group.

**Trade-offs:** Simple and requires zero external tooling. Breaks if the agent starts unrelated conversations in the same chat. Using a dedicated group for support avoids contamination.

**Example:**
```python
# services/support.py
class SupportService:
    def __init__(self, support_chat_id: int):
        self.support_chat_id = support_chat_id
        # maps forwarded message_id → original customer chat_id
        self._sessions: dict[int, int] = {}

    async def escalate(self, bot, customer_chat_id: int, first_message):
        sent = await bot.forward_message(
            chat_id=self.support_chat_id,
            from_chat_id=customer_chat_id,
            message_id=first_message.message_id
        )
        self._sessions[sent.message_id] = customer_chat_id

    def get_customer_id(self, reply_to_message_id: int) -> int | None:
        return self._sessions.get(reply_to_message_id)
```

## Data Flow

### Onboarding Flow (phone collection)

```
Customer sends /start
    ↓
onboarding.cmd_start handler
    → sets FSM state: OnboardingStates.waiting_for_phone
    → sends greeting message with brand name from config
    ↓
Customer sends phone number
    ↓
onboarding.receive_phone handler (filtered by FSM state)
    → validates format (+380...)
    → SheetsService.find_customer_by_phone(phone)
        → Google Sheets API: reads customer sheet, searches phone column
    → if found: UserService.register(chat_id, phone), clear FSM state, show main menu
    → if not found: send "not found" message, keep state (ask again)
```

### Order Lookup Flow

```
Customer taps "Мої замовлення" button
    ↓
orders.show_orders handler
    → UserService.get_phone(chat_id)  [in-memory lookup]
    → SheetsService.get_orders_by_phone(phone)
        → Google Sheets API: filter orders sheet by phone column
        → returns list of Order objects
    → format orders as Ukrainian text
    → send message with order list
```

### Support Escalation Flow

```
Customer taps "Підтримка" button
    ↓
support.start_support handler
    → FSM: set SupportStates.in_support_session
    → SupportService.escalate(bot, chat_id, initial_message)
        → bot.forward_message → support agent's chat
        → stores {forwarded_msg_id: customer_chat_id}
    ↓
Customer sends more messages (while in SupportStates.in_support_session)
    ↓
support.relay_to_agent handler
    → bot.forward_message → support chat
    ↓
Agent replies (in support chat, replies to forwarded message)
    ↓
support.relay_to_customer handler (triggered in support chat)
    → SupportService.get_customer_id(reply_to.message_id)
    → bot.send_message(customer_chat_id, agent_reply_text)
```

### Broadcast Flow

```
Admin sends /broadcast command in support chat
    ↓
broadcast.start_broadcast handler (admin-only, chat_id filter)
    → FSM: set BroadcastStates.waiting_for_message
    ↓
Admin sends broadcast message text
    ↓
broadcast.send_broadcast handler
    → UserService.get_all_subscribers() [opted-in users only]
    → BroadcastService.send_to_all(bot, subscriber_list, message_text)
        → for each subscriber: bot.send_message(chat_id, text)
        → skip: opted-out users
        → handle: TelegramForbiddenError (user blocked bot → mark as opted-out)
    → send confirmation to admin
```

### Key Data Stores

| Store | What Lives There | Technology |
|-------|-----------------|------------|
| Google Sheets (Orders tab) | phone, name, order_id, product, status, delivery_date | Google Sheets API |
| Google Sheets (Subscribers tab) | chat_id, phone, opt_in_date, opted_out flag | Google Sheets API (write on onboarding) |
| In-memory dict | chat_id → phone mapping (fast lookup during session) | Python dict in UserService |
| FSM Storage | Per-user conversation state (onboarding step, support mode) | aiogram MemoryStorage (restart-lossy) |
| Support Sessions | forwarded_message_id → customer_chat_id (ephemeral) | Python dict in SupportService |

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Telegram Bot API | Long polling via aiogram (`dp.start_polling(bot)`) | Polling is simpler than webhook for single-server deploy; no public URL required |
| Google Sheets API | Service account credentials + gspread library | Credentials JSON file referenced in `.env`; never committed to repo |
| Support Agent Chat | Bot API forward/send messages to a specific Telegram chat ID | Support agent chat ID stored in config; agent must start the bot or be in a group where bot is admin |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Handler → Service | Direct Python function/method call | Handlers call service methods; services return domain objects, not Telegram types |
| Handler → Keyboard | Direct Python function call returning `InlineKeyboardMarkup` | Keyboards are pure functions: input=data, output=markup object |
| Handler → Messages | Direct string access | `messages.GREETING.format(brand=config.BRAND_NAME)` pattern |
| Service → Google Sheets | HTTP via gspread (sync or async wrapper) | gspread is sync; use `asyncio.to_thread()` to avoid blocking the event loop |
| SupportService → Telegram | Via `bot` object passed into service methods | Do NOT import bot globally; pass as argument to keep services testable |

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 0-500 subscribers | Current architecture is sufficient. MemoryStorage, in-memory dicts, sync Sheets calls wrapped in `asyncio.to_thread()`. |
| 500-5,000 subscribers | Google Sheets API has 60 reads/min quota per user. Cache subscriber list in memory, refresh every N minutes. Broadcasts become slow (one message/user, rate limit ~30 msg/sec). Add sleep between broadcast sends. |
| 5,000+ subscribers | Google Sheets becomes a bottleneck. Migrate order data to SQLite or PostgreSQL. Keep Sheets as human-visible view only. Use Redis for FSM state persistence. Batched broadcast with queue. |

### Scaling Priorities

1. **First bottleneck — Google Sheets API quota:** Sheets has a 60-reads-per-minute quota on the free tier. At moderate traffic (multiple users checking orders simultaneously), this will trigger `429 Too Many Requests`. Fix: in-memory cache of the orders sheet, refreshed every 60 seconds.

2. **Second bottleneck — Broadcast rate limits:** Telegram limits bots to ~30 messages/second. For 1,000 subscribers, a broadcast takes ~33 seconds. For 5,000, ~3 minutes. Fix: add `asyncio.sleep(0.05)` between sends and handle `RetryAfter` exceptions from Telegram.

## Anti-Patterns

### Anti-Pattern 1: Global Bot Instance in Services

**What people do:** Import the `bot` object globally at the top of service files to call `bot.send_message()` directly from services.

**Why it's wrong:** Creates circular imports, makes services untestable without a real bot, and couples services to the Telegram API.

**Do this instead:** Pass `bot` as a parameter to service methods that need to send messages, or keep all Telegram calls inside handlers and have services return data only.

### Anti-Pattern 2: Calling Google Sheets API Synchronously in Handlers

**What people do:** Call `gspread` methods directly in async handlers without wrapping in `asyncio.to_thread()`.

**Why it's wrong:** gspread is a synchronous library. Calling it directly in an async handler blocks the entire event loop, freezing the bot for all users during every Sheets call.

**Do this instead:**
```python
# In SheetsService:
async def get_orders_by_phone(self, phone: str) -> list[Order]:
    return await asyncio.to_thread(self._sync_get_orders, phone)

def _sync_get_orders(self, phone: str) -> list[Order]:
    # actual gspread calls here
    rows = self.worksheet.get_all_records()
    return [Order(**r) for r in rows if r["phone"] == phone]
```

### Anti-Pattern 3: Storing All State in FSM

**What people do:** Store the user's phone number, their order list, and display preferences all in FSM state data.

**Why it's wrong:** FSM is designed for conversation control flow, not data persistence. It uses more storage than needed and is lost on restart. Data for the current session clutters state context.

**Do this instead:** FSM state holds only the current conversation step. User data (phone, chat_id) lives in `UserService`. Order data is fetched from Sheets on demand or cached in a short-lived dict.

### Anti-Pattern 4: Hardcoding Ukrainian Strings in Handlers

**What people do:** Write `await message.answer("Вітаємо! Ваше замовлення...")` inline inside handler functions.

**Why it's wrong:** Changing any copy requires hunting through handler files. Configuring the brand name requires multiple edits. Impossible to proofread all user-facing text in one place.

**Do this instead:** All strings in `bot/messages.py`. Handlers import and use: `await message.answer(MSG.GREETING.format(brand=config.BRAND_NAME))`.

### Anti-Pattern 5: No Rate Limiting on Broadcast

**What people do:** Iterate all subscribers and call `bot.send_message()` in a tight loop without delays.

**Why it's wrong:** Telegram will return `RetryAfter` errors (HTTP 429) after ~30 messages/second. Unhandled, this crashes the broadcast or silently drops messages.

**Do this instead:** Add `asyncio.sleep(0.05)` between sends, catch `TelegramRetryAfter` and sleep for the indicated retry-after duration, log failures.

## Build Order Implications

The component dependencies create a natural build order:

1. **Config + Messages** — No dependencies. Build first. Everything else reads config and messages.
2. **SheetsService** — Depends on config (sheet ID, credentials). Build second. Testable in isolation.
3. **UserService** — Depends on SheetsService (writes subscriber rows). Build third.
4. **Onboarding handlers + FSM** — Depends on UserService + SheetsService (phone lookup). This is the critical path — nothing else works without phone registration.
5. **Keyboards + Main Menu** — Depends on config (brand name for buttons). Build alongside onboarding.
6. **Orders handlers** — Depends on UserService (get phone) + SheetsService (get orders). Build after onboarding works.
7. **FAQ handlers** — Depends on Messages (FAQ content). Standalone, no service dependency.
8. **SupportService + Support handlers** — Depends on UserService (identify customer). Needs config (support_chat_id).
9. **BroadcastService + Broadcast handlers** — Depends on UserService (subscriber list). Should be last — needs everything else working first.

## Sources

- aiogram 3 documentation architecture patterns (training knowledge, MEDIUM confidence — verify at https://docs.aiogram.dev/en/latest/)
- gspread + asyncio integration patterns (training knowledge, MEDIUM confidence)
- Telegram Bot API rate limit behavior (training knowledge, MEDIUM confidence — verify at https://core.telegram.org/bots/faq)
- Google Sheets API quota documentation (training knowledge, MEDIUM confidence — verify at https://developers.google.com/sheets/api/limits)

---
*Architecture research for: Telegram e-commerce bot with Google Sheets backend (KS Bot)*
*Researched: 2026-03-02*
