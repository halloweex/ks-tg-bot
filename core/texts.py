"""All Ukrainian UI strings as module-level constants.

No string literals should appear in handler files — import from here instead.

**The voice is the brand's, and it is written on «ти».** From the 2026 strategy
(Tone of voice, p. 199): "Ми звертаємось до людини як до рівної, на «ти»", and
every example dialogue there is on «ти». The brand character is a friend who
knows K-beauty — expert but never above you — so these strings say "Натисни",
not "Натисніть", and "твої замовлення", not "Ваші".

Three don'ts from the same section, worth keeping in view when adding a string:
no talking down, no diminutives, and no excessive emoji. One per screen is a
signpost; three is decoration.

The operator surface below — the support chat, the admin alerts — is *not*
written in this voice. It is read by whoever is working, next to the logs.
"""
import re
from datetime import datetime
from html import escape
from urllib.parse import quote

# Greeting (warm personal tone)
# Use .format(brand_name=config.brand_name) at runtime
GREETING = (
    "Вітаємо у {brand_name}! 🌸\n\n"
    "Покажу, де твоє замовлення, що ти вже купувала і коли улюблений засіб "
    "знову зʼявиться.\n\n"
    "Щоб знайти твої замовлення, поділись, будь ласка, номером телефону — "
    "тим самим, що вказувала при покупці."
)

BTN_SHARE_PHONE = "📱 Поділитися номером"

# Security: the phone must be proven via the request_contact button (own number
# only). Manual entry / forwarded contacts are rejected to prevent viewing
# someone else's orders and delivery address (IDOR).
ERR_CONTACT_NOT_OWN = (
    "❗ Поділись, будь ласка, своїм власним номером через кнопку "
    "«📱 Поділитися номером» нижче.\nПереслані або чужі контакти не приймаються."
)
MSG_USE_SHARE_BUTTON = (
    "З міркувань безпеки номер не можна вводити вручну.\n"
    "Скористайся кнопкою «📱 Поділитися номером» нижче 👇"
)

# Error messages (neutral/factual tone)
ERR_GENERIC = "Виникла технічна помилка. Спробуй, будь ласка, трохи згодом."
ERR_API_UNAVAILABLE = "Сервіс тимчасово недоступний. Спробуй через кілька хвилин."
ERR_PHONE_NOT_FOUND = (
    "На жаль, ми не знайшли замовлень за цим номером.\n"
    "Перевір номер або напиши менеджеру — розберемось разом."
)
ERR_INVALID_PHONE = (
    "Не вдалося розібрати номер із твого профілю Telegram.\n"
    "Натисни «💬 Менеджер» — підключимо номер вручну."
)

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
# No emoji in the label: the button carries Instagram's own logo as its icon.
BTN_INSTAGRAM = "Instagram"
BTN_MENU = "📋 Меню"
BTN_CHANGE_PHONE = "📱 Змінити номер"
BTN_LANGUAGE = "🌍 Мова"

# Order display
MSG_ORDERS_HEADER = "<b>📦 Твої замовлення</b>"
MSG_ORDER_SOURCE_WEB = "🌐 Сайт"
MSG_ORDER_SOURCE_INSTAGRAM = "📸 Instagram"


NOVAPOSHTA_TRACKING_URL = "https://novaposhta.ua/tracking/?cargo_number={ttn}"


# ---------------------------------------------------------------------------
# Custom emoji: the brand marks a message can carry instead of a picture.
#
# A <tg-emoji> renders a real logo inline in the text — the red Nova Poshta
# square where "🚚" used to be — and costs nothing to send: it is an entity, not
# an attachment. Two things decide how they are used here.
#
# **They are a privilege, not a feature.** Per the Bot API: "Custom emoji
# entities can only be used by bots that purchased additional usernames on
# Fragment or in the messages directly sent by the bot to private, group and
# supergroup chats if the owner of the bot has a Telegram Premium subscription."
# This bot travels the second road, so the permission is somebody's paid
# subscription and can lapse without anybody here doing anything. Nothing may
# depend on one working: `strip_custom_emoji` is what the outgoing middleware
# falls back to (bot/middlewares.py), and every tag carries the plain emoji it
# replaces.
#
# **"Directly sent by the bot" excludes inline mode.** A message the customer
# sends by picking a result belongs to them, not to the bot, so no custom emoji
# belongs in a string that reaches InputTextMessageContent — nor in an inline
# result's title or description, which are plain text and would show the tag
# itself. That rules out everything format_cached_order touches.
#
# The ids come from the packs published by t.me/karelin_icons (UIcons), read
# back through getStickerSet. They are Telegram's own identifiers for the
# stickers and are stable as long as the pack is.

# 🚚 Nova Poshta — the only carrier this shop ships with. From the UIcons pack.
NOVA_POSHTA = "5266999677340890591"
# 📸 Instagram, where most of these orders are actually placed.
INSTAGRAM = "5454400924510334242"
# 💳 The ways to pay, from UIcons_Fin. Only the ones the payment page actually
# names: neither pack has an Apple Pay or a Google Pay mark, and inventing one
# out of a similar logo would put the wrong brand next to a payment method.
# Privat24 is here unused on purpose — the day the page mentions PrivatBank it
# is one tag away, and it is the icon the owner asked for by name.
VISA = "5436170489145871132"
MASTERCARD = "5436051454127262267"
MONOBANK = "5436006709157974440"
PRIVAT24 = "5436010252505992225"

_CUSTOM_EMOJI = re.compile(r'<tg-emoji\s+emoji-id="\d+">(.*?)</tg-emoji>', re.S)


def custom_emoji(emoji_id: str, fallback: str) -> str:
    """A custom emoji, carrying the ordinary one it stands in for.

    The fallback is not decoration: Telegram shows it wherever the custom emoji
    cannot be drawn — system notifications, message previews, a non-premium
    reader forwarding the message — and it is what stays if the tags are
    stripped.
    """
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def with_logo(text: str, emoji_id: str, plain: str) -> str:
    """The same line with its first `plain` emoji wearing a real logo.

    Written this way round because the plain emoji is what a custom one carries
    inside it anyway: the line is composed once, in one string table, and the
    surfaces that may use a logo wrap it afterwards. The ones that may not — an
    inline result's title, the card a customer sends — leave it alone.
    """
    return text.replace(plain, custom_emoji(emoji_id, plain), 1)


def strip_custom_emoji(text: str) -> str:
    """The same text with every custom emoji reduced to its plain twin.

    What a message becomes when Telegram refuses the entities: still a
    sentence, still with an emoji where the logo was, just not the logo.
    """
    return _CUSTOM_EMOJI.sub(r"\1", text)


def customer_ref(chat_id: int, *, name: str = "", username: str = "",
                 phone: str = "") -> str:
    """Who a customer is, in one line a manager can act on.

    The name is a link to the person's Telegram profile — `tg://user?id=` is
    what makes a name in a bot's message tappable — and the parts that follow
    are the two ways to reach or find them: the @username to write to, and the
    number to look up in the CRM. Whatever is unknown is left out rather than
    printed empty.

    The chat id stays, last and quiet: it is what the support thread is keyed
    on, and the one identifier that is still there when a customer has no name,
    no username and no number of ours.

    Sent with parse_mode="HTML", hence the escaping — a name really can carry
    '&' or '<'.
    """
    label = escape(name.strip()) if name.strip() else MSG_CUSTOMER_UNKNOWN
    parts = [f'<a href="tg://user?id={chat_id}">{label}</a>']
    if username:
        parts.append(f"@{escape(username.lstrip('@'))}")
    if phone:
        parts.append(escape(phone))
    parts.append(f"<code>{chat_id}</code>")
    return " · ".join(parts)


def tracking_url(ttn: str) -> str:
    """Where a parcel with this TTN can be looked up.

    Its own function because a url button needs the address and nothing else,
    while the line in a message needs it wrapped in an anchor.
    """
    return NOVAPOSHTA_TRACKING_URL.format(ttn=quote(ttn, safe=""))


def tracking_link(ttn: str) -> str:
    """A TTN rendered as a link to the Nova Poshta tracking page.

    Order and delivery messages are sent with parse_mode="HTML", so every
    dynamic value they interpolate must be escaped — product names carry '&'
    (5k+ of them: "Differ & Deeper", "Skin&Lab"), which is what made an earlier
    version give up on markup entirely.
    """
    return f'<a href="{tracking_url(ttn)}">{escape(ttn)}</a>'


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


def short_date(raw: str) -> str:
    """dd.mm.yyyy from a stored timestamp, or the raw value if unparseable.

    Cached timestamps come from two systems and one of them has been known to
    send a date the other's parser refuses; showing what we were given beats
    showing nothing where the alternative is a line that says "востаннє".
    """
    try:
        return datetime.fromisoformat(raw).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return raw or ""


def price_label(value) -> str:
    """A price as a button or a list row shows it: whole hryvnia, thousands
    spaced out.

    3480.36 reads as 3 480 — the kopecks are noise next to a product name, and
    an unbroken 3480 is a number the eye has to count digits in. Anything
    unparseable comes back as 0 rather than raising: a missing price must not
    take the button it sits on off the screen.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"{int(round(number)):,}".replace(",", "\u2009")


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
# The digest. One order is a card, the rest are a line each — a date, a sum and
# how much was in it, which is what a person scans a history for. The layout
# was chosen on real data after the owner called the old screen unreadable:
# five orders were 42 lines, twenty of them the words "Статус:/Товари:/Сума:".
MSG_ORDER_DIGEST_LINE = "{glyph} {date} · {total} {currency} · {items}"
# Cancelled orders are not history a customer is looking for; they are noise
# with a date on it. Counted, folded away, and one tap from being seen.
BTN_CANCELLED_SHOW = "❌ Скасовані ({count})"
BTN_CANCELLED_HIDE = "🙈 Сховати скасовані"
MSG_CANCELLED_HEADER = "❌ Скасовані:"
# On the card, when the parcel has a number: the live answer from Nova Poshta,
# which the shop's own record does not have.
# No emoji in the label: the button carries Nova Poshta's own mark as its icon
# (icon_custom_emoji_id), and a lorry beside the logo is one picture too many.
BTN_WHERE_PARCEL = "Де посилка?"
BTN_ORDERS_NEWER = "◀️ Новіші"
BTN_ORDERS_OLDER = "Старіші ▶️"
MSG_ORDERS_TRUNCATED = "...та інші замовлення"
MSG_ORDER_MORE_ITEMS = "…та інші ({count})"
# The button carries the order's number in the list above it, not its name:
# most orders come from Instagram and have no number of their own, so labels
# built from the source and the date read almost identically and are too long
# to sit side by side.
# The card's item list, unfolded and folded again. Labelled with the count
# rather than with a number in a list: nothing on the digest is numbered any
# more, and a button that says "🔎 3" needs a legend to explain what 3 is.
BTN_SHOW_ITEMS = "🔎 Усі товари ({count})"
BTN_HIDE_ITEMS = "🔼 Згорнути товари"
MSG_ORDERS_EXPAND_HINT = "🔎 номер — усі товари цього замовлення"
# Shown only when the sync has not confirmed the data for over an hour — see
# bot/sync.py. "год" needs no plural form, which is the reason it is written
# that way: 1, 2 and 5 hours all read correctly.
MSG_ORDERS_STALE = "⏳ Дані оновлювалися понад {hours} год тому"
# Two forms of the same line. The rich one carries the carrier's own logo and
# goes into messages the bot sends itself; the plain one goes into the card a
# customer sends by picking a row out of the inline list, where a custom emoji
# is not ours to use. Same rule as the label above.
MSG_ORDER_TRACKING = "🚚 ТТН: {code}"
MSG_ORDER_LOCATION = "📍 {location}"

# Status messages
MSG_NO_ORDERS = (
    "За номером, яким ти поділилася, замовлень поки немає.\n\n"
    "Якщо замовлення було, можливо, воно оформлене на інший номер — той, що ти "
    "вказала під час покупки, а не той, до якого прив'язаний Telegram. "
    "Напиши нам — знайдемо його вручну."
)
MSG_SUPPORT_FORWARDED = "Передали менеджеру — відповімо тут."

# Menu headers
MSG_MAIN_MENU = "Чим допомогти?"
# Sits inside the input field while the menu keyboard is open, in place of
# "Write a message" — the field is not what we want tapped.
# The greyed-out line in the input field. A place the brand can say hello
# without costing a message — and the one place a pet name reads as warmth
# rather than as familiarity, because it is not addressed at anything.
MSG_MENU_PLACEHOLDER = "Красуне, обери дію"
# Carries the menu that lives in a message rather than under the input field.
# Its own line, because Telegram gives a message one keyboard and these are two
# kinds of keyboard — so the menu always arrives as the second of two messages.
MSG_MENU_PICK = "Обери в меню 👇"
MSG_WEBSITE_INTRO = "Наш магазин — тут 👇"
# Both used to be a single short line above a keyboard, which draws a bubble
# the size of the words in it — a screen that looks like an afterthought next
# to the four buttons under it. A heading and a sentence give the section the
# weight the rest of them have.
MSG_INFO_MENU = (
    "<b>ℹ️ Довідка</b>\n\n"
    "Коротко про магазин, доставку й оплату.\n"
    "Потрібна людина — «💬 Менеджер» у меню.\n\n"
    "Обери розділ 👇"
)
MSG_SETTINGS_MENU = (
    "<b>⚙️ Налаштування</b>\n\n"
    "Номер телефону, за яким ми знаходимо твої замовлення, і мова бота."
)

# Menu content
MSG_SUPPORT_PROMPT = "Напиши свій запит — ми підключимо менеджера 💬"
MSG_NEW_PHONE_PROMPT = "Щоб оновити номер, поділись ним через кнопку нижче 👇"
MSG_PHONE_CHANGED = "Номер телефону змінено! \u2705"

# Onboarding responses
MSG_WELCOME_BACK = "З поверненням! \U0001f338"
MSG_WELCOME_BACK_NAME = "{name}, рада тебе бачити! \U0001f338"
MSG_PHONE_VERIFIED = "Дякуємо! Номер підтверджено \u2705"

# Support relay (admin-side)
# Two lines rather than one sentence: a name cannot be declined, and "від
# Оксана Петренко" is what a sentence around it produces. The second line is
# also where the eye goes first on a phone.
MSG_SUPPORT_ADMIN_NOTE = "📩 Нове звернення\n👤 {who}"
# When we know nothing but the chat id. The CRM name arrives with the buyer
# card, the Telegram one with the message — a customer missing both has neither
# ordered under this number nor a name set on their account.
MSG_CUSTOMER_UNKNOWN = "клієнт"
MSG_SUPPORT_REPLY_INSTRUCTION = "↩️ Відповідайте на переслане повідомлення, щоб відповісти клієнту."
MSG_SUPPORT_NO_REPLY_TARGET = "Будь ласка, відповідайте на переслане повідомлення клієнта."

# Broadcast opt-out
MSG_OPT_OUT_CONFIRM = "Ти відписалась від розсилки. Щоб підписатись знову, надішли /start"
MSG_OPT_IN_CONFIRM = "Ти знову підписана на розсилку!"

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
    "<b>⭐ Твої постійні засоби</b>\n"
    "Те, що ти замовляєш найчастіше. Натисни, щоб замовити ще раз 👇"
)
# When nothing has been bought twice, calling it a favourite is a small lie:
# a quarter of customers have only ever ordered one product.
MSG_FAVOURITES_HEADER_ONCE = (
    "<b>🛍 Ти це вже купувала</b>\n"
    "Натисни, щоб замовити ще раз 👇"
)
# Counts rather than "N разів" to sidestep Ukrainian plural agreement.
MSG_FAVOURITE_LINE = "замовлень: {orders} · {qty} шт · востаннє {date}"
MSG_NO_FAVOURITES = (
    "Поки що нема з чого скласти улюблене — тут зʼявиться те, "
    "що ти замовляєш найчастіше."
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
MSG_FAVOURITES_ALSO = "Також ти купувала: {names}"
BTN_WANT_DISCOUNT = "💰 Хочу знижку на ці товари"
# On a card there is one product and its name is the line above the button, so
# the button says neither.
BTN_WANT_DISCOUNT_CARD = "💰 Хочу знижку"
MSG_DISCOUNT_SENT = (
    "Передали менеджеру твій запит на знижку. Відповімо тут, у боті."
)
MSG_DISCOUNT_ALREADY = "Твій запит уже в роботі — менеджер відповість найближчим часом."
# Said when the message did not reach the manager. It used to say the opposite:
# the send was attempted, the failure was logged, and the customer was thanked
# for a request nobody received. A shop that says "passed on" when nothing was
# passed on is worse than one that admits a hiccup.
MSG_DISCOUNT_FAILED = "Не вдалося передати запит. Спробуй, будь ласка, за кілька хвилин."
MSG_SUPPORT_NOT_DELIVERED = (
    "Не вдалося передати повідомлення менеджеру 😔\n"
    "Спробуй, будь ласка, ще раз за кілька хвилин — твій текст нікуди не зник, "
    "просто надішли його знову."
)
# Goes to the support chat, so Ukrainian regardless of the customer's language.
MSG_DISCOUNT_ADMIN = "💰 Запит на знижку\n👤 {who}"
# Sent on the day, and deliberately promising nothing. There is no discount
# policy yet, and a bot inventing one commits the shop to it — the same reason
# the discount button asks a manager instead of issuing a code. What this does
# is remember, which is the part a shop usually forgets.
MSG_BIRTHDAY = (
    "🎂 З днем народження!\n\n"
    "Дякуємо, що ти з нами — і нехай цей рік буде до тебе добрим 🌸\n"
    "Ось те, що ти любиш найбільше:"
)
BTN_BIRTHDAY_FAVOURITES = "⭐ Мої улюблені"
MSG_BACK_IN_STOCK_HEADER = "🔔 Знову в наявності:"
# Numbered like the order buttons, for the same reason: a 40-character product
# name does not fit on a button next to another one.
BTN_NOTIFY_ME = "🔔 {product}"
BTN_NOTIFY_CANCEL = "🔕 {product}"
MSG_STOCK_HINT = "🔔 номер — повідомимо, щойно зʼявиться"
MSG_SUBSCRIBED = "Повідомимо, щойно товар знову з'явиться."
MSG_UNSUBSCRIBED = "Більше не сповіщатимемо про цей товар."
MSG_FAVOURITE_OUT_OF_STOCK = "зараз немає"

# Favourites as Telegram's own inline list — the panel that opens above the
# input field, with a photo beside every product. The screen shows the top five
# as buttons; this shows everything the customer has ever bought and filters as
# they type. It is the first button on that screen because it is as close as
# the menu key can get to opening the panel itself: a reply-keyboard key sends
# text and nothing else.
BTN_FAVOURITES_ALL = "🔍 Усе, що ти купувала · з фото"
# Two lines under the product's name in the panel. The first is today's price
# and whether it can be bought; the second is MSG_FAVOURITE_LINE, the same
# "замовлень: 3 · 4 шт · востаннє 15.06.2026" the manager sees.
MSG_INLINE_IN_STOCK = "{price} ₴ · у наявності"
MSG_INLINE_OUT_OF_STOCK = "{price} ₴ · зараз немає"
# Picking a result sends this card into the chat. It is sent by the customer,
# not by the bot — that is how inline mode works — so it says what was picked
# and carries the one button worth having under it.
MSG_INLINE_CARD = "<b>{name}</b>\n{detail}"
BTN_BUY = "🛒 Замовити"
# On a card the product's name is the message above the button, so these say
# only what the button does — unlike their twins on the favourites screen,
# which carry the name because five of them sit in a column.
BTN_NOTIFY_CARD = "🔔 Повідомити, коли зʼявиться"
BTN_WAITING_CARD = "✅ Чекаєте — скасувати"
BTN_OPEN_PRODUCT = "🌐 Дивитися на сайті"
# The panel can answer with a button above the results instead of results.
# These three are what it says when there is nothing to show: in somebody
# else's chat, before the number is shared, and before the first order.
MSG_INLINE_NOT_HERE = "Цей список відкривається в чаті з ботом"
MSG_INLINE_NEED_PHONE = "Поділись номером — і побачиш свої товари"
MSG_INLINE_EMPTY = "Тут зʼявиться те, що ти замовляла"
# The list holds every product the customer has bought, so the only nothing it
# can answer with is "nothing was typed that matches" — MSG_INLINE_NOT_IN_
# CATALOGUE lived here until the products without an offer became rows too.
MSG_INLINE_NOTHING_FOUND = "Нічого не знайшли — спробуй іншу назву"
# The bar above the list. It is the way back to the favourites screen, which is
# where the two things the list cannot carry live: the discount request and the
# back-in-stock subscription. Neither can be a button in the list — a message
# sent through inline mode has no callback message to redraw.
MSG_INLINE_SCREEN = "⭐ Знижка та сповіщення про наявність"

# Orders as the same inline list. Two lists, one bot, so the query says which:
# the button inserts this word after the bot's username and the handler reads
# it. Localised like every label, and matched in every language it can be
# rendered in — a button sent months ago outlives a language change.
MSG_INLINE_ORDERS_PREFIX = "замовлення"
BTN_ORDERS_ALL = "🔍 Усі замовлення · з пошуком"
# The two lines under an order in the list: what happened to it and what it
# cost, then what was in it.
MSG_INLINE_ORDER_DETAIL = "{status} · {total} {currency}"
# The button the whole thing is for. A cart permalink with everything that
# order held — one tap and the basket is what it was that day. The second form
# is for an order some of whose products the shop no longer sells: it says how
# much of it can be repeated rather than quietly repeating less.
BTN_REORDER = "🛒 Замовити ще раз"
BTN_REORDER_PARTIAL = "🛒 Замовити ще раз · {available} з {total}"
BTN_TRACK_PARCEL = "🚚 Відстежити посилку"
MSG_INLINE_ORDERS_EMPTY = "Тут зʼявляться твої замовлення"
MSG_INLINE_ORDERS_SCREEN = "📦 Відкрити екран замовлень"

# --- Порадити подрузі ------------------------------------------------------
#
# The third inline mode, and the only one allowed outside the chat with the
# bot: it shows one product from the shop's own catalogue and nothing about
# whoever is sharing it. The other two are a purchase history and are answered
# in the private chat only.
#
# The word after the username, like «замовлення» for the orders list, plus the
# sku. Matched in every language it can be rendered in, because the button that
# writes it was built in whatever language the customer had at the time.
MSG_INLINE_SHARE_PREFIX = "поділитися"
BTN_SHARE_PRODUCT = "🎁 Порадити подрузі"
# What the friend receives. Deliberately impersonal: the message is sent by the
# customer, from their own account, and a line saying what they bought would be
# the bot telling their friend something they did not.
MSG_SHARE_CARD = "<b>{name}</b>\n{price} ₴ · {brand}"
MSG_SHARE_ROW = "Надіслати подрузі"
BTN_SHARE_BUY = "🛒 Купити"
BTN_SHARE_ABOUT = "Що це за магазин"

# --- Реферальна програма ---------------------------------------------------
#
# Клієнтка ділиться ботом, подруга робить перше замовлення — клієнтка отримує
# промокод. Текст самої винагороди лежить у config.yaml (`referral_reward`):
# розмір знижки — це зобов'язання бізнесу, і бот його не вигадує.
BTN_INVITE = "🎁 Запроси подругу"
# Перше замовлення. Розмір знижки й код — у config.yaml: код заводиться в
# адмінці магазину, і бот роздає посилання, а не вигадує знижку.
BTN_FIRST_ORDER = "🛒 Забрати знижку"
# When no code is configured, the offer still stands — the shop promised it in
# the invitation — and a person hands it over. Saying so is what keeps the
# promise honest without withdrawing it.
MSG_FIRST_ORDER_BY_HAND = "\n\nНапиши менеджеру — і він надішле промокод."
MSG_FIRST_ORDER = (
    "<b>🎁 Це буде твоє перше замовлення з нами</b>\n\n"
    "{reward}. Вона вже в посиланні — просто обери, що подобається."
)
MSG_INVITE_SCREEN = (
    "<b>🎁 Запроси подругу</b>\n\n"
    "Надішли їй бота — а коли вона зробить перше замовлення, ми надішлемо "
    "тобі промокод сюди ж, у чат."
)
MSG_INVITE_COUNTS = "Запрошено: {invited} · вже замовили: {earned}"
BTN_INVITE_SEND = "📨 Надіслати подрузі"
# Рядок у панелі й картка, яку отримує подруга.
MSG_INVITE_ROW = "Надіслати запрошення"
MSG_INVITE_CARD = (
    "<b>{brand}</b>\n"
    "Бот, який показує твої замовлення, нагадує про улюблені засоби й пише, "
    "щойно вони знову зʼявляються."
)
# The offer, said in the text as well as drawn on the card: a preview can fail
# to load, and an invitation that then says nothing about the discount is an
# invitation that lost its point.
MSG_INVITE_REWARD = "\n\n🎁 {reward}"
BTN_INVITE_OPEN = "Відкрити бота"
# Приходить, коли подруга зробила перше замовлення.
MSG_REFERRAL_EARNED = (
    "🎁 Твоя подруга зробила перше замовлення!\n"
    "Дякуємо, що порадила нас 🌸"
)
# With a code configured the reward is in the message, and the button applies
# it. Without one the message says what actually happens — a person writes it —
# because "промокод уже в дорозі" from a bot that cannot send one is the kind
# of promise that quietly stops being kept.
MSG_REFERRAL_CODE = "\n\nТвій промокод: <code>{code}</code>\n{reward}"
MSG_REFERRAL_BY_HAND = "\n\nМенеджер надішле промокод найближчим часом."
BTN_REFERRAL_USE = "🛒 Використати знижку"
# Іде менеджеру: поки промокоди виписує людина, це його сигнал до дії.
MSG_REFERRAL_ADMIN_SUMMARY = (
    "🎁 Реферали: {count} — подруги зробили перші замовлення.\n"
    "Час виписати промокоди тим, хто їх привів."
)
BTN_DELIVERY_STATUS = "🚚 Відслідкувати замовлення"
# The carrier's own mark rather than a lorry: every parcel here is a Nova
# Poshta one, and the screen is recognised by that red square before a word of
# it is read. Safe in this string because only the delivery screen uses it —
# see core/emoji.py for why an inline result must never carry one.
MSG_DELIVERY_HEADER = f"<b>{custom_emoji(NOVA_POSHTA, '🚚')} Твої відправлення</b>"
MSG_NO_DELIVERIES = "Наразі немає відправлень для відстеження."
MSG_DELIVERY_STATUS = "Статус: {status}"
MSG_DELIVERY_SCHEDULED = "📅 Очікувана дата: {date}"
MSG_DELIVERY_ACTUAL = "✅ Отримано: {date}"
MSG_DELIVERY_WAREHOUSE = "🏤 {warehouse}"
MSG_DELIVERY_NO_TRACKING = "⏳ ТТН ще не присвоєно"
MSG_DELIVERIES_TRUNCATED = "...та інші відправлення"
