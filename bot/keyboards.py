"""Inline keyboard builders for menu navigation."""
from __future__ import annotations

from urllib.parse import quote, urlencode, urlparse

from aiogram.types import (InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.callbacks import (BroadcastAction, InfoAction, MenuAction,
                           SettingsAction)
from collections.abc import Sequence

from core.i18n import LANGUAGE_NAMES, SUPPORTED, Texts


def _tags(campaign: str, lang: str = "") -> str:
    """The UTM query string every shop link out of the bot carries.

    `campaign` is what tells one place in the bot from another in the shop's
    analytics — and that is the only place they can be told apart at all, since
    Telegram reports nothing when a url button is tapped.
    """
    tags = {"utm_source": "telegram", "utm_medium": "bot", "utm_campaign": campaign}
    if lang:
        tags["locale"] = lang
    return urlencode(tags)


# Telegram draws a button in one of three colours; anything else is the
# client's own default. They are used here by *meaning*, not by importance —
# the same action wears the same colour on every screen, and a screen with five
# ways to buy something is five green buttons because that is what it is.
#
#   success — opens a basket or a checkout, i.e. spends money
#   primary — the way into an inline list, the one thing a screen is for
#   danger  — takes away something the customer set up
#
# Everything else stays uncoloured. A screen where every button is coloured is
# a screen where none of them is.
STYLE_CART = "success"
STYLE_LIST = "primary"
STYLE_UNDO = "danger"


def tagged_website_url(url: str) -> str:
    """Website URL with UTM tags.

    Telegram sends no event when a `url=` button is tapped — there is no callback
    to hook. Tagging the link is the only way these clicks can ever be counted,
    and it happens in the shop's analytics, not here.
    """
    if not url:
        return url
    tags = _tags("main_menu")
    return f"{url}{'&' if urlparse(url).query else '?'}{tags}"


def discount_url(website_url: str, code: str, lang: str = "uk") -> str:
    """A link that applies a discount code and lands in the shop.

    `/discount/{code}?redirect=…` is Shopify's own mechanism, and the reason
    this works without an Admin token: the code is created once by a person in
    the shop's admin, and the bot only hands out the link that puts it in
    somebody's session. Verified against the live storefront — a 302 to the
    redirect target with the tags intact.
    """
    landing = "/?" + urlencode({
        "utm_source": "telegram",
        "utm_medium": "bot",
        "utm_campaign": "first_order",
        "locale": lang,
    })
    return (f"{website_url.rstrip('/')}/discount/{quote(code, safe='')}"
            f"?{urlencode({'redirect': landing})}")


def cart_url(website_url: str, variant_ids: Sequence[int], lang: str = "uk",
             campaign: str = "favourites") -> str:
    """A Shopify cart permalink for exactly these variants.

    /cart/{variant}:1,{variant}:1 hands the shop a whole basket in a URL and
    redirects to checkout with it. Verified against the live storefront: the
    redirect keeps both the locale and the UTM tags, so a purchase started here
    is attributable in the shop's own analytics — which is the only place it can
    be, since Telegram reports nothing when a url button is tapped.

    Note that a permalink *replaces* the basket rather than adding to it. That
    is the intent here: the button says "order this", and arriving at checkout
    with something the customer put in their cart last week added to it would be
    a surprise nobody asked for.
    """
    items = ",".join(f"{variant_id}:1" for variant_id in variant_ids)
    return f"{website_url.rstrip('/')}/cart/{items}?{_tags(campaign, lang)}"


def shop_url(website_url: str, lang: str = "uk",
             campaign: str = "first_order") -> str:
    """The shop's front page, tagged.

    For the customer who has nothing to reorder and no code to apply: the
    discount link cannot be built without a code, and «there is no code yet»
    is a reason to send someone to a manager, not a reason to leave them with
    no way into the shop at all.
    """
    return f"{website_url.rstrip('/')}/?{_tags(campaign, lang)}"


def product_url(website_url: str, handle: str, lang: str = "uk",
                campaign: str = "favourites") -> str:
    """The product's own page on the shop.

    Where a customer is sent when there is nothing to put in a basket: the
    product is one they have bought and cannot buy today. The page says so in
    the shop's own words, which is more than a card with no button under it.
    """
    return f"{website_url.rstrip('/')}/products/{handle}?{_tags(campaign, lang)}"


def share_phone_kb(t: Texts, *, with_manager: bool = False) -> ReplyKeyboardMarkup:
    """Reply keyboard with the single request_contact button.

    request_contact is the only way to prove phone ownership: Telegram fills in
    contact.user_id with the sender's own id, which handlers verify.

    `with_manager` adds a second key, for the one outcome where pressing the
    first again cannot help: the contact arrived and the number inside it did
    not parse. Sharing it once more produces the same number and the same
    refusal, so that screen needs an exit that is not the button that just
    failed.
    """
    keyboard = [[KeyboardButton(text=t.BTN_SHARE_PHONE, request_contact=True)]]
    if with_manager:
        keyboard.append([KeyboardButton(text=t.BTN_SUPPORT)])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_menu_kb(t: Texts, name: str = "") -> ReplyKeyboardMarkup:
    """The main menu, as the keyboard under the input field.

    It is a reply keyboard and not an inline one for a reason that has nothing
    to do with taste: the square toggle in the input row — the thing people
    reach for to get the menu back — is drawn by the client only while a reply
    keyboard exists. No API creates it. An inline menu, however tidy, leaves
    that corner of the screen empty.

    `is_persistent` is deliberately **not** set. It sounds like what we want —
    "always show the keyboard" — but it is what takes the toggle icon away:
    per the API, with it off "the custom keyboard can be hidden and opened with
    a keyboard icon". The icon is the point. A menu that cannot be put away is
    also a menu that cannot be brought back.

    The placeholder replaces "Write a message" in a field we would rather
    nobody typed into.

    Three to a row is safe here, unlike inline: a reply keyboard spans the
    screen instead of the message bubble.

    «🌐 Сайт» is a key like the others because a reply button cannot carry a
    URL — pressing it makes the bot answer with the link.

    Every key opens a screen, «⭐ Улюблені» included. It used to be a `web_app`
    key wherever `webapp_url` was set: the page in webapp/ asked the client to
    write "@bot " into the input field and closed, which opened the inline list
    without ever showing the screen. Two entries that looked alike behaved
    differently — «📦 Замовлення» opened a screen, «⭐ Улюблені» opened a search
    box — and the search is one tap further in, behind «🔍» on the screen
    itself, where somebody who wants to search will look for it.
    """
    builder = ReplyKeyboardBuilder()
    # Six, not seven: «🚚 Відслідкувати замовлення» was a second key for what a
    # customer thinks of as one question, and the answer it gave now lives on
    # the order it belongs to — one tap on «Де посилка?» inside the card.
    for label in (t.BTN_ORDERS, t.BTN_FAVOURITES,
                  t.BTN_SUPPORT, t.BTN_INVITE,
                  t.BTN_WEBSITE, t.BTN_INFO, t.BTN_SETTINGS):
        builder.button(text=label)
    builder.adjust(2, 2, 3)
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=False,
        # The one line on this keyboard addressed at a person rather than at a
        # screen, so it is the one that carries their name. `name` is the
        # customer's Telegram first name and may be anything or nothing —
        # Texts.menu_placeholder is what decides how much of it is usable.
        input_field_placeholder=t.menu_placeholder(name),
    )


def main_menu_inline_kb(t: Texts, website_url: str) -> InlineKeyboardMarkup:
    """The main menu as buttons inside a message, beside the keyboard below.

    Both menus lead to the same screens, favourites included: a menu entry
    answers "show me my products", and the panel that searches them is one tap
    further in, behind «🔍» on the screen it opens.

    This menu still exists for what only an inline button can do — the panel
    behind that «🔍» is a switch_inline_query_current_chat button, and a
    KeyboardButton has no such field. It is just no longer the menu entry
    itself.

    The keyboard below stays where it is. It is what draws the ☰ toggle in the
    input row — no API creates that — and it is what the customer reaches for
    when this message has scrolled away.

    Layout 2+2+2. It was 2+1+2+2 while «🚚 Відслідкувати замовлення» was here:
    that label beside anything else wraps onto a second line. The entry is gone
    — the parcel is answered for on the order it belongs to — and the six that
    remain are short enough to pair up.
    """
    builder = InlineKeyboardBuilder()
    # "open_*" rather than the bare section names: MenuAction("info") and
    # MenuAction("support") already mean "go back to the page list" and "ask
    # for a person" *inside* a screen, and those two edit the message they came
    # from. A menu entry must not — it would replace the menu with the section.
    builder.button(text=t.BTN_ORDERS, callback_data=MenuAction(action="open_orders"))
    builder.button(text=t.BTN_FAVOURITES,
                   callback_data=MenuAction(action="open_favourites"))
    builder.button(text=t.BTN_SUPPORT,
                   callback_data=MenuAction(action="open_support"))
    builder.button(text=t.BTN_INVITE,
                   callback_data=MenuAction(action="open_invite"))
    builder.button(text=t.BTN_WEBSITE, url=tagged_website_url(website_url))
    builder.button(text=t.BTN_INFO, callback_data=MenuAction(action="open_info"))
    builder.button(text=t.BTN_SETTINGS,
                   callback_data=MenuAction(action="open_settings"))
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def website_kb(t: Texts, website_url: str) -> InlineKeyboardMarkup:
    """The shop link, which only an inline button can carry — and the way back."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_WEBSITE, url=tagged_website_url(website_url))
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def menu_kb(t: Texts) -> InlineKeyboardMarkup:
    """Just the way back, for a screen with nothing else to press.

    The support prompt is the one: while it is on screen the customer is
    expected to type, and until this existed the only way out of that was to
    type something.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    return builder.as_markup()


def info_menu_kb(t: Texts) -> InlineKeyboardMarkup:
    """The four pages, and the way back to the menu they were opened from.

    The Back button is here again because navigation edits one message now: the
    menu that opened this list was replaced by it, so without this the way back
    is the keyboard below the input field — which answers with a new message
    and leaves the trail this screen exists to avoid.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_ABOUT, callback_data=InfoAction(page="about"))
    builder.button(text=t.BTN_CONTACTS, callback_data=InfoAction(page="contacts"))
    builder.button(text=t.BTN_PAYMENT, callback_data=InfoAction(page="payment"))
    builder.button(text=t.BTN_DELIVERY, callback_data=InfoAction(page="delivery"))
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def broadcast_confirm_kb(t: Texts) -> InlineKeyboardMarkup:
    """Yes/No confirmation for the admin broadcast flow."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_BROADCAST_YES, callback_data=BroadcastAction(action="send"))
    builder.button(text=t.BTN_BROADCAST_NO, callback_data=BroadcastAction(action="cancel"))
    builder.adjust(2)
    return builder.as_markup()


def settings_menu_kb(t: Texts, *, has_phone: bool = True) -> InlineKeyboardMarkup:
    """Build the settings submenu inline keyboard (2 items, 1 per row).

    One per row because "Налаштування:" is a short message and an inline
    keyboard is only as wide as the bubble it hangs under — «📱 Змінити номер»
    does not survive being squeezed into half of it.
    """
    builder = InlineKeyboardBuilder()
    # «Змінити» when there is one, «Поділитися» when there is not. The screen
    # above says «поділись ним кнопкою нижче», and a button offering to change
    # a number she has never given contradicts the sentence pointing at it.
    builder.button(text=t.BTN_CHANGE_PHONE if has_phone else t.BTN_SHARE_PHONE,
                   callback_data=SettingsAction(action="phone"))
    builder.button(text=t.BTN_LANGUAGE, callback_data=SettingsAction(action="language"))
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def language_kb(current: str, t: Texts | None = None) -> InlineKeyboardMarkup:
    """One button per supported language, ticking the active one.

    **`t` is what adds the way out, and both call sites need different things.**

    From Налаштування this replaces the live screen, so without a way back the
    only exits are changing the language — which she may have come only to
    look at — or typing /menu. Production runs `bottom_menu: false`, so there is
    no keyboard under the input field to fall back on either. It was the one
    inline keyboard in the bot with no route out of it, and the guard test in
    tests/test_main_menu.py did not catch it for the plainest of reasons: this
    function was not in the list it checks.

    The offer in `bot/handlers/common.py` passes no `t` and must not: it is a
    standalone message rather than the live screen, sent once when Telegram's
    language differs from ours. Ignoring it costs nothing and leaves her exactly
    where she was, so "back" there would point at a screen she never left.

    Back to Налаштування rather than to the main menu, which is the same shape
    the Довідка pages use (`bot/handlers/info.py::_back_to_info_kb`): a
    sub-screen returns to its parent, and the parent carries the menu.
    """
    builder = InlineKeyboardBuilder()
    for code, name in LANGUAGE_NAMES.items():
        mark = " ✅" if code == current else ""
        builder.button(text=f"{name}{mark}", callback_data=SettingsAction(action="lang", value=code))
    if t is not None:
        builder.button(text=t.BTN_BACK,
                       callback_data=MenuAction(action="open_settings"))
        # The languages share one row; the way out gets its own, so it is not
        # read as a third language.
        builder.adjust(len(SUPPORTED), 1)
        return builder.as_markup()
    builder.adjust(len(SUPPORTED))
    return builder.as_markup()
