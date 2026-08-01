"""Per-user language resolution and the English string table.

`texts.py` stays the Ukrainian source of truth — every key is defined there.
This module holds translations keyed by the same names and a `Texts` view that
handlers receive as `t`, so `texts.MSG_ORDERS_HEADER` becomes `t.MSG_ORDERS_HEADER`
and resolves per user.

A key missing from a translation falls back to Ukrainian rather than raising:
a half-translated string table degrades to the default language instead of
breaking the handler that uses it.
"""
from __future__ import annotations

from bot import texts

DEFAULT_LANG = "uk"

# Who reads what decides the language:
#   * customers  -> their Telegram language, Ukrainian when it is not clear;
#   * the support chat -> Ukrainian, because support answers in Ukrainian and a
#     group chat has no single language of its own;
#   * admins -> English by default, since their surface sits next to the logs,
#     the runbook and the backup alerts.
# Logs, alerts and anything else purely operational are English regardless and
# do not go through these tables at all.
ADMIN_DEFAULT_LANG = "en"
OPERATOR_LANG = "uk"

# Adding a language is one entry here plus one table below — nothing else in the
# codebase enumerates languages.
LANGUAGE_NAMES = {
    "uk": "Українська",
    "en": "English",
}
SUPPORTED = tuple(LANGUAGE_NAMES)

EN: dict[str, str] = {
    "GREETING": (
        "Welcome to {brand_name}! 🌸\n\n"
        "I'll show you where your order is, what you've bought before, and when "
        "a favourite is back in stock.\n\n"
        "To find your orders, please share your phone number — the same one you "
        "used when ordering."
    ),
    "BTN_SHARE_PHONE": "📱 Share my number",
    "ERR_CONTACT_NOT_OWN": (
        "❗ Please share your own number using the «📱 Share my number» button "
        "below.\nForwarded or third-party contacts are not accepted."
    ),
    "MSG_USE_SHARE_BUTTON": (
        "For security reasons the number cannot be typed in.\n"
        "Please use the «📱 Share my number» button below 👇"
    ),
    "ERR_GENERIC": "Something went wrong. Please try again later.",
    "ERR_API_UNAVAILABLE": "The service is temporarily unavailable. Please try again in a few minutes.",
    "ERR_PHONE_NOT_FOUND": (
        "We couldn't find any orders for this number.\n"
        "Please check the number or contact support."
    ),
    "ERR_INVALID_PHONE": "Invalid number format. Please use the international format, e.g. +380XXXXXXXXX.",
    "BTN_ORDERS": "📦 Orders",
    "BTN_INFO": "ℹ️ Help",
    "BTN_SUPPORT": "💬 Manager",
    "BTN_WEBSITE": "🌐 Shop",
    "BTN_SETTINGS": "⚙️ Settings",
    "BTN_ABOUT": "About us",
    "BTN_CONTACTS": "Contacts",
    "BTN_PAYMENT": "Payment",
    "BTN_DELIVERY": "Delivery",
    "BTN_BACK": "◀️ Back",
    "BTN_MENU": "📋 Menu",
    "BTN_CHANGE_PHONE": "📱 Change number",
    "BTN_LANGUAGE": "🌍 Language",
    "MSG_ORDERS_HEADER": "<b>📦 Your orders</b>",
    "MSG_ORDER_SOURCE_WEB": "🌐 Website",
    "MSG_ORDER_SOURCE_INSTAGRAM": "📸 Instagram",
    "LBL_STATUS": "Status",
    "LBL_PRODUCTS": "Items",
    "LBL_TOTAL": "Total",
    "LBL_DATE": "Date",
    "MSG_ORDERS_PAGE": "Showing {first}–{last} of {total}",
    "BTN_ORDERS_NEWER": "◀️ Newer",
    "BTN_ORDERS_OLDER": "Older ▶️",
    "MSG_ORDERS_TRUNCATED": "...and more orders",
    "MSG_ORDER_MORE_ITEMS": "…and {count} more",
    "BTN_SHOW_ITEMS": "🔎 {order}",
    "BTN_HIDE_ITEMS": "🔼 {order}",
    "MSG_ORDERS_EXPAND_HINT": "🔎 number — every item in that order",
    "MSG_ORDER_TRACKING": "🚚 Tracking: {code}",
    "MSG_ORDER_LOCATION": "📍 {location}",
    "MSG_NO_ORDERS": (
        "We couldn't find any orders for the number you shared.\n\n"
        "If you have ordered from us, it may be under a different number — the "
        "one you gave at checkout rather than the one your Telegram uses. "
        "Message us and we'll find it manually."
    ),
    "MSG_SUPPORT_FORWARDED": "Your message has been sent to a manager. Please wait for a reply.",
    "MSG_MAIN_MENU": "How can I help?",
    "MSG_MENU_RETIRED": "The menu now lives in the ☰ button next to the input field 👇",
    "MSG_INFO_MENU": "Choose a section:",
    "MSG_SETTINGS_MENU": "Settings:",
    "MSG_LANGUAGE_CURRENT": "Current language: English ✅",
    "MSG_SUPPORT_PROMPT": "Write your message and we'll pass it on to a manager:",
    "MSG_NEW_PHONE_PROMPT": "To update your number, share it using the button below 👇",
    "MSG_PHONE_CHANGED": "Phone number updated! ✅",
    "MSG_WELCOME_BACK": "Welcome back! 🌸",
    "MSG_WELCOME_BACK_NAME": "{name}, lovely to see you again! 🌸",
    "MSG_PHONE_VERIFIED": "Thank you! Number verified ✅",
    "MSG_SUPPORT_REPLY_PREFIX": "Reply from a manager:",
    "MSG_SUPPORT_ADMIN_NOTE": "📩 Message from a user (chat_id: {chat_id}):",
    "MSG_SUPPORT_REPLY_INSTRUCTION": "↩️ Reply to the forwarded message to answer the customer.",
    "MSG_SUPPORT_NO_REPLY_TARGET": "Please reply to the customer's forwarded message.",
    "MSG_OPT_OUT_CONFIRM": "You have unsubscribed from our updates. Send /start to subscribe again.",
    "MSG_OPT_IN_CONFIRM": "You are subscribed to our updates again!",
    "MSG_BROADCAST_PROMPT": "Enter the broadcast text:",
    "MSG_BROADCAST_CONFIRM": "Send this message to {count} users?",
    "BTN_BROADCAST_YES": "✅ Yes",
    "BTN_BROADCAST_NO": "❌ No",
    "MSG_BROADCAST_CANCELLED": "Broadcast cancelled.",
    "MSG_BROADCAST_STARTED": "Broadcast started...",
    "MSG_BROADCAST_COMPLETE": "Broadcast finished!\n\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
    "MSG_BROADCAST_NO_RECIPIENTS": "There are no active subscribers to send to.",
    "MSG_ORDER_LATEST_MARK": "⭐ ",
    "BTN_FAVOURITES": "⭐ Favourites",
    "MSG_FAVOURITES_HEADER": "<b>⭐ The ones you love most</b>",
    "MSG_FAVOURITES_HEADER_ONCE": "🛍 Products you have ordered:",
    "MSG_FAVOURITE_LINE": "orders: {orders} · {qty} pcs · last {date}",
    "MSG_NO_FAVOURITES": (
        "Nothing to base favourites on yet — the products you order most often "
        "will show up here."
    ),
    "BTN_WANT_DISCOUNT": "💰 I'd like a discount on these",
    "MSG_DISCOUNT_SENT": (
        "We've passed your discount request to a manager. We'll get back to you "
        "here in the bot."
    ),
    "MSG_DISCOUNT_ALREADY": "Your request is already with a manager — they will reply shortly.",
    "MSG_DISCOUNT_ADMIN": "💰 Discount request (chat_id: {chat_id}):",
    "MSG_BACK_IN_STOCK_HEADER": "🔔 Back in stock:",
    "BTN_NOTIFY_ME": "🔔 {product}",
    "BTN_NOTIFY_CANCEL": "🔕 {product}",
    "MSG_STOCK_HINT": "🔔 number — we'll write when it is back",
    "MSG_SUBSCRIBED": "We'll message you as soon as it is back.",
    "MSG_UNSUBSCRIBED": "We will not notify you about this product.",
    "MSG_FAVOURITE_OUT_OF_STOCK": "out of stock",
    "BTN_DELIVERY_STATUS": "🚚 Delivery",
    "MSG_DELIVERY_HEADER": "<b>🚚 Your parcels</b>",
    "MSG_NO_DELIVERIES": "There are no shipments to track right now.",
    "MSG_DELIVERY_STATUS": "Status: {status}",
    "MSG_DELIVERY_SCHEDULED": "📅 Estimated date: {date}",
    "MSG_DELIVERY_ACTUAL": "✅ Received: {date}",
    "MSG_DELIVERY_WAREHOUSE": "🏤 {warehouse}",
    "MSG_DELIVERY_NO_TRACKING": "⏳ No tracking number assigned yet",
    "MSG_DELIVERIES_TRUNCATED": "...and more shipments",
    # Language switching — no Ukrainian counterpart needed in texts.py, both
    # sides live here because the offer is shown in both languages at once.
    "MSG_LANGUAGE_OFFER": (
        "Your Telegram is set to {language}. Would you like to continue in "
        "{language}, or stay in Ukrainian?"
    ),
    "MSG_LANGUAGE_SET": "Language set to English ✅",
    "MSG_LANGUAGE_CHOOSE": "Choose a language:",
}

UK_EXTRA: dict[str, str] = {
    "MSG_LANGUAGE_OFFER": (
        "У вашому Telegram обрано {language}. Продовжити цією мовою "
        "чи залишити українську?"
    ),
    "MSG_LANGUAGE_SET": "Мову змінено на українську ✅",
    "MSG_LANGUAGE_CHOOSE": "Оберіть мову:",
}

# CRM status values as the customer should read them. Keys are the raw KeyCRM
# values (order status_name and shipping_status), lowercased. Anything not listed
# is shown as-is rather than hidden — an unknown status is still information.
STATUS_NAMES: dict[str, dict[str, str]] = {
    "uk": {
        "new": "Прийнято",
        "completed": "Виконано",
        "canceled": "Скасовано",
        "not_available": "Немає в наявності",
        "delivered": "Доставлено",
        "delivered_to_delivery": "Передано перевізнику",
        "in_transit": "В дорозі",
        "departing": "Відправлено",
        "pickup": "Очікує у відділенні",
        "return": "Повертається",
    },
    "en": {
        "new": "Received",
        "completed": "Completed",
        "canceled": "Cancelled",
        "not_available": "Out of stock",
        "delivered": "Delivered",
        "delivered_to_delivery": "Handed to carrier",
        "in_transit": "In transit",
        "departing": "Shipped",
        "pickup": "Ready for pickup",
        "return": "Being returned",
        # Statuses named in Ukrainian in the CRM. They occur in real orders, so
        # without these the English UI would show Ukrainian status text.
        "прибув у відділення": "Arrived at branch",
        "повертається": "Being returned",
        "повернено": "Returned",
        "помилка доставки": "Delivery failed",
        "зібрано для самовивозу": "Ready for self-pickup",
    },
}

# Currency as each language writes it. KeyCRM rows store "грн" and Shopify rows
# store "UAH" for the same money, so mapping both directions also stops a
# Shopify-only order from showing "UAH" to a Ukrainian reader.
CURRENCY_NAMES: dict[str, dict[str, str]] = {
    "uk": {"uah": "грн", "грн": "грн"},
    "en": {"uah": "UAH", "грн": "UAH"},
}

_TABLES: dict[str, dict[str, str]] = {"uk": UK_EXTRA, "en": EN}


def normalize(code: str | None) -> str:
    """Map a Telegram language_code to a language we actually support.

    Telegram sends things like 'en', 'en-GB', 'uk', or nothing at all. Anything
    we don't have strings for falls back to Ukrainian — the shop's language.
    """
    if not code:
        return DEFAULT_LANG
    base = code.split("-")[0].lower()
    return base if base in SUPPORTED else DEFAULT_LANG


class Texts:
    """A language-bound view over the string tables.

    Attribute access mirrors the `texts` module, so handlers read the same
    names they always did.
    """

    __slots__ = ("lang",)

    def __init__(self, lang: str = DEFAULT_LANG) -> None:
        self.lang = lang if lang in SUPPORTED else DEFAULT_LANG

    def __getattr__(self, name: str) -> str:
        table = _TABLES.get(self.lang, {})
        if name in table:
            return table[name]
        return getattr(texts, name)

    def status(self, raw: str) -> str:
        """A CRM status value as the customer should read it.

        Falls through to the raw value for anything untranslated: showing
        'presence_confirmed' is worse than a proper label but better than hiding
        the state of someone's order.
        """
        if not raw:
            return ""
        return STATUS_NAMES.get(self.lang, {}).get(raw.strip().lower(), raw)

    def currency(self, raw: str) -> str:
        """Currency label for this language, or the raw code if unmapped."""
        if not raw:
            return ""
        return CURRENCY_NAMES.get(self.lang, {}).get(raw.strip().lower(), raw)

    def order_source_label(self, row: dict) -> str:
        """Language-aware version of the shared order label."""
        order_name = row.get("order_name", "")
        if order_name:
            return f"{self.MSG_ORDER_SOURCE_WEB} {order_name}".strip()
        return self.MSG_ORDER_SOURCE_INSTAGRAM


def admin_texts(stored: str | None) -> Texts:
    """Strings for an admin-only surface.

    English unless the admin has explicitly chosen a language — their Telegram
    language is deliberately not consulted, so switching the bot to Ukrainian to
    preview the customer view does not also switch their own tooling.
    """
    return Texts(stored or ADMIN_DEFAULT_LANG)


def operator_texts() -> Texts:
    """Strings addressed to the support chat."""
    return Texts(OPERATOR_LANG)


def customer_texts(stored: str | None) -> Texts:
    """Strings addressed to a customer we are messaging out of the blue.

    Used where there is no incoming update from them to resolve a language from,
    so only a stored choice is available; Ukrainian when there is none.
    """
    return Texts(stored or DEFAULT_LANG)


def variants(key: str) -> set[str]:
    """Every language's value for a key.

    Router filters that match on button text are built at import time, so they
    must accept the button in any language the bot can render it in.
    """
    return {getattr(Texts(lang), key) for lang in SUPPORTED}
