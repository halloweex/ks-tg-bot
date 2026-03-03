"""All Ukrainian UI strings as module-level constants.

No string literals should appear in handler files — import from here instead.
"""

# Greeting (warm personal tone)
# Use .format(brand_name=config.brand_name) at runtime
GREETING = (
    "Вітаємо у {brand_name}! 🌸\n\n"
    "Я допоможу вам, покажу та проведу за руку у світ краси.\n\n"
    "Натисніть кнопку нижче, щоб поділитися номером, "
    "або введіть його вручну у форматі +380XXXXXXXXX."
)

BTN_SHARE_PHONE = "📱 Поділитися номером"

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
BTN_CHANGE_PHONE = "📱 Змінити номер"
BTN_LANGUAGE = "🌍 Мова"

# Status messages
MSG_NO_ORDERS = "У вас поки немає замовлень."
MSG_PHONE_ACCEPTED = "Номер прийнято! Реєструю вас..."
MSG_SUPPORT_FORWARDED = "Ваше повідомлення надіслано менеджеру. Очікуйте відповідь."

# Onboarding responses
MSG_WELCOME_BACK = "З поверненням! \U0001f338"
MSG_PHONE_VERIFIED = "Номер підтверджено! \u2705"
