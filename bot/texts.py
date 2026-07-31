"""All Ukrainian UI strings as module-level constants.

No string literals should appear in handler files — import from here instead.
"""
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

# Main menu buttons
BTN_ORDERS = "📦 Мої замовлення"
BTN_INFO = "ℹ️ Дізнатися більше"
BTN_SUPPORT = "💬 Зв'язатися з менеджером"
BTN_WEBSITE = "🌐 Веб-сайт"
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
MSG_ORDERS_LOADING = "Завантажую ваші замовлення..."
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
BTN_SHOW_ITEMS = "🔎 Товари: {order}"
BTN_HIDE_ITEMS = "🔼 Згорнути: {order}"
MSG_ORDER_TRACKING = "🚚 ТТН: {code}"
MSG_ORDER_LOCATION = "📍 {location}"

# Status messages
MSG_NO_ORDERS = (
    "У вас поки немає замовлень за номером, яким ви поділилися.\n\n"
    "Якщо ви робили замовлення, можливо, воно оформлене на інший номер — "
    "той, що ви вказали під час покупки, а не той, до якого прив'язаний Telegram. "
    "Напишіть нам, і ми знайдемо його вручну."
)
MSG_PHONE_ACCEPTED = "Номер прийнято! Реєструю вас..."
MSG_SUPPORT_FORWARDED = "Ваше повідомлення надіслано менеджеру. Очікуйте відповідь."

# Menu headers
MSG_MAIN_MENU = "Чим допомогти?"
MSG_INFO_MENU = "Оберіть розділ:"
MSG_SETTINGS_MENU = "Налаштування:"

# Menu content
MSG_LANGUAGE_CURRENT = "Поточна мова: Українська \u2705"
MSG_SUPPORT_PROMPT = "Напишіть ваше повідомлення, і ми передамо його менеджеру:"
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

# Order cache
MSG_ORDER_LATEST = "⭐ Останнє замовлення:"
MSG_ORDERS_SYNCING = "Оновлюю замовлення..."

# Delivery status
BTN_FAVOURITES = "⭐ Улюблені товари"
MSG_FAVOURITES_HEADER = "<b>⭐ Те, що ви любите найбільше</b>"
# When nothing has been bought twice, calling it a favourite is a small lie:
# a quarter of customers have only ever ordered one product.
MSG_FAVOURITES_HEADER_ONCE = "🛍 Товари, які ви замовляли:"
# Counts rather than "N разів" to sidestep Ukrainian plural agreement.
MSG_FAVOURITE_LINE = "замовлень: {orders} · {qty} шт · востаннє {date}"
MSG_NO_FAVOURITES = (
    "Поки що нема з чого скласти улюблене — тут з'являться товари, "
    "які ви замовляєте найчастіше."
)
BTN_WANT_DISCOUNT = "💰 Хочу знижку на ці товари"
MSG_DISCOUNT_SENT = (
    "Передали менеджеру ваш запит на знижку. Ми звʼяжемось із вами тут, у боті."
)
MSG_DISCOUNT_ALREADY = "Ваш запит уже в роботі — менеджер відповість найближчим часом."
# Goes to the support chat, so Ukrainian regardless of the customer's language.
MSG_DISCOUNT_ADMIN = "💰 Запит на знижку (chat_id: {chat_id}):"
MSG_BACK_IN_STOCK_HEADER = "🔔 Знову в наявності:"
BTN_NOTIFY_ME = "🔔 Сповістити: {product}"
BTN_NOTIFY_CANCEL = "🔕 Не сповіщати: {product}"
MSG_SUBSCRIBED = "Повідомимо, щойно товар знову з'явиться."
MSG_UNSUBSCRIBED = "Більше не сповіщатимемо про цей товар."
MSG_FAVOURITE_OUT_OF_STOCK = "зараз немає"
BTN_DELIVERY_STATUS = "🚚 Статус доставки"
MSG_DELIVERY_HEADER = "<b>🚚 Ваші відправлення</b>"
MSG_DELIVERY_LOADING = "Перевіряю статус доставки..."
MSG_NO_DELIVERIES = "Наразі немає відправлень для відстеження."
MSG_DELIVERY_STATUS = "Статус: {status}"
MSG_DELIVERY_SCHEDULED = "📅 Очікувана дата: {date}"
MSG_DELIVERY_ACTUAL = "✅ Отримано: {date}"
MSG_DELIVERY_WAREHOUSE = "🏤 {warehouse}"
MSG_DELIVERY_NO_TRACKING = "⏳ ТТН ще не присвоєно"
