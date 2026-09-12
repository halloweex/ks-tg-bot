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

from aiogram.types import (InlineKeyboardMarkup, ReplyKeyboardMarkup,
                           ReplyKeyboardRemove)

from bot.callbacks import MenuAction
from bot.keyboards import main_menu_inline_kb, main_menu_kb
from bot.screen import send_main_menu
from core.i18n import Texts
from tests.conftest import REPO_ROOT

SHOP = "https://koreanstory.com.ua"
T = Texts("uk")


def _config(bottom_menu: bool = True):
    return SimpleNamespace(website_url=SHOP, bottom_menu=bottom_menu)


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


# --- every entry opens a screen --------------------------------------------

def test_favourites_opens_a_screen_like_every_other_entry():
    """It used to hand the input field straight to the inline list, so the two
    halves of one menu behaved differently: «📦 Замовлення» opened a screen and
    «⭐ Улюблені» opened a search box. The search is still there, one tap in,
    behind «🔍» on the screen this opens."""
    favourites = next(b for b in _inline_buttons() if b.text == T.BTN_FAVOURITES)
    assert favourites.switch_inline_query_current_chat is None
    assert MenuAction.unpack(favourites.callback_data).action == "open_favourites"


def test_no_menu_entry_takes_over_the_input_field():
    assert not any(b.switch_inline_query_current_chat is not None
                   for b in _inline_buttons())


def test_the_shop_is_a_link_here_and_a_message_below():
    """The reply keyboard cannot carry a URL — pressing «🌐 Сайт» there makes
    the bot answer with the link. In the message it is the link itself."""
    website = next(b for b in _inline_buttons() if b.text == T.BTN_WEBSITE)
    assert website.url.startswith(SHOP)
    assert "utm_source=telegram" in website.url


# --- the shape ---------------------------------------------------------------

def test_every_label_is_short_enough_to_share_a_row():
    """An inline keyboard is only as wide as the message bubble, and a label
    that wraps takes the shape of the grid with it — which is what took this
    menu off the screen the last time it was inline (CHANGELOG, 2026-07-31).
    «🚚 Відслідкувати замовлення» was the one that could not pair up, and it is
    no longer an entry: the parcel is answered for on its own order now."""
    assert all(len(row) <= 2 for row in _inline_rows())
    assert max(len(b.text) for b in _inline_buttons()) <= 20


def test_the_key_below_is_a_plain_key_and_opens_the_screen():
    """It was a Mini App key wherever a page was published: opening it asked
    the client to write "@bot " into the input field and closed, which is how a
    menu key ended up opening a search box instead of a screen."""
    key = _favourites_key(main_menu_kb(T))
    assert key.web_app is None
    assert key.text == T.BTN_FAVOURITES


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


def test_the_switch_takes_the_keyboard_off_the_screen_it_is_already_on():
    """A reply keyboard belongs to the message that sent it, and nothing
    updates the one somebody is looking at. Removing it is a markup like any
    other, so it rides along with whatever the bot was going to say anyway."""
    sent = []

    async def answer(text, reply_markup=None, **kwargs):
        sent.append((text, reply_markup))

    message = SimpleNamespace(answer=answer)
    asyncio.run(send_main_menu(message, T, _config(bottom_menu=False), "Вітаємо"))

    assert isinstance(sent[0][1], ReplyKeyboardRemove)
    # The menu itself does not go anywhere: with the keyboard off, the message
    # is the only menu there is.
    assert isinstance(sent[1][1], InlineKeyboardMarkup)


# --- navigation is one message changing -------------------------------------

def test_every_screen_the_menu_opens_can_bring_it_back():
    """A menu entry replaces the menu message with the section, so each
    section carries the way back. Without it the only route is the keyboard
    below the input field, which answers with a new message — the trail all of
    this exists to avoid.

    Every screen, not only the sub-menus: this was reported from a chat where
    somebody opened their orders and could not get out of them."""
    import json

    from bot.handlers.orders import _favourites_kb, _no_orders_kb, _orders_kb
    from bot.keyboards import info_menu_kb, menu_kb, settings_menu_kb, website_kb
    from core.domain.offer import Offer

    order = {"id": 1, "order_name": "", "status_name": "completed",
             "status_group_id": 1, "grand_total": 100, "currency": "грн",
             "ordered_at": "2026-08-01T10:00:00", "tracking_code": "",
             "products_json": json.dumps([{"name": "A", "qty": 1, "sku": "1"}])}
    favourite = {"name": "A", "sku": "1", "orders": 2, "qty": 2, "last": "2026-08-01"}
    offer = Offer(sku="1", variant_id=1, handle="h", title="A", price="680",
                  available=True)

    screens = (
        info_menu_kb(T),
        settings_menu_kb(T),
        website_kb(T, SHOP),
        menu_kb(T),
        _orders_kb([order], T),
        _no_orders_kb(T),
        _favourites_kb([favourite], {"1": offer}, {}, set(), T, SHOP),
    )
    for keyboard in screens:
        actions = {MenuAction.unpack(b.callback_data).action
                   for row in keyboard.inline_keyboard for b in row
                   if b.callback_data and b.callback_data.startswith("menu:")}
        assert "menu" in actions


# A screen may also lead out through its parent rather than straight to the
# menu — the Довідка pages do, and so does the language screen. What it may not
# do is lead nowhere.
LEADS_SOMEWHERE = {"menu", "info", "open_settings"}


def test_the_language_screen_has_a_way_out():
    """It did not, and the list above is why nobody noticed: `language_kb` was
    simply not in it.

    From Налаштування this replaces the live screen, so the only exits were
    changing the language — which she may have opened it only to look at — or
    typing /menu. It was written while production ran bottom_menu: false, with no
    keyboard
    under the input field either."""
    from bot.keyboards import language_kb

    keyboard = language_kb("uk", T)
    actions = {MenuAction.unpack(b.callback_data).action
               for row in keyboard.inline_keyboard for b in row
               if b.callback_data and b.callback_data.startswith("menu:")}
    assert actions & LEADS_SOMEWHERE, (
        "the one inline keyboard in the bot with no route out of it")


def test_the_language_offer_deliberately_has_none():
    """The other call site, and the asymmetry is the point rather than an
    oversight. The offer is a standalone message sent once when Telegram's
    language differs from ours; ignoring it leaves her exactly where she was, so
    a "back" there would point at a screen she never left."""
    from bot.keyboards import language_kb

    rows = language_kb("uk").inline_keyboard
    assert [b.text for row in rows for b in row] == ["Українська ✅", "English"]


def test_every_page_of_the_dovidka_leads_out_through_its_parent():
    """The precedent the language screen follows. Not in the tuple above
    because its way out is «◀️ Назад» to the page list rather than the menu —
    which is a route, and a dead end is the absence of one."""
    from bot.handlers.info import _back_to_info_kb

    for markup in (_back_to_info_kb(T), _back_to_info_kb(T, "https://insta")):
        actions = {MenuAction.unpack(b.callback_data).action
                   for row in markup.inline_keyboard for b in row
                   if b.callback_data and b.callback_data.startswith("menu:")}
        assert actions & LEADS_SOMEWHERE


def test_the_way_back_is_handled():
    assert "menu" in _handled_actions()


# --- colour ------------------------------------------------------------------

def test_the_same_action_wears_the_same_colour_everywhere():
    """Colour is used by meaning, not by importance: green is "this spends
    money", blue is the way into an inline list, red takes something away. A
    screen where every button is coloured is a screen where none of them is."""
    import asyncio

    from bot.handlers.inline import _card_kb, _order_kb
    from bot.handlers.orders import _favourites_kb, _orders_kb
    from core.domain.offer import Offer

    offer = Offer(sku="1", variant_id=11, handle="h", title="T", price="680",
                  available=True)
    sold_out = Offer(sku="2", variant_id=22, handle="h", title="T", price="680",
                     available=False)
    favourites = [{"name": "A", "sku": "1", "orders": 2, "qty": 2, "last": "2026-08-01"},
                  {"name": "B", "sku": "2", "orders": 1, "qty": 1, "last": "2026-08-01"}]

    screen = _favourites_kb(favourites, {"1": offer}, {"2": 0}, {"2"}, T, SHOP)
    styles = {b.text.split(" ·")[0]: b.style
              for row in screen.inline_keyboard for b in row}
    assert styles[T.BTN_FAVOURITES_ALL.split(" ·")[0]] == "primary"
    assert styles["🛒 A"] == "success"
    assert styles["✅ Чекаєте: B"] == "danger"
    assert styles[T.BTN_WANT_DISCOUNT] is None, "not everything is coloured"

    card = _card_kb("1", offer, T, SHOP, waiting=False, out_of_stock=False)
    assert card.inline_keyboard[0][0].style == "success", "a card's buy button"

    waiting = _card_kb("2", sold_out, T, SHOP, waiting=True, out_of_stock=True)
    assert waiting.inline_keyboard[0][0].style == "danger", "cancelling a wait"

    offered = _card_kb("2", sold_out, T, SHOP, waiting=False, out_of_stock=True)
    assert offered.inline_keyboard[0][0].style is None, \
        "offering to notify takes nothing away"

    row = {"tracking_code": ""}
    products = [{"name": "A", "sku": "1", "qty": 1}]
    basket = _order_kb(row, products, {"1": offer}, T, SHOP)
    assert basket.inline_keyboard[0][0].style == "success", "ordering it again"


def test_the_menu_command_lets_go_of_the_support_state():
    """«📋 Меню» as a button has always cleared the state; as a command it did
    not even take one.

    So a customer who opened support, read «розкажи, що турбує», changed her
    mind and typed /menu got the menu and stayed in the support state. Her next
    message, about anything at all, went to a manager. One screen with two
    entrances behaving differently, which is the defect this bot has paid for
    more than once."""
    import asyncio
    from types import SimpleNamespace

    from bot.handlers import menu as mod
    from bot.states import SupportStates

    class _State:
        def __init__(self) -> None:
            self.state = SupportStates.waiting_message

        async def clear(self) -> None:
            self.state = None

        async def set_state(self, state) -> None:
            self.state = state

    state = _State()
    said: list[str] = []

    async def answer(text, **kwargs):
        said.append(text)
        return SimpleNamespace(message_id=1)

    message = SimpleNamespace(chat=SimpleNamespace(id=1),
                              from_user=SimpleNamespace(id=1), answer=answer)
    config = SimpleNamespace(env=SimpleNamespace(admin_ids=[]),
                             website_url="https://shop.example", bottom_menu=False)

    asyncio.run(mod.restore_menu(message, state, config, T))

    assert state.state is None, (
        "she asked for the menu and is still talking to a manager")
    assert said, "and the menu itself still arrives"


# --- the keyboard below, now that production sends it again -------------------

def _keys_on_the_keyboard_below() -> set[str]:
    """The string-table keys `main_menu_kb` puts on its keys, read from source.

    By AST rather than by rendering, because the rendering is what the other
    tests check: here the question is which *keys* the keyboard is built from, and
    a label matched back to a key would be ambiguous the day two keys hold the
    same words.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("bot/keyboards.py").read_text(encoding="utf-8"))
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "main_menu_kb")
    return {node.attr for node in ast.walk(fn)
            if isinstance(node, ast.Attribute) and node.attr.startswith("BTN_")}


def _keys_the_menu_router_answers() -> set[str]:
    """Every key wired up with `@_menu("…")` in bot/handlers/menu.py."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("bot/handlers/menu.py").read_text(encoding="utf-8"))
    # AsyncFunctionDef, not FunctionDef: every handler here is an `async def`,
    # and the first version of this test quietly found nothing.
    return {decorator.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and getattr(decorator.func, "id", "") == "_menu"
            and decorator.args and isinstance(decorator.args[0], ast.Constant)}


def test_every_key_on_the_keyboard_below_has_a_handler():
    """Production sends this keyboard again since 2026-09-12 (`bottom_menu: true`),
    so a key nothing answers is a customer tapping and getting silence — and a
    reply key sends plain text, which no callback guard can catch."""
    orphans = _keys_on_the_keyboard_below() - _keys_the_menu_router_answers()
    assert not orphans, f"keys on the keyboard with no handler: {sorted(orphans)}"


def test_every_key_is_matched_in_every_language_and_form():
    """The router filters are built at import time from `variants(key)`, and the
    label a customer sees depends on their language *and* on the grammatical form
    they are addressed in. «🎁 Запроси подругу» is the one that changes with the
    form, so a form missing from that set is a man holding a keyboard whose key
    does nothing."""
    from core.i18n import FORMS, SUPPORTED, Texts, variants

    for key in _keys_on_the_keyboard_below():
        accepted = variants(key)
        for lang in SUPPORTED:
            for gender in FORMS:
                label = getattr(Texts(lang, gender), key)
                assert label in accepted, f"{key} in {lang}/{gender} is not matched"


def test_the_labels_below_fit_their_rows_in_every_form():
    """Same rule as the inline menu above, over the forms: the keyboard is laid
    out two, two and three to a row, and a long label makes the grid ragged."""
    from core.i18n import FORMS, SUPPORTED, Texts

    for lang in SUPPORTED:
        for gender in FORMS:
            keyboard = main_menu_kb(Texts(lang, gender))
            longest = max(len(key.text) for row in keyboard.keyboard for key in row)
            assert longest <= 20, f"{lang}/{gender}: a {longest}-character label"
