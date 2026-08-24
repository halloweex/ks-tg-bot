"""All Ukrainian UI strings as module-level constants.

No string literals should appear in handler files — import from here instead.
"""
import re
from html import escape
from urllib.parse import quote

# Greeting (warm personal tone)
# Use .format(brand_name=config.brand_name) at runtime
GREETING = (
    "Вітаємо у {brand_name}! 🌸\n\n"
    "Я покажу, де ваше замовлення, що ви вже купували і коли улюблений засіб "
    "знову зʼявиться.\n\n"
    "Щоб знайти ваші замовлення, поділіться, будь ласка, номером телефону — "
    "тим самим, що ви вказували при покупці."
)

BTN_SHARE_PHONE = "📱 Поділитися номером"

# Security: the phone must be proven via the request_contact button (own number
# only). Manual entry / forwarded contacts are rejected to prevent viewing
# someone else's orders and delivery address (IDOR).
ERR_CONTACT_NOT_OWN = (
    "❗ Поділіться, будь ласка, своїм власним номером через кнопку "
    "«📱 Поділитися номером» нижче.\nПереслані або чужі контакти не приймаються."
)
MSG_USE_SHARE_BUTTON = (
    "З міркувань безпеки номер не можна вводити вручну.\n"
    "Скористайтеся кнопкою «📱 Поділитися номером» нижче 👇"
)

# Error messages (neutral/factual tone)
ERR_GENERIC = "Виникла технічна помилка. Спробуйте пізніше."
ERR_API_UNAVAILABLE = "Сервіс тимчасово недоступний. Спробуйте через кілька хвилин."
ERR_PHONE_NOT_FOUND = (
    "На жаль, ми не знайшли замовлень за цим номером.\n"
    "Перевірте номер або зверніться до підтримки."
)
ERR_INVALID_PHONE = "Невірний формат номера. Введіть у міжнародному форматі, наприклад +380XXXXXXXXX."

# Main menu buttons. Short by design: the menu is laid out two and three to a
# row, and a long label there wraps to two lines and makes the grid ragged.
BTN_ORDERS = "📦 Замовлення"
BTN_INFO = "ℹ️ Довідка"
BTN_SUPPORT = "💬 Менеджер"
BTN_WEBSITE = "🌐 Сайт"
BTN_SETTINGS = "⚙️ Налаштування"

# Submenu buttons
BTN_ABOUT = "Про нас"
BTN_CONTACTS = "Контакти"
BTN_PAYMENT = "Оплата"
BTN_DELIVERY = "Доставка"
BTN_BACK = "◀️ Назад"
BTN_MENU = "📋 Меню"
BTN_CHANGE_PHONE = "📱 Змінити номер"
BTN_LANGUAGE = "🌍 Мова"

# Order display
MSG_ORDERS_HEADER = "<b>📦 Ваші замовлення</b>"
MSG_ORDER_SOURCE_WEB = "🌐 Сайт"
MSG_ORDER_SOURCE_INSTAGRAM = "📸 Instagram"


NOVAPOSHTA_TRACKING_URL = "https://novaposhta.ua/tracking/?cargo_number={ttn}"


def tracking_link(ttn: str) -> str:
    """A TTN rendered as a link to the Nova Poshta tracking page.

    Order and delivery messages are sent with parse_mode="HTML", so every
    dynamic value they interpolate must be escaped — product names carry '&'
    (5k+ of them: "Differ & Deeper", "Skin&Lab"), which is what made an earlier
    version give up on markup entirely.
    """
    safe = escape(ttn)
    return f'<a href="{NOVAPOSHTA_TRACKING_URL.format(ttn=quote(ttn, safe=""))}">{safe}</a>'


# KeyCRM product names average 85 characters and reach 147 — brand, description
# and volume are all packed into one string, so the full name is unreadable in a
# list on a phone.
NAME_MAX_LEN = 40


def shorten_name(name: str, limit: int = NAME_MAX_LEN) -> str:
    """Trim a product name to `limit` characters, adding an ellipsis if cut."""
    name = str(name)
    return name if len(name) <= limit else name[:limit].rstrip() + "…"


# The CRM writes a product as "English name - українська назва", in either
# order, and sometimes as one mixed string with no separator at all. Trimming
# such a name from the left throws away the half a Ukrainian customer reads:
# "Dear Doer The Hidden Body Scrub - Dear Doer the Hidd…"
_NAME_SPLIT = re.compile(r"\s+[-–—]\s+")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
# The brand, as far as the first word: the leading latin token, with a
# "(Miniature)" marker in front of it skipped.
_BRAND = re.compile(r"^(?:\(Miniature\)\s*)?([A-Za-z][\w:.'’]*)")


def product_label(name: str, limit: int = NAME_MAX_LEN) -> str:
    """A product name as a customer should read it: their half, brand kept.

    Picks the half of the name written in Cyrillic — which is the one the
    customer reads — and puts the brand back in front if that half dropped it,
    since "Гелева маска з колагеном" without "Abib" names no product anyone can
    ask for. Names with no separator, or with no Cyrillic at all, are left alone
    and only trimmed: "CURE SPF Cooling Sunstick" is already what it is.
    """
    name = str(name).strip()
    parts = [part.strip() for part in _NAME_SPLIT.split(name) if part.strip()]
    if len(parts) > 1:
        cyrillic = [part for part in parts if _CYRILLIC.search(part)]
        chosen = cyrillic[0] if cyrillic else parts[0]
    else:
        chosen = name

    brand = _BRAND.match(name)
    if brand and not chosen.lower().startswith(brand.group(1).lower()):
        chosen = f"{brand.group(1)} {chosen}"

    chosen = re.sub(r"\s+", " ", chosen).strip(" ,")
    return shorten_name(chosen, limit)


def order_source_label(row: dict) -> str:
    """Label a cached order by where it was placed.

    A store order number means it came from the website — whether the row was
    fetched from Shopify directly or from KeyCRM, which mirrors the number for
    orders its Shopify integration pulled in. Shared by the order list and the
    delivery view so one order never carries two different labels.
    """
    order_name = row.get("order_name", "")
    if order_name:
        return f"{MSG_ORDER_SOURCE_WEB} {order_name}".strip()
    return MSG_ORDER_SOURCE_INSTAGRAM
# Field labels inside an order block — these were inline literals in the handler,
# which meant they stayed Ukrainian in every language.
LBL_STATUS = "Статус"
LBL_PRODUCTS = "Товари"
LBL_TOTAL = "Сума"
LBL_DATE = "Дата"
# Replaced the old "...та інші замовлення", which said there were more and gave
# no way to reach them.
MSG_ORDERS_PAGE = "Показано {first}–{last} з {total}"
BTN_ORDERS_NEWER = "◀️ Новіші"
BTN_ORDERS_OLDER = "Старіші ▶️"
MSG_ORDERS_TRUNCATED = "...та інші замовлення"
MSG_ORDER_MORE_ITEMS = "…та інші ({count})"
# The button carries the order's number in the list above it, not its name:
# most orders come from Instagram and have no number of their own, so labels
# built from the source and the date read almost identically and are too long
# to sit side by side.
BTN_SHOW_ITEMS = "🔎 {order}"
BTN_HIDE_ITEMS = "🔼 {order}"
MSG_ORDERS_EXPAND_HINT = "🔎 номер — усі товари цього замовлення"
# Shown only when the sync has not confirmed the data for over an hour — see
# bot/sync.py. "год" needs no plural form, which is the reason it is written
# that way: 1, 2 and 5 hours all read correctly.
MSG_ORDERS_STALE = "⏳ Дані оновлювалися понад {hours} год тому"
MSG_ORDER_TRACKING = "🚚 ТТН: {code}"
MSG_ORDER_LOCATION = "📍 {location}"

# Status messages
MSG_NO_ORDERS = (
    "У вас поки немає замовлень за номером, яким ви поділилися.\n\n"
    "Якщо ви робили замовлення, можливо, воно оформлене на інший номер — "
    "той, що ви вказали під час покупки, а не той, до якого прив'язаний Telegram. "
    "Напишіть нам, і ми знайдемо його вручну."
)
MSG_SUPPORT_FORWARDED = "Ваше повідомлення надіслано менеджеру. Очікуйте відповідь."

# Menu headers
MSG_MAIN_MENU = "Чим допомогти?"
# Sits inside the input field while the menu keyboard is open, in place of
# "Write a message" — the field is not what we want tapped.
MSG_MENU_PLACEHOLDER = "Оберіть дію"
MSG_WEBSITE_INTRO = "Наш магазин — тут 👇"
MSG_INFO_MENU = "Оберіть розділ:"
MSG_SETTINGS_MENU = "Налаштування:"

# Menu content
MSG_LANGUAGE_CURRENT = "Поточна мова: Українська \u2705"
MSG_SUPPORT_PROMPT = "Напиши свій запит — ми підключимо менеджера 💬"
MSG_NEW_PHONE_PROMPT = "Щоб оновити номер, поділіться ним через кнопку нижче 👇"
MSG_PHONE_CHANGED = "Номер телефону змінено! \u2705"

# Onboarding responses
MSG_WELCOME_BACK = "З поверненням! \U0001f338"
MSG_WELCOME_BACK_NAME = "{name}, раді бачити вас знову! \U0001f338"
MSG_PHONE_VERIFIED = "Дякуємо! Номер підтверджено \u2705"

# Support relay (admin-side)
MSG_SUPPORT_REPLY_PREFIX = "Відповідь від менеджера:"
MSG_SUPPORT_ADMIN_NOTE = "📩 Повідомлення від користувача (chat_id: {chat_id}):"
MSG_SUPPORT_REPLY_INSTRUCTION = "↩️ Відповідайте на переслане повідомлення, щоб відповісти клієнту."
MSG_SUPPORT_NO_REPLY_TARGET = "Будь ласка, відповідайте на переслане повідомлення клієнта."

# Broadcast opt-out
MSG_OPT_OUT_CONFIRM = "Ви відписались від розсилки. Щоб підписатись знову, надішліть /start"
MSG_OPT_IN_CONFIRM = "Ви знову підписані на розсилку!"

# Admin broadcast
MSG_BROADCAST_PROMPT = "Введіть текст розсилки:"
MSG_BROADCAST_CONFIRM = "Надіслати це повідомлення {count} користувачам?"
BTN_BROADCAST_YES = "✅ Так"
BTN_BROADCAST_NO = "❌ Ні"
MSG_BROADCAST_CANCELLED = "Розсилку скасовано."
MSG_BROADCAST_STARTED = "Розсилка розпочата..."
MSG_BROADCAST_COMPLETE = "Розсилку завершено!\n\nНадіслано: {sent}\nПомилок: {failed}\nЗаблоковано: {blocked}"
MSG_BROADCAST_NO_RECIPIENTS = "Немає активних підписників для розсилки."

# The newest order is marked with a star on its own heading line rather than
# announced above it — the list is numbered, so one glyph says what a whole
# line used to.
MSG_ORDER_LATEST_MARK = " ⭐"

# Favourites
BTN_FAVOURITES = "⭐ Улюблені"
# Two lines, and the second one is an instruction rather than a description.
# The first line of this screen is not this header at all — it is the customer's
# own "⭐ Улюблені", echoed into the chat by the menu key they pressed. A header
# that says the same thing again in other words is the second of three lines
# before anything actionable appears.
MSG_FAVOURITES_HEADER = (
    "<b>⭐ Ваші постійні засоби</b>\n"
    "Те, що ви замовляєте найчастіше. Натисніть, щоб замовити ще раз 👇"
)
# When nothing has been bought twice, calling it a favourite is a small lie:
# a quarter of customers have only ever ordered one product.
MSG_FAVOURITES_HEADER_ONCE = (
    "<b>🛍 Ви це вже купували</b>\n"
    "Натисніть, щоб замовити ще раз 👇"
)
# Counts rather than "N разів" to sidestep Ukrainian plural agreement.
MSG_FAVOURITE_LINE = "замовлень: {orders} · {qty} шт · востаннє {date}"
MSG_NO_FAVOURITES = (
    "Поки що нема з чого скласти улюблене — тут з'являться товари, "
    "які ви замовляєте найчастіше."
)
# Numbered like the notify buttons above and for the same reason: a row of
# glyph-and-number buttons fits, a row of 40-character product names does not.
# One button per product, carrying the product's own name. The numbered
# variants these replaced needed a legend under the list explaining what the
# numbers meant — and a screen that has to explain its own buttons has already
# lost the argument.
BTN_BUY_PRODUCT = "🛒 {name} · {price} ₴"
# The verb first, and the verb is the bot's promise rather than the product's
# state: "немає" told the customer something they could already see and offered
# them nothing. Every shop that does this well — Rozetka, MAKEUP, Zara — puts
# the undertaking on the button and the availability in the text.
#
# One word for it, though. "Повідомити, коли зʼявиться" is the fuller promise
# and it made this button half again as wide as the buy buttons above it, which
# is a worse thing to be than slightly less explicit. The pop-up that follows
# the tap says the whole sentence.
BTN_NOTIFY_PRODUCT = "🔔 Повідомити: {name}"
BTN_NOTIFY_WAITING = "✅ Чекаєте: {name}"
BTN_BUY_ALL = "🛒 Усе разом · {total} ₴"
# Products the shop can say nothing about — no offer, no stock figure — get no
# button, because there is nothing to press. Naming them here keeps them from
# vanishing off a screen that is supposed to list what someone buys.
MSG_FAVOURITES_ALSO = "Також ви купували: {names}"
BTN_WANT_DISCOUNT = "💰 Хочу знижку на ці товари"
MSG_DISCOUNT_SENT = (
    "Передали менеджеру ваш запит на знижку. Ми звʼяжемось із вами тут, у боті."
)
MSG_DISCOUNT_ALREADY = "Ваш запит уже в роботі — менеджер відповість найближчим часом."
# Goes to the support chat, so Ukrainian regardless of the customer's language.
MSG_DISCOUNT_ADMIN = "💰 Запит на знижку (chat_id: {chat_id}):"
MSG_BACK_IN_STOCK_HEADER = "🔔 Знову в наявності:"
# Numbered like the order buttons, for the same reason: a 40-character product
# name does not fit on a button next to another one.
BTN_NOTIFY_ME = "🔔 {product}"
BTN_NOTIFY_CANCEL = "🔕 {product}"
MSG_STOCK_HINT = "🔔 номер — повідомимо, щойно зʼявиться"
MSG_SUBSCRIBED = "Повідомимо, щойно товар знову з'явиться."
MSG_UNSUBSCRIBED = "Більше не сповіщатимемо про цей товар."
MSG_FAVOURITE_OUT_OF_STOCK = "зараз немає"
BTN_DELIVERY_STATUS = "🚚 Відслідкувати замовлення"
MSG_DELIVERY_HEADER = "<b>🚚 Ваші відправлення</b>"
MSG_NO_DELIVERIES = "Наразі немає відправлень для відстеження."
MSG_DELIVERY_STATUS = "Статус: {status}"
MSG_DELIVERY_SCHEDULED = "📅 Очікувана дата: {date}"
MSG_DELIVERY_ACTUAL = "✅ Отримано: {date}"
MSG_DELIVERY_WAREHOUSE = "🏤 {warehouse}"
MSG_DELIVERY_NO_TRACKING = "⏳ ТТН ще не присвоєно"
MSG_DELIVERIES_TRUNCATED = "...та інші відправлення"
