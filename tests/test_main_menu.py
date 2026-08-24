"""The menu exists twice, and the two halves have to stay one menu.

Neither form can do the other's job: only a reply keyboard draws the ☰ toggle
in the input row, and only an inline button can hand the input field to the
inline list. So both are sent, and these tests are about what would go wrong if
they drifted — a section reachable from one and not the other, a button whose
callback nobody handles, the favourites button quietly losing the one property
it exists for.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
from types import SimpleNamespace

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from bot.callbacks import MenuAction
from bot.keyboards import main_menu_inline_kb, main_menu_kb
from bot.screen import send_main_menu
from core.i18n import Texts
from tests.conftest import REPO_ROOT

SHOP = "https://koreanstory.com.ua"
WEBAPP = "https://halloweex.github.io/ks-tg-bot/"
T = Texts("uk")


def _config(webapp_url: str = WEBAPP):
    return SimpleNamespace(website_url=SHOP, webapp_url=webapp_url)


def _inline_rows():
    return main_menu_inline_kb(T, SHOP).inline_keyboard


def _inline_buttons():
    return [button for row in _inline_rows() for button in row]


def _handled_actions() -> set[str]:
    """Every MenuAction the menu module has a handler for."""
    source = (pathlib.Path(REPO_ROOT) / "bot/handlers/menu.py").read_text()
    return set(re.findall(r'MenuAction\.filter\(F\.action == "([a-z_]+)"\)', source))


# --- the two halves are one menu -------------------------------------------

def test_both_menus_offer_the_same_sections():
    """A section on one and not the other is a section half the customers
    cannot reach — which half depending on where they last tapped."""
    below = [button.text for row in main_menu_kb(T).keyboard for button in row]
    assert sorted(button.text for button in _inline_buttons()) == sorted(below)


def test_every_button_in_the_message_leads_somewhere():
    """A callback nobody handles is a button that answers with silence."""
    for button in _inline_buttons():
        assert (button.callback_data or button.url
                or button.switch_inline_query_current_chat is not None), button.text


def test_every_callback_in_the_menu_has_a_handler():
    handled = _handled_actions()
    wanted = {MenuAction.unpack(button.callback_data).action
              for button in _inline_buttons() if button.callback_data}
    assert wanted <= handled, f"no handler for {sorted(wanted - handled)}"


def test_the_menu_does_not_reuse_the_callbacks_that_edit_a_screen():
    """MenuAction("info") and MenuAction("support") already mean "back to the
    page list" and "ask for a person" inside a screen, and both edit the
    message they came from. A menu entry carrying one would replace the menu
    with the section — and the favourites button lives on that message."""
    actions = {MenuAction.unpack(button.callback_data).action
               for button in _inline_buttons() if button.callback_data}
    assert not actions & {"info", "support"}


# --- the button the whole thing is for -------------------------------------

def test_favourites_hands_the_input_field_to_the_inline_list():
    """The empty query is what makes the client write "@bot " and open the
    list. A KeyboardButton has no such field, which is why this menu exists."""
    favourites = next(b for b in _inline_buttons() if b.text == T.BTN_FAVOURITES)
    assert favourites.switch_inline_query_current_chat == ""
    assert favourites.callback_data is None


def test_the_shop_is_a_link_here_and_a_message_below():
    """The reply keyboard cannot carry a URL — pressing «🌐 Сайт» there makes
    the bot answer with the link. In the message it is the link itself."""
    website = next(b for b in _inline_buttons() if b.text == T.BTN_WEBSITE)
    assert website.url.startswith(SHOP)
    assert "utm_source=telegram" in website.url


# --- the shape ---------------------------------------------------------------

def test_the_long_delivery_label_gets_a_row_of_its_own():
    """An inline keyboard is only as wide as the message bubble. This label
    beside anything else wraps onto a second line, which is what took the menu
    off the screen the last time it was inline (CHANGELOG, 2026-07-31)."""
    row = next(row for row in _inline_rows()
               if any(b.text == T.BTN_DELIVERY_STATUS for b in row))
    assert len(row) == 1


def test_the_key_below_opens_the_mini_app_that_fills_the_input_field():
    """A KeyboardButton has no switch_inline field — the insertion can only be
    asked for from inside the client, and a Mini App is the only thing down
    here that runs there (webapp/index.html)."""
    key = _favourites_key(main_menu_kb(T, WEBAPP))
    assert key.web_app is not None and key.web_app.url == WEBAPP


def test_without_the_page_the_key_is_the_plain_one_it_used_to_be():
    """The page is published separately from the bot. Until it is up, the key
    must open the favourites screen rather than a Telegram sheet with a 404 in
    it — which is what an unpublished Mini App url looks like to a customer."""
    key = _favourites_key(main_menu_kb(T, ""))
    assert key.web_app is None


def _favourites_key(keyboard):
    return next(key for row in keyboard.keyboard for key in row
                if key.text == T.BTN_FAVOURITES)


def test_the_menu_arrives_as_two_messages_because_it_has_to():
    """Telegram gives a message one markup, and these are two kinds of markup:
    the keyboard under the input field and the buttons in the bubble."""
    sent = []

    async def answer(text, reply_markup=None, **kwargs):
        sent.append((text, reply_markup))

    message = SimpleNamespace(answer=answer)
    asyncio.run(send_main_menu(message, T, _config(), "Вітаємо"))

    assert [text for text, _markup in sent] == ["Вітаємо", T.MSG_MENU_PICK]
    assert isinstance(sent[0][1], ReplyKeyboardMarkup)
    assert isinstance(sent[1][1], InlineKeyboardMarkup)


# --- navigation is one message changing -------------------------------------

def test_every_screen_the_menu_opens_can_bring_it_back():
    """A menu entry replaces the menu message with the section, so each
    section carries the way back. Without it the only route is the keyboard
    below the input field, which answers with a new message — the trail all of
    this exists to avoid."""
    from bot.keyboards import info_menu_kb, settings_menu_kb

    for keyboard in (info_menu_kb(T), settings_menu_kb(T)):
        actions = {MenuAction.unpack(b.callback_data).action
                   for row in keyboard.inline_keyboard for b in row
                   if b.callback_data and b.callback_data.startswith("menu:")}
        assert "menu" in actions


def test_the_way_back_is_handled():
    assert "menu" in _handled_actions()
