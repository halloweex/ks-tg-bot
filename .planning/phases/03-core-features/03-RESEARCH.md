# Phase 3: Core Features - Research

**Researched:** 2026-03-03
**Domain:** Telegram bot UI — inline keyboards, menu navigation, order display, support relay, settings management (aiogram 3.x)
**Confidence:** HIGH

## Summary

Phase 3 transforms the bot from an onboarding-only tool into the complete user-facing product. It covers five distinct feature areas: (1) inline keyboard navigation with main menu and submenus, (2) order display merging Shopify + KeyCRM data, (3) static info pages loaded from config.yaml, (4) live support message relay between user and admin, and (5) settings for phone change and language display.

The existing codebase already has all the building blocks: API clients with `get_orders_by_phone()` methods, SQLite user storage, config.yaml with info page text fields, `support_chat_id` in config, and Ukrainian text constants in `texts.py`. The primary implementation work is writing aiogram 3 callback query handlers with `InlineKeyboardBuilder`, using `callback_query.message.edit_text()` for seamless menu transitions, and implementing `bot.forward_message()` / `bot.send_message()` for the support relay.

**Primary recommendation:** Use aiogram 3's `InlineKeyboardBuilder` + `CallbackData` factory for all menu navigation (not raw callback_data strings). Use `message.edit_text()` for in-place menu transitions (not new messages). Use `bot.forward_message()` for user-to-admin support relay with `reply_to_message` tracking for admin-to-user responses.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| NAVG-01 | Main menu with 5 buttons: Orders, Find more, Contact support, Website, Settings | InlineKeyboardBuilder with 5 buttons; Website button uses `url=` param; others use CallbackData |
| NAVG-02 | "Find more" submenu: About us, Contacts, Payment, Delivery + Back button | CallbackData factory with `menu:info` prefix; `edit_text()` to swap menu in place |
| NAVG-03 | "Settings" submenu: Change phone, Language + Back button | CallbackData with `menu:settings` prefix; Back button triggers `edit_text()` back to main menu |
| NAVG-04 | Website button opens shop URL in browser | `InlineKeyboardButton(text=..., url=config.website_url)` — no callback handler needed |
| NAVG-05 | Back buttons in all submenus return to parent menu | Single callback handler for `menu:back` that re-renders main menu via `edit_text()` |
| ORDR-01 | Order details: status, product names, amounts, delivery date | Format both `ShopifyOrder` and `KeyCRMOrder` into unified display strings with HTML formatting |
| ORDR-02 | Full order history merged from Shopify + KeyCRM | `asyncio.gather()` both API calls, merge results, sort by date descending |
| ORDR-03 | Each order shows its source (web / Instagram) | Tag orders with source label: Shopify = "web", KeyCRM = "Instagram" |
| ORDR-04 | Friendly error when no orders found or API unavailable | Use `return_exceptions=True` in gather; distinguish empty results from errors (pattern from Phase 2) |
| INFO-01 | "About us" page displays shop description | Read `config.about_text`, send via `edit_text()` with Back button |
| INFO-02 | "Contacts" page displays phone, email, social media links | Read `config.contacts_text`, send via `edit_text()` with Back button |
| INFO-03 | "Payment" page displays payment methods and terms | Read `config.payment_text`, send via `edit_text()` with Back button |
| INFO-04 | "Delivery" page displays delivery options and timelines | Read `config.delivery_text`, send via `edit_text()` with Back button |
| SUPP-01 | "Contact manager" forwards user message to support person | `bot.forward_message(chat_id=config.support_chat_id, ...)` after user enters support mode |
| SUPP-02 | Support person can reply through the bot back to customer | Detect admin reply-to-forwarded-message, extract original `chat_id`, send back via `bot.send_message()` |
| SETT-01 | User can change linked phone number (re-validates against Shopify/KeyCRM) | FSM state `SettingsStates.waiting_new_phone`, reuse phone validation logic, `save_user()` overwrites |
| SETT-02 | Language selector shows Ukrainian as active (structure ready for future) | Static display: "Українська ✅" — no switching logic needed, just a visual placeholder |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| aiogram | 3.25.0 | Bot framework — Router, CallbackData, InlineKeyboardBuilder, FSM | Already installed; all menu/callback patterns are built-in |
| httpx | 0.28.1 | Async HTTP for Shopify + KeyCRM API calls | Already installed; used by existing API clients |
| aiosqlite | 0.22.1 | Async SQLite for user data | Already installed; per-operation connection pattern established |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pyyaml | 6.0.3 | Load config.yaml (info page texts) | Already installed; config.py uses it |
| loguru | 0.7.3 | Structured logging | Already installed; all modules use it |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| InlineKeyboardBuilder | Raw InlineKeyboardMarkup dicts | Builder is cleaner, supports `.adjust()` for row layout, auto-packs CallbackData |
| CallbackData factory | Raw string callback_data | Factory gives type safety, auto-pack/unpack, works with magic filter F.field |
| forward_message for support | copy_message | forward_message shows "Forwarded from" header which helps admin identify user; copy_message is cleaner but loses sender info |

**Installation:**
```bash
# No new packages needed — all dependencies already in requirements.txt
```

## Architecture Patterns

### Recommended Project Structure
```
bot/
├── handlers/
│   ├── __init__.py          # Package marker
│   ├── common.py            # /start (existing)
│   ├── onboarding.py        # Phone collection (existing)
│   ├── menu.py              # NEW: Main menu + navigation callbacks
│   ├── orders.py            # NEW: Order display handler
│   ├── info.py              # NEW: Info page callbacks
│   ├── support.py           # NEW: Support relay (user→admin, admin→user)
│   └── settings.py          # NEW: Settings submenu + phone change
├── services/
│   ├── keycrm.py            # Existing KeyCRM client
│   └── shopify.py           # Existing Shopify client
├── keyboards.py             # NEW: All InlineKeyboardBuilder factories
├── callbacks.py             # NEW: All CallbackData class definitions
├── states.py                # Extend with SupportStates, SettingsStates
├── texts.py                 # Extend with new Ukrainian strings
├── config.py                # Existing
├── db.py                    # Existing
├── __main__.py              # Register new routers
└── __init__.py
```

### Pattern 1: CallbackData Factory for Menu Actions
**What:** Define typed callback data classes instead of raw strings.
**When to use:** Every inline button that triggers a bot action (not URL buttons).
**Example:**
```python
# bot/callbacks.py
# Source: https://docs.aiogram.dev/en/latest/dispatcher/filters/callback_data.html
from aiogram.filters.callback_data import CallbackData

class MenuAction(CallbackData, prefix="menu"):
    """Top-level menu navigation."""
    action: str  # "orders", "info", "support", "settings", "back"

class InfoAction(CallbackData, prefix="info"):
    """Info submenu pages."""
    page: str  # "about", "contacts", "payment", "delivery", "back"

class SettingsAction(CallbackData, prefix="sett"):
    """Settings submenu."""
    action: str  # "phone", "language", "back"
```

### Pattern 2: In-Place Menu Navigation via edit_text()
**What:** Use `callback_query.message.edit_text()` to swap menu content in place instead of sending new messages.
**When to use:** All menu transitions (main → submenu → info page → back).
**Example:**
```python
# Source: https://docs.aiogram.dev/en/latest/api/methods/edit_message_text.html
@router.callback_query(MenuAction.filter(F.action == "info"))
async def show_info_menu(callback: CallbackQuery, callback_data: MenuAction, config: AppConfig) -> None:
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ABOUT, callback_data=InfoAction(page="about"))
    builder.button(text=texts.BTN_CONTACTS, callback_data=InfoAction(page="contacts"))
    builder.button(text=texts.BTN_PAYMENT, callback_data=InfoAction(page="payment"))
    builder.button(text=texts.BTN_DELIVERY, callback_data=InfoAction(page="delivery"))
    builder.button(text=texts.BTN_BACK, callback_data=InfoAction(page="back"))
    builder.adjust(2, 2, 1)  # 2 buttons per row, back button alone
    await callback.message.edit_text(
        texts.MSG_INFO_MENU,
        reply_markup=builder.as_markup(),
    )
```

### Pattern 3: Support Relay via forward_message + reply detection
**What:** Forward user message to admin; detect admin's reply-to-forwarded to route response back.
**When to use:** SUPP-01 and SUPP-02.
**Example:**
```python
# Source: https://docs.aiogram.dev/en/latest/api/methods/forward_message.html
# User sends message to bot while in support mode:
@router.message(SupportStates.waiting_message)
async def forward_to_support(message: Message, state: FSMContext, bot: Bot, config: AppConfig) -> None:
    await bot.forward_message(
        chat_id=config.support_chat_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )
    await message.answer(texts.MSG_SUPPORT_FORWARDED)
    await state.clear()

# Admin replies to forwarded message — route back to user:
@router.message(F.chat.id == support_chat_id, F.reply_to_message)
async def admin_reply(message: Message, bot: Bot) -> None:
    # forward_from on the replied-to message contains original user
    original = message.reply_to_message
    if original and original.forward_from:
        await bot.send_message(
            chat_id=original.forward_from.id,
            text=message.text,
        )
```

### Pattern 4: Merged Order Display
**What:** Fetch from both APIs in parallel, tag with source, format as unified list.
**When to use:** ORDR-01 through ORDR-04.
**Example:**
```python
# Parallel fetch with error handling
results = await asyncio.gather(
    keycrm.get_orders_by_phone(phone),
    shopify.get_orders_by_phone(phone) if shopify else asyncio.coroutine(lambda: [])(),
    return_exceptions=True,
)

# Merge and tag
orders = []
for order in (results[0] if not isinstance(results[0], Exception) else []):
    orders.append(format_keycrm_order(order, source="Instagram"))
for order in (results[1] if not isinstance(results[1], Exception) else []):
    orders.append(format_shopify_order(order, source="web"))

# Sort by date descending
orders.sort(key=lambda o: o["date"], reverse=True)
```

### Pattern 5: Show Main Menu After Onboarding
**What:** After phone verification, show inline main menu instead of `ReplyKeyboardRemove`.
**When to use:** Transition from Phase 2 onboarding to Phase 3 menu.
**Example:**
```python
# After save_user() in onboarding flow, AND on /start for returning users:
async def show_main_menu(message_or_callback, config: AppConfig) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ORDERS, callback_data=MenuAction(action="orders"))
    builder.button(text=texts.BTN_INFO, callback_data=MenuAction(action="info"))
    builder.button(text=texts.BTN_SUPPORT, callback_data=MenuAction(action="support"))
    builder.button(text=texts.BTN_WEBSITE, url=config.website_url)
    builder.button(text=texts.BTN_SETTINGS, callback_data=MenuAction(action="settings"))
    builder.adjust(1)  # 1 button per row for clean look
    await message_or_callback.answer(
        texts.MSG_MAIN_MENU,
        reply_markup=builder.as_markup(),
    )
```

### Anti-Patterns to Avoid
- **Sending new messages for menu navigation:** Every menu transition should use `edit_text()` on the existing message, not `answer()`. Sending new messages clutters the chat with stale keyboards.
- **Raw callback_data strings:** Never use `callback_data="orders"` — use `CallbackData` factory classes. Raw strings break silently when you refactor.
- **Forgetting `callback.answer()`:** Telegram shows a loading spinner on the button until `answer()` is called. Always call it first in every callback handler, even with no text.
- **Shared aiosqlite connections:** The project uses per-operation connections (established in Phase 1). Do not introduce a shared connection — it causes "cannot reuse" errors.
- **Blocking API calls:** Always use `asyncio.gather()` for parallel Shopify + KeyCRM lookups. Sequential calls double the response time.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Inline keyboard layout | Manual list-of-lists construction | `InlineKeyboardBuilder` + `.adjust()` | Builder handles row wrapping, attach, copy; adjust takes variadic row sizes |
| Callback data parsing | String splitting / regex on callback_data | `CallbackData` factory + `.filter()` | Type-safe pack/unpack, works with aiogram magic filter, catches mismatches at parse time |
| Phone re-validation | New validation logic in settings | Reuse `PHONE_PATTERN` from `onboarding.py` + same `save_user()` from `db.py` | Avoids divergent validation rules |
| Order date parsing | Custom datetime parsers | `datetime.fromisoformat()` for both APIs | Shopify returns ISO 8601; KeyCRM returns ISO-like `created_at` |
| Message text truncation | Manual string slicing for long order lists | Telegram's 4096 char limit check + pagination or "show more" button | Prevents silent message truncation |

**Key insight:** aiogram 3's `InlineKeyboardBuilder` and `CallbackData` factory handle 90% of the menu infrastructure. The real work is formatting order data and wiring the support relay.

## Common Pitfalls

### Pitfall 1: Telegram Message Length Limit
**What goes wrong:** A user with many orders generates a message exceeding 4096 characters, and `edit_text()` raises `TelegramBadRequest`.
**Why it happens:** Each order takes ~150-200 chars; 25+ orders exceeds the limit.
**How to avoid:** Check message length before sending. If too long, show the most recent N orders with a "Show more" callback button, or paginate.
**Warning signs:** Any formatted order list that does not have a length check.

### Pitfall 2: forward_from Privacy Settings
**What goes wrong:** `message.reply_to_message.forward_from` is `None` when the original sender has "forwarding" privacy enabled in Telegram settings.
**Why it happens:** Telegram respects user privacy — if a user disables link to their account in forwards, `forward_from` is not populated.
**How to avoid:** Instead of relying on `forward_from`, when forwarding user message to admin, also send a metadata message (or add caption) with the user's `chat_id`. Store mapping in FSM data or a small DB table. This way the admin reply handler can look up the user by the forwarded message metadata, not `forward_from`.
**Warning signs:** Support replies silently fail for some users but work for others.

### Pitfall 3: Stale Inline Keyboards
**What goes wrong:** User clicks a button on an old message (from a previous session). The bot tries to `edit_text()` but the message is too old or the callback data references a state that no longer exists.
**Why it happens:** Inline keyboards persist in chat history forever. Users scroll up and click old buttons.
**How to avoid:** Handle `TelegramBadRequest` in callback handlers gracefully — catch the exception and send a new message with a fresh menu instead.
**Warning signs:** Unhandled exceptions in callback query handlers.

### Pitfall 4: Callback Query Answer Timeout
**What goes wrong:** Telegram shows a persistent loading spinner on the clicked button.
**Why it happens:** The handler does async work (API calls) before calling `callback.answer()`.
**How to avoid:** Always call `await callback.answer()` as the FIRST line in every callback handler. Do the async work after.
**Warning signs:** Users see spinning buttons that never resolve.

### Pitfall 5: Router Registration Order
**What goes wrong:** The support admin reply handler never fires, or onboarding handler swallows support messages.
**Why it happens:** aiogram processes routers in registration order. A broad catch-all handler in an earlier router swallows messages meant for later routers.
**How to avoid:** Register routers in this order: common (commands) → menu (callbacks) → orders → info → support → settings → onboarding (FSM catch-all last). The support router must filter on `F.chat.id == support_chat_id` to avoid catching regular user messages.
**Warning signs:** Handlers that should fire are silently skipped.

### Pitfall 6: Shopify Client is None
**What goes wrong:** `shopify.get_orders_by_phone()` raises `AttributeError: 'NoneType' object has no attribute 'get_orders_by_phone'`.
**Why it happens:** Shopify credentials are optional (per Phase 1 decision `dp["shopify"] = None`).
**How to avoid:** Always check `if shopify:` before calling. In `asyncio.gather()`, conditionally include the Shopify coroutine or use an empty-result fallback.
**Warning signs:** Any code that calls `shopify.method()` without a None check.

## Code Examples

### Verified: InlineKeyboardBuilder with CallbackData
```python
# Source: https://docs.aiogram.dev/en/latest/utils/keyboard.html
# Source: https://docs.aiogram.dev/en/latest/dispatcher/filters/callback_data.html
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData

class MenuAction(CallbackData, prefix="menu"):
    action: str

builder = InlineKeyboardBuilder()
builder.button(text="Orders", callback_data=MenuAction(action="orders"))
builder.button(text="Info", callback_data=MenuAction(action="info"))
builder.adjust(1)
markup = builder.as_markup()
```

### Verified: Callback Query Handler with edit_text
```python
# Source: https://docs.aiogram.dev/en/latest/api/methods/edit_message_text.html
from aiogram import Router, F
from aiogram.types import CallbackQuery

router = Router()

@router.callback_query(MenuAction.filter(F.action == "orders"))
async def handle_orders(callback: CallbackQuery, callback_data: MenuAction) -> None:
    await callback.answer()  # MUST be first
    await callback.message.edit_text(
        "Your orders:",
        reply_markup=new_keyboard_markup,
    )
```

### Verified: URL Button (no callback handler needed)
```python
# Source: https://docs.aiogram.dev/en/latest/api/types/inline_keyboard_button.html
builder.button(text="Website", url="https://koreanstory.com.ua")
# Clicking this opens the URL in user's browser — no callback_query fires
```

### Verified: Forward Message to Support
```python
# Source: https://docs.aiogram.dev/en/latest/api/methods/forward_message.html
await bot.forward_message(
    chat_id=config.support_chat_id,
    from_chat_id=message.chat.id,
    message_id=message.message_id,
)
```

### Verified: copy_message (alternative to forward for cleaner relay)
```python
# Source: https://docs.aiogram.dev/en/latest/api/methods/copy_message.html
await bot.copy_message(
    chat_id=config.support_chat_id,
    from_chat_id=message.chat.id,
    message_id=message.message_id,
)
```

### Verified: FSM States for Support and Settings
```python
# Extend existing bot/states.py
from aiogram.fsm.state import State, StatesGroup

class SupportStates(StatesGroup):
    waiting_message = State()

class SettingsStates(StatesGroup):
    waiting_new_phone = State()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Raw `callback_data` strings | `CallbackData` factory classes | aiogram 3.0+ | Type safety, magic filter support |
| `ReplyKeyboardMarkup` for menus | `InlineKeyboardMarkup` for menus | Telegram Bot API standard practice | Inline keyboards don't clutter chat, support edit_text() |
| `aiogram.types.InlineKeyboardMarkup(inline_keyboard=[[...]])` | `InlineKeyboardBuilder().as_markup()` | aiogram 3.0+ | Cleaner API, row adjustment, copy/attach support |
| `dp.callback_query_handler()` (aiogram 2.x) | `@router.callback_query()` (aiogram 3.x) | aiogram 3.0 | Router-based, filters as decorator args |

**Deprecated/outdated:**
- `Dispatcher.callback_query_handler()`: aiogram 2.x pattern. Use `Router.callback_query()` in 3.x.
- `types.InlineKeyboardMarkup(row_width=...)`: aiogram 2.x. Use `InlineKeyboardBuilder.adjust()` in 3.x.
- `CallbackQueryHandler` class: aiogram 2.x. Use `@router.callback_query(Filter)` decorator in 3.x.

## Open Questions

1. **Message length for heavy order histories**
   - What we know: Telegram message limit is 4096 chars. Each order is ~150-200 chars formatted.
   - What's unclear: What's the realistic maximum number of orders per customer for this shop? If it's under ~20, no pagination needed.
   - Recommendation: Implement a simple "show first 10 orders" with a "Show more" button as a safety measure. Low effort, prevents edge-case crashes.

2. **forward_from privacy for support relay**
   - What we know: Telegram allows users to hide `forward_from`. When hidden, the admin cannot see who sent the forwarded message.
   - What's unclear: Whether the shop's actual customers have this privacy setting enabled.
   - Recommendation: Do NOT rely on `forward_from`. Instead, send a bot-generated metadata line before the forwarded message (e.g., "Message from user chat_id: 12345") or store a forwarded_message_id → chat_id mapping in the DB. This guarantees admin can always reply.

3. **Admin reply detection**
   - What we know: The admin replies by using Telegram's reply-to-message on the forwarded message in their chat with the bot.
   - What's unclear: Whether the admin will reliably use reply-to (vs. just typing a message in the bot chat).
   - Recommendation: Use `F.reply_to_message` filter. If admin sends a message without replying, either ignore it or prompt them to reply to a specific forwarded message. Keep it simple for v1.

## Sources

### Primary (HIGH confidence)
- [aiogram 3.25.0 InlineKeyboardBuilder docs](https://docs.aiogram.dev/en/latest/utils/keyboard.html) — keyboard builder API, adjust(), as_markup()
- [aiogram 3.25.0 CallbackData factory docs](https://docs.aiogram.dev/en/latest/dispatcher/filters/callback_data.html) — CallbackData class, prefix, pack/unpack, filter()
- [aiogram 3.25.0 CallbackQuery type](https://docs.aiogram.dev/en/latest/api/types/callback_query.html) — answer(), message field, data field
- [aiogram 3.25.0 InlineKeyboardButton](https://docs.aiogram.dev/en/latest/api/types/inline_keyboard_button.html) — url param, callback_data param, web_app param
- [aiogram 3.25.0 forwardMessage](https://docs.aiogram.dev/en/latest/api/methods/forward_message.html) — chat_id, from_chat_id, message_id
- [aiogram 3.25.0 copyMessage](https://docs.aiogram.dev/en/latest/api/methods/copy_message.html) — alternative to forward without "Forwarded from" header
- [aiogram 3.25.0 editMessageText](https://docs.aiogram.dev/en/latest/api/methods/edit_message_text.html) — in-place menu transitions
- [aiogram 3.25.0 FSM docs](https://docs.aiogram.dev/en/latest/dispatcher/finite_state_machine/index.html) — StatesGroup, State, set_state, clear

### Secondary (MEDIUM confidence)
- [aiogram GitHub Discussion #1169](https://github.com/aiogram/aiogram/discussions/1169) — CallbackQuery usage patterns
- [aiogram GitHub Discussion #1239](https://github.com/aiogram/aiogram/discussions/1239) — Callback button troubleshooting

### Tertiary (LOW confidence)
- Support relay pattern with `forward_from` privacy workaround — based on general Telegram Bot API knowledge; the `forward_from` = None behavior is documented by Telegram but specific workaround patterns are from community practice

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries needed; all patterns use aiogram 3.25.0 which is already installed
- Architecture: HIGH — patterns (CallbackData factory, InlineKeyboardBuilder, edit_text navigation) verified against official aiogram 3.25.0 docs
- Pitfalls: HIGH — forward_from privacy, message length limits, callback answer timing are well-documented Telegram Bot API behaviors
- Support relay: MEDIUM — the specific admin-reply-to-forwarded pattern works but the forward_from privacy edge case needs a workaround (metadata message or DB mapping)

**Research date:** 2026-03-03
**Valid until:** 2026-04-03 (stable — aiogram 3.x API is mature)
