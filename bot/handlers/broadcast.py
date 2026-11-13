"""Broadcast handlers — opt-out commands and the admin flow that queues a send.

The sending itself left with stage 6: the flow below records a job, queues one
message per recipient and stops. Everything that used to be here — the driver,
the per-recipient retry, the blocked-chat handling, the pacing, the lock that
kept two jobs from overlapping and the resume-after-restart — is the outbox now,
and none of it was ever specific to broadcasts.
"""
from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime, timezone
from dataclasses import replace

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from core.i18n import Texts, broadcast_texts
from bot import post
from bot.callbacks import BroadcastAction, BroadcastFilter, PromoClaim
from bot.analytics import track
from core.config import AppConfig
from core.domain import promo as promos
from core.domain.audience import STEPPED, WORD_LIMIT, Audience
from core.domain.quiet import SHOP_TZ
from core.ports.discounts import DiscountLookup, DiscountLookupFailed
from core.repos.campaigns import SqliteCampaignOutcomes
from core.usecases.analytics import (activity_report, campaign_report,
                                     usage_report)
from core.usecases.broadcast import start_broadcast
from core.repos.broadcast import SqliteBroadcastJournal
from core.repos.events import SqliteActivityLog, SqliteUsageStats
from core.repos.outbox import SqliteMessageQueue
from core.repos.users import (SqliteMailingList, get_broadcast_recipients,
                              get_user_language, opt_out_user)
from bot.keyboards import (broadcast_audience_kb, broadcast_name_kb,
                           broadcast_promo_doubt_kb, promo_shop_kb)
from bot.states import BroadcastStates
from bot.tasks import spawn

router = Router()


# --------------- Opt-out commands (/stop, /unsubscribe) ---------------


@router.message(Command("stop"))
@router.message(Command("unsubscribe"))
async def cmd_stop(message: Message, t: Texts) -> None:
    """Opt the user out of broadcast messages."""
    await opt_out_user(message.chat.id)
    track(message.chat.id, "opted_out")
    await message.answer(t.MSG_OPT_OUT_CONFIRM)


# --------------- Admin broadcast flow ---------------


def _is_admin(user_id: int, config: AppConfig) -> bool:
    return user_id in config.env.admin_ids


def _can_broadcast(user_id: int, config: AppConfig) -> bool:
    """Admins, and the staff trusted with this flow and nothing else."""
    return _is_admin(user_id, config) or user_id in config.env.broadcaster_ids


def _team(config: AppConfig) -> list[int]:
    """Who a test send goes to: the admins and the broadcast staff, once each."""
    return list(dict.fromkeys([*config.env.admin_ids, *config.env.broadcaster_ids]))


async def _flow_texts(user_id: int, config: AppConfig) -> Texts:
    return broadcast_texts(await get_user_language(user_id),
                           admin=_is_admin(user_id, config))


async def _queue_broadcast(text: str, admin_id: int,
                           content: dict | None = None,
                           audience: Audience | None = None,
                           name: str | None = None) -> None:
    """Hand the job to the queue and let the sender get on with it.

    Nothing is spawned any more. The messages are rows before this returns, and
    the outbox sender empties them at its own pace — which is what makes a
    redeploy in the middle of a broadcast a non-event instead of the reason
    resume_broadcasts existed.

    The three handles are built here for the same reason cmd_stats builds its
    store: there is no composition root yet, so the handler is where it is known
    which engine is underneath. When one exists, this line takes them as
    arguments like everything else.
    """
    started = await start_broadcast(
        text, admin_id,
        SqliteBroadcastJournal(), SqliteMailingList(), SqliteMessageQueue(),
        post=content, audience=audience,
    )
    logger.info("Broadcast job #{} «{}» queued for {} recipient(s)",
                started.job_id, name or "unnamed", started.queued)


@router.message(Command("broadcast"))
async def cmd_broadcast(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts,
) -> None:
    """Start the broadcast flow (admins and BROADCASTER_USER_IDS)."""
    if not _can_broadcast(message.from_user.id, config):
        return
    at = await _flow_texts(message.from_user.id, config)
    # A fresh start forgets the last one: a promo code that was waiting on
    # «use anyway» must not turn up under the next post.
    await state.set_data({})
    await state.set_state(BroadcastStates.waiting_message)
    await message.answer(at.MSG_BROADCAST_PROMPT)


# Parts of an album still arriving, by chat and media group. Telegram delivers
# an album as one update per part and never says which part is the last, so
# they are gathered here until none has arrived for _ALBUM_SETTLE_SECONDS. In
# memory on purpose: an album takes a second to arrive, and a restart inside
# that second loses a post the sender has not confirmed yet — they send it again.
_albums: dict[tuple[int, str], list[Message]] = {}
_album_last_part: dict[tuple[int, str], float] = {}
_ALBUM_SETTLE_SECONDS = 1.5


@router.message(BroadcastStates.waiting_message)
async def process_broadcast_message(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts,
) -> None:
    """Take the post, whatever it is, and show it back before asking."""
    if not _can_broadcast(message.from_user.id, config):
        return

    at = await _flow_texts(message.from_user.id, config)

    # **The one place an admin command can reach a customer.**
    #
    # This handler takes any message, and a command is a message like any other
    # line. Registration order then decides the outcome and is not on our side:
    # `/stop` and `/broadcast` sit above this handler and fire, while `/stats`
    # and `/chatid` sit below it and become the broadcast — so "/stats" goes out
    # to every subscriber, from the shop, signed by the shop.
    #
    # Checked here rather than by reordering the router, because reordering only
    # covers the commands that have handlers. A typo — "/statss", "/menü" — has
    # none, and would still be sent. Everything starting with a slash is
    # refused, and the state is kept so the real text can simply be typed next.
    if message.text and message.text.startswith("/"):
        await message.answer(at.MSG_BROADCAST_LOOKS_LIKE_A_COMMAND)
        return

    if message.media_group_id:
        key = (message.chat.id, message.media_group_id)
        first = key not in _albums
        _albums.setdefault(key, []).append(message)
        _album_last_part[key] = asyncio.get_running_loop().time()
        if first:
            spawn(_finish_album(key, message.bot, state, at, len(_team(config))),
                  name="broadcast_album")
        return

    await _offer(message.bot, message.chat.id, post.single(message), state, at,
                 len(_team(config)))


async def _finish_album(key: tuple[int, str], bot: Bot, state: FSMContext,
                        at: Texts, team: int) -> None:
    """Wait for the album to stop growing, then offer it as one post."""
    loop = asyncio.get_running_loop()
    while (wait := _album_last_part[key] + _ALBUM_SETTLE_SECONDS - loop.time()) > 0:
        await asyncio.sleep(wait)
    parts = _albums.pop(key)
    _album_last_part.pop(key, None)
    # The sender may have left the flow while the parts were arriving.
    if await state.get_state() != BroadcastStates.waiting_message.state:
        return
    await _offer(bot, key[0], post.album(parts), state, at, team)


async def _offer(bot: Bot, chat_id: int, content: dict, state: FSMContext,
                 at: Texts, team: int, *, kept: dict | None = None) -> None:
    """Send the sender exactly what recipients will get, then ask.

    The preview goes through the same function the outbox uses, so it is not a
    picture of the post but the post: if Telegram refuses it here, it would
    have refused it for everybody, and nothing has been queued yet.

    `kept` is the conversation so far when the post is a new version of one
    already taken: its filters stay, and its buttons and promo code move onto
    the new version. Without it the post starts from everybody and nothing.
    """
    audience = Audience()
    if kept is not None:
        audience = Audience.from_dict(kept.get("audience"))
        content, dropped = _carry_over(kept.get("broadcast_post") or {}, content)
        if dropped:
            await bot.send_message(chat_id, at.MSG_BROADCAST_BUTTONS_ALBUM)

    reason = post.problem(content)
    if reason:
        logger.info("Broadcast post refused: {}", reason)
        await bot.send_message(chat_id, at.MSG_BROADCAST_UNSUPPORTED)
        return

    recipients = await get_broadcast_recipients()
    if not recipients:
        await bot.send_message(chat_id, at.MSG_BROADCAST_NO_RECIPIENTS)
        await state.clear()
        return

    # Headings, a table or a quote typed as text go out as a rich message, if
    # Telegram reads the markup. If it does not, the post as the client sent
    # it is still a post: that one is shown instead, and what is shown is what
    # is stored.
    shown = False
    rich = post.from_markup(content)
    if rich:
        try:
            await post.send(bot, chat_id, rich, silent=False)
            content, shown = rich, True
        except TelegramBadRequest as exc:
            logger.warning("Broadcast markup refused by Telegram: {}", exc.message)
    if not shown:
        try:
            await post.send(bot, chat_id, content, silent=False)
        except TelegramBadRequest as exc:
            logger.warning("Broadcast preview refused by Telegram: {}", exc.message)
            await bot.send_message(chat_id, at.MSG_BROADCAST_PREVIEW_FAILED)
            return

    await state.update_data(broadcast_post=content,
                            broadcast_text=post.summary(content),
                            audience=audience.as_dict(), promo_pending=None)
    await state.set_state(BroadcastStates.waiting_confirm)
    text, markup = await _audience_screen(at, audience, team, content)
    await bot.send_message(chat_id, text, reply_markup=markup)


def _carry_over(taken: dict, new: dict) -> tuple[dict, bool]:
    """The new version with the old one's buttons and promo code on it.

    True beside it when there were some and they could not come: an album
    takes no keyboard, so the sender is told rather than finding out later.
    """
    had = bool(taken.get("buttons") or taken.get("promo"))
    if "album" in new:
        return new, had
    new = post.with_buttons(new, taken.get("buttons") or [])
    promo = taken.get("promo")
    if promo:
        new = post.with_promo(new, promo["code"], promo["text"])
    return new, False


def _with_edit(taken: dict, edited: Message) -> dict | None:
    """The post with the edited message in it, or None if it is not part of it."""
    stored = taken.get("message")
    if stored is not None:
        return post.single(edited) if stored.get("message_id") == edited.message_id else None
    parts = taken.get("album") or []
    if not any(part.get("message_id") == edited.message_id for part in parts):
        return None
    # One part of an album edited, a caption most likely: the rest stay.
    return {"album": [post.capture(edited) if part.get("message_id") == edited.message_id
                      else part for part in parts]}


# After the post is taken and before it goes: everywhere an edit of it should
# replace what was taken.
_POST_TAKEN = StateFilter(
    BroadcastStates.waiting_confirm, BroadcastStates.waiting_buttons,
    BroadcastStates.waiting_promo, BroadcastStates.waiting_word,
    BroadcastStates.waiting_name)


@router.edited_message(_POST_TAKEN)
async def process_broadcast_edit(
    message: Message, config: AppConfig, state: FSMContext,
) -> None:
    """The sender fixed the post in Telegram: take the new version, keep the rest.

    Before this an edit changed nothing: the bot had taken the post when it
    arrived, so the preview and the broadcast went out as first typed while
    the sender's own chat showed the fix. The only way to correct a typo was
    to start /broadcast over and set the filters again.

    Whatever was being asked is dropped and the picker comes back under the
    new preview: a name typed after an edit must answer for the post as it now
    is, so the question is asked again. No `t`: the language middleware does
    not run for edits, and the flow picks its own language anyway.
    """
    if not _can_broadcast(message.from_user.id, config):
        return
    data = await state.get_data()
    replaced = _with_edit(data.get("broadcast_post") or {}, message)
    if replaced is None:
        return
    at = await _flow_texts(message.from_user.id, config)
    await message.answer(at.MSG_BROADCAST_POST_UPDATED)
    await _offer(message.bot, message.chat.id, replaced, state, at,
                 len(_team(config)), kept=data)


async def _audience_screen(at: Texts, audience: Audience, team: int,
                           content: dict | None = None):
    """The picker as it stands: how many the filters leave, out of how many.

    Counted afresh on every tap rather than cached in the state, so the number
    on the send button is the number that will be queued — an opt-out or a new
    order a minute ago is already in it.
    """
    mailing = SqliteMailingList()
    total = len(await mailing.recipients())
    count = total if audience.everyone else len(await mailing.recipients(audience))
    text = (at.MSG_BROADCAST_AUDIENCE.format(count=count, total=total) if count
            else at.MSG_BROADCAST_AUDIENCE_EMPTY.format(total=total))
    return text, broadcast_audience_kb(at, audience, count, team=team,
                                       buttons=post.button_count(content),
                                       promo=post.promo_of(content))


async def _redraw(callback: CallbackQuery, at: Texts, audience: Audience,
                  team: int, content: dict | None = None) -> None:
    text, markup = await _audience_screen(at, audience, team, content)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        # A stale screen tapped twice asks for what is already shown.
        if "not modified" not in exc.message.lower():
            raise


# The name a sender types to send. Its whole job is to be typed on purpose, so
# it is held to a shape nobody produces by accident, and kept with the job.
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{2,39}")
# What a person types to get out, not to name a broadcast. Several of them are
# perfectly good names by shape, «stop» and «cancel» among them, and taking one
# for a name would send the broadcast to everybody: so they go back instead.
_BACK_WORDS = {"-", "–", "—", "back", "cancel", "stop", "no", "nope", "exit",
               "назад", "ні", "нет", "стоп", "скасувати", "відміна", "отмена"}


async def _send_now(data: dict, sender_id: int, name: str | None = None) -> None:
    # The journal row keeps the name in front of the post's words: nothing
    # else in it can say which job was «nov-2026-sale» once the log is gone.
    text = f"[{name}] {data['broadcast_text']}" if name else data["broadcast_text"]
    await _queue_broadcast(text, sender_id,
                           data.get("broadcast_post"),
                           Audience.from_dict(data.get("audience")), name)


@router.callback_query(BroadcastStates.waiting_confirm, BroadcastFilter.filter())
async def process_broadcast_filter(
    callback: CallbackQuery,
    callback_data: BroadcastFilter,
    config: AppConfig,
    state: FSMContext,
    t: Texts,
) -> None:
    """One tap on a filter: move it to its next choice, or ask for a word."""
    await callback.answer()
    if not _can_broadcast(callback.from_user.id, config):
        return
    at = await _flow_texts(callback.from_user.id, config)
    data = await state.get_data()
    audience = Audience.from_dict(data.get("audience"))

    name = callback_data.name
    if name in STEPPED:
        audience = audience.stepped(name)
    elif name == "bought" and not audience.bought:
        await _ask_word(callback, state, "bought", at.MSG_BROADCAST_ASK_BOUGHT)
        return
    elif name == "bought":
        audience = replace(audience, bought="")
    elif name == "waiting" and audience.waiting == "any":
        audience = replace(audience, waiting="yes")
    elif name == "waiting" and not audience.waiting_for:
        await _ask_word(callback, state, "waiting_for", at.MSG_BROADCAST_ASK_WAITING)
        return
    elif name == "waiting":
        audience = replace(audience, waiting="any", waiting_for="")
    else:
        return

    await state.update_data(audience=audience.as_dict())
    await _redraw(callback, at, audience, len(_team(config)),
                  data.get("broadcast_post"))


async def _ask_word(callback: CallbackQuery, state: FSMContext, field: str,
                    prompt: str) -> None:
    """Turn the picker into the question; the answer brings a fresh picker.

    The screen itself becomes the prompt rather than a second message under it,
    so there is never a live picker above a question it is waiting on.
    """
    await state.update_data(word_for=field)
    await state.set_state(BroadcastStates.waiting_word)
    await callback.message.edit_text(prompt)


@router.message(BroadcastStates.waiting_word)
async def process_broadcast_word(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts,
) -> None:
    """The word for a product filter, then back to the picker."""
    if not _can_broadcast(message.from_user.id, config):
        return
    at = await _flow_texts(message.from_user.id, config)
    data = await state.get_data()
    field = data.get("word_for")
    prompt = (at.MSG_BROADCAST_ASK_BOUGHT if field == "bought"
              else at.MSG_BROADCAST_ASK_WAITING)

    word = (message.text or "").strip()
    if not word or word.startswith("/"):
        await message.answer(prompt)
        return

    audience = Audience.from_dict(data.get("audience"))
    if field == "bought":
        audience = replace(audience, bought=word[:WORD_LIMIT])
    else:
        audience = replace(audience, waiting="yes", waiting_for=word[:WORD_LIMIT])
    await state.update_data(audience=audience.as_dict())
    await state.set_state(BroadcastStates.waiting_confirm)
    text, markup = await _audience_screen(at, audience, len(_team(config)),
                                          data.get("broadcast_post"))
    await message.answer(text, reply_markup=markup)


@router.callback_query(BroadcastStates.waiting_confirm, BroadcastAction.filter())
async def process_broadcast_confirm(
    callback: CallbackQuery,
    callback_data: BroadcastAction,
    config: AppConfig,
    state: FSMContext,
    t: Texts,
) -> None:
    """Send to whoever the filters leave, add buttons, test on the team,
    reset, or cancel."""
    if not _can_broadcast(callback.from_user.id, config):
        await callback.answer()
        return
    at = await _flow_texts(callback.from_user.id, config)
    if callback_data.action in ("buttons", "promo"):
        # Answers the tap itself: on an album the answer is the explanation.
        await _ask_for_post_buttons(callback, state, at, callback_data.action)
        return
    await callback.answer()

    team = _team(config)
    data = await state.get_data()
    stored = data.get("broadcast_post")
    if callback_data.action == "test":
        await _send_test(callback, state, at, team)
        return
    if callback_data.action == "reset":
        await state.update_data(audience=Audience().as_dict())
        await _redraw(callback, at, Audience(), len(team), stored)
        return

    if callback_data.action == "cancel":
        await state.clear()
        await callback.message.edit_text(at.MSG_BROADCAST_CANCELLED)
        return
    if callback_data.action != "send":
        # A button from another screen still in the chat, the name question's
        # «back» or a promo warning's «use anyway», tapped after its moment
        # passed. Ignored: read as «cancel», it threw the whole post away.
        return

    if not data.get("broadcast_text"):
        await state.clear()
        await callback.message.edit_text(at.MSG_BROADCAST_CANCELLED)
        return
    audience = Audience.from_dict(data.get("audience"))
    recipients = await SqliteMailingList().recipients(audience)
    if not recipients:
        # A stale send button: since the screen was drawn, the last person
        # these filters matched opted out or stopped matching.
        await _redraw(callback, at, audience, len(team), stored)
        return

    # The button alone sends nothing. A tap can be a slip of the thumb; typing
    # a name cannot, so the name is what sends (process_broadcast_name).
    await state.set_state(BroadcastStates.waiting_name)
    await callback.message.edit_text(
        at.MSG_BROADCAST_ASK_NAME.format(count=len(recipients)),
        reply_markup=broadcast_name_kb(at))


async def _send_test(callback: CallbackQuery, state: FSMContext, at: Texts,
                     team: list[int]) -> None:
    """The post to the admins and the broadcast staff only, then the picker again.

    For seeing the post land as a customer will — in the chat list, with its
    notification, on another person's phone — before choosing who gets it.
    Sent straight away rather than queued: it is a handful of chats, the sender
    is waiting for the answer, and a test is not a campaign, so it stays out of
    the journal and the campaign reports.

    The picker it was tapped on becomes the report, and a fresh picker comes
    after the copies, so the next tap is at the bottom of the chat rather than
    above the sender's own copy.
    """
    data = await state.get_data()
    content = data.get("broadcast_post")
    if not content:
        await state.clear()
        await callback.message.edit_text(at.MSG_BROADCAST_CANCELLED)
        return
    # The keyboard goes first: a second tap while the copies are going out
    # would send them all again.
    await callback.message.edit_text(at.MSG_BROADCAST_TEST_SENDING)
    sent = 0
    for chat_id in team:
        try:
            await post.send(callback.bot, chat_id, content, silent=False)
            sent += 1
        except TelegramAPIError as exc:
            # Mostly someone who never pressed /start: Telegram lets a bot
            # write only to those who have.
            logger.info("Broadcast test not delivered to {}: {}", chat_id, exc.message)
    logger.info("Broadcast test sent to {} of {} team member(s)", sent, len(team))
    report = at.MSG_BROADCAST_TEST_SENT.format(sent=sent, total=len(team))
    if sent < len(team):
        report += "\n" + at.MSG_BROADCAST_TEST_MISSED
    await callback.message.edit_text(report)
    text, markup = await _audience_screen(
        at, Audience.from_dict(data.get("audience")), len(team), content)
    # The same picker read, to the owner, as the bot offering to send again.
    # It is the real send still waiting; said in so many words, and with the
    # way out for a sender whose test was the whole point.
    text = f"{at.MSG_BROADCAST_AFTER_TEST}\n\n{text}"
    await callback.bot.send_message(callback.message.chat.id, text,
                                    reply_markup=markup)


def _buttons_complaint(at: Texts, exc: post.ButtonsError) -> str:
    """What was wrong with the typed buttons, in a form that can be fixed.

    A link without words gets the fix written out with that very link, in
    <code> so a tap copies it. Every piece the sender typed is escaped: this
    goes out under the bot's HTML parse mode.
    """
    if exc.line is None:
        return at.MSG_BROADCAST_BUTTONS_TOO_MANY.format(
            rows=post.BUTTON_ROWS,
            per_row=post.BUTTONS_PER_ROW)
    if exc.url:
        example = f"{at.BROADCAST_BUTTON_EXAMPLE_LABEL} - {exc.url}"
        return at.MSG_BROADCAST_BUTTONS_NEED_TEXT.format(
            line=html.escape(exc.line), example=html.escape(example))
    return at.MSG_BROADCAST_BUTTONS_INVALID.format(line=html.escape(exc.line))


async def _ask_for_post_buttons(callback: CallbackQuery, state: FSMContext,
                                at: Texts, which: str) -> None:
    """Turn the picker into the question about link buttons or a promo code.

    The same shape as a word filter: the screen becomes the prompt, and the
    answer brings the post back with its buttons and a fresh picker under it.
    """
    content = (await state.get_data()).get("broadcast_post") or {}
    if "album" in content:
        await callback.answer(at.MSG_BROADCAST_BUTTONS_ALBUM, show_alert=True)
        return
    await callback.answer()
    if which == "promo":
        await state.set_state(BroadcastStates.waiting_promo)
        await callback.message.edit_text(at.MSG_BROADCAST_ASK_PROMO)
    else:
        await state.set_state(BroadcastStates.waiting_buttons)
        await callback.message.edit_text(at.MSG_BROADCAST_ASK_BUTTONS)


async def _show_post_again(message: Message, state: FSMContext, at: Texts,
                           config: AppConfig, data: dict, content: dict) -> bool:
    """Send the changed post back to its sender, then a fresh picker.

    Shown rather than described: a button Telegram will not take fails here,
    for the sender, instead of in every recipient's row. False when it failed,
    and then nothing is stored.
    """
    reason = post.problem(content)
    if reason is None:
        try:
            await post.send(message.bot, message.chat.id, content, silent=False)
        except TelegramBadRequest as exc:
            reason = exc.message
    if reason:
        logger.warning("Broadcast buttons refused: {}", reason)
        await message.answer(at.MSG_BROADCAST_BUTTONS_REFUSED)
        return False
    await state.update_data(broadcast_post=content)
    await state.set_state(BroadcastStates.waiting_confirm)
    text, markup = await _audience_screen(
        at, Audience.from_dict(data.get("audience")), len(_team(config)), content)
    await message.answer(text, reply_markup=markup)
    return True


@router.message(BroadcastStates.waiting_buttons)
async def process_broadcast_buttons(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts,
) -> None:
    """The typed buttons, or «-» for none; then the post again, as it will go."""
    if not _can_broadcast(message.from_user.id, config):
        return
    at = await _flow_texts(message.from_user.id, config)
    typed = (message.text or "").strip()
    if not typed or typed.startswith("/"):
        await message.answer(at.MSG_BROADCAST_ASK_BUTTONS)
        return
    data = await state.get_data()
    content = data.get("broadcast_post")
    if not content:
        await state.clear()
        await message.answer(at.MSG_BROADCAST_CANCELLED)
        return
    try:
        rows = [] if typed in ("-", "–", "—") else post.parse_buttons(typed)
    except post.ButtonsError as exc:
        await message.answer(_buttons_complaint(at, exc))
        return
    await _show_post_again(message, state, at, config, data,
                           post.with_buttons(content, rows))


@router.message(BroadcastStates.waiting_promo)
async def process_broadcast_promo(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts, promo_lookup: DiscountLookup | None = None,
) -> None:
    """The promo code for the post's «🎁 Забрати промокод» button, or «-».

    The code must already exist in the shop's admin: the bot hands it out and
    builds the link that applies it, and creates nothing. With a token it is
    looked up first (core/domain/promo.py); without one, it is taken as typed.
    """
    if not _can_broadcast(message.from_user.id, config):
        return
    at = await _flow_texts(message.from_user.id, config)
    typed = (message.text or "").strip()
    if not typed or typed.startswith("/"):
        await message.answer(at.MSG_BROADCAST_ASK_PROMO)
        return
    data = await state.get_data()
    content = data.get("broadcast_post")
    if not content:
        await state.clear()
        await message.answer(at.MSG_BROADCAST_CANCELLED)
        return
    if typed in ("-", "–", "—"):
        await _show_post_again(message, state, at, config, data,
                               post.with_promo(content, None))
        return
    code = post.promo_code(typed)
    if not code:
        # The sender's own words, under the bot's HTML parse mode.
        await message.answer(at.MSG_BROADCAST_PROMO_INVALID.format(
            code=html.escape(typed[:60])))
        return
    if promo_lookup is None:
        await _show_post_again(message, state, at, config, data,
                               _with_claim(content, code))
        return
    try:
        found = await promo_lookup.find(code)
        outcome = promos.verdict(found, datetime.now(timezone.utc))
    except DiscountLookupFailed as exc:
        logger.warning("Promo code {} not checked: {}", code, exc)
        found, outcome = None, "unchecked"
    if outcome == promos.OK:
        await state.update_data(promo_pending=None)
        await message.answer(at.MSG_BROADCAST_PROMO_FOUND.format(
            code=html.escape(code), details=_promo_details(at, found)))
        await _show_post_again(message, state, at, config, data,
                               _with_claim(content, code))
        return
    # Advice, not a gate: the code may be created a minute from now, and the
    # shop may simply not have answered. The sender decides.
    await state.update_data(promo_pending=code)
    await message.answer(_promo_doubt(at, code, outcome, found),
                         reply_markup=broadcast_promo_doubt_kb(at))


@router.callback_query(BroadcastStates.waiting_promo, BroadcastAction.filter())
async def process_broadcast_promo_choice(
    callback: CallbackQuery,
    callback_data: BroadcastAction,
    config: AppConfig,
    state: FSMContext,
    t: Texts,
) -> None:
    """After a warning about the code: use it anyway, or type another."""
    await callback.answer()
    if not _can_broadcast(callback.from_user.id, config):
        return
    at = await _flow_texts(callback.from_user.id, config)
    data = await state.get_data()
    code, content = data.get("promo_pending"), data.get("broadcast_post")
    if callback_data.action == "promo_use" and code and content:
        await state.update_data(promo_pending=None)
        await callback.message.edit_reply_markup(reply_markup=None)
        await _show_post_again(callback.message, state, at, config, data,
                               _with_claim(content, code))
    elif callback_data.action == "promo_retry":
        await state.update_data(promo_pending=None)
        await callback.message.edit_text(at.MSG_BROADCAST_ASK_PROMO)


def _with_claim(content: dict, code: str) -> dict:
    # The label is the customer's, so it is Ukrainian whoever is composing.
    return post.with_promo(content, code, Texts("uk").BTN_PROMO_CLAIM)


def _day(moment: datetime) -> str:
    return moment.astimezone(SHOP_TZ).strftime("%d.%m.%Y")


def _promo_details(at: Texts, found: promos.Promo) -> str:
    """«−10%, до 15.11.2026, використано 0 з 1»: enough to recognise the code."""
    parts = []
    if found.percent is not None:
        parts.append(f"−{found.percent:g}%")
    elif found.amount:
        parts.append(f"−{found.amount}")
    parts.append(at.PROMO_UNTIL.format(date=_day(found.ends_at)) if found.ends_at
                 else at.PROMO_NO_END)
    if found.usage_limit is not None:
        parts.append(at.PROMO_USED.format(used=found.used or 0, limit=found.usage_limit))
    return ", ".join(parts)


def _promo_doubt(at: Texts, code: str, outcome: str,
                 found: promos.Promo | None) -> str:
    """What is wrong with the code, as far as the shop said."""
    code = html.escape(code)
    if outcome == promos.MISSING:
        return at.MSG_BROADCAST_PROMO_MISSING.format(code=code)
    if outcome == promos.EXPIRED and found and found.ends_at:
        return at.MSG_BROADCAST_PROMO_EXPIRED.format(code=code, date=_day(found.ends_at))
    if outcome == promos.SCHEDULED and found and found.starts_at:
        return at.MSG_BROADCAST_PROMO_SCHEDULED.format(code=code,
                                                       date=_day(found.starts_at))
    if outcome == promos.USED_UP and found:
        return at.MSG_BROADCAST_PROMO_USED_UP.format(
            code=code, used=found.used, limit=found.usage_limit)
    if outcome == "unchecked":
        return at.MSG_BROADCAST_PROMO_UNCHECKED.format(code=code)
    return at.MSG_BROADCAST_PROMO_INACTIVE.format(code=code)


@router.callback_query(PromoClaim.filter())
async def claim_promo(
    callback: CallbackQuery, callback_data: PromoClaim, config: AppConfig,
    t: Texts,
) -> None:
    """A customer tapped «🎁 Забрати промокод»: the code, and the shop with it.

    In any state and for anybody: the button is on a post that went to
    customers. The code goes out as a message rather than a pop-up, because a
    pop-up cannot be copied, and under it the link that applies the code by
    itself (bot/keyboards.py::discount_url).
    """
    await callback.answer()
    code = post.promo_code(callback_data.code)
    if not code:
        return
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    track(chat_id, "promo_claimed", code=code)
    await callback.bot.send_message(
        chat_id, t.MSG_PROMO_CODE.format(code=html.escape(code)),
        reply_markup=promo_shop_kb(t, config.website_url, code))


@router.callback_query(BroadcastStates.waiting_name, BroadcastAction.filter())
async def process_broadcast_name_action(
    callback: CallbackQuery,
    callback_data: BroadcastAction,
    config: AppConfig,
    state: FSMContext,
    t: Texts,
) -> None:
    """Back to the picker, or cancel, from the question about the name.

    Anything else is a button from an older screen still in the chat, and is
    ignored: a stale «Send» must not count as an answer, and a stale anything
    must not cancel what is on screen now.
    """
    await callback.answer()
    if not _can_broadcast(callback.from_user.id, config):
        return
    at = await _flow_texts(callback.from_user.id, config)
    if callback_data.action == "back":
        data = await state.get_data()
        await state.set_state(BroadcastStates.waiting_confirm)
        await _redraw(callback, at, Audience.from_dict(data.get("audience")),
                      len(_team(config)), data.get("broadcast_post"))
    elif callback_data.action == "cancel":
        await state.clear()
        await callback.message.edit_text(at.MSG_BROADCAST_CANCELLED)


@router.message(BroadcastStates.waiting_name)
async def process_broadcast_name(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts,
) -> None:
    """The typed name, and only then the send."""
    if not _can_broadcast(message.from_user.id, config):
        return
    at = await _flow_texts(message.from_user.id, config)
    typed = (message.text or "").strip()
    data = await state.get_data()
    audience = Audience.from_dict(data.get("audience"))
    team, stored = len(_team(config)), data.get("broadcast_post")

    if typed.casefold() in _BACK_WORDS:
        await state.set_state(BroadcastStates.waiting_confirm)
        text, markup = await _audience_screen(at, audience, team, stored)
        await message.answer(text, reply_markup=markup)
        return
    name = typed.lower()
    if not _NAME.fullmatch(name):
        # The sender's own words, under the bot's HTML parse mode.
        await message.answer(at.MSG_BROADCAST_NAME_INVALID.format(
            name=html.escape(typed[:60] or "…")))
        return
    if not data.get("broadcast_text"):
        await state.clear()
        await message.answer(at.MSG_BROADCAST_CANCELLED)
        return
    if not await SqliteMailingList().recipients(audience):
        # Everybody these filters matched left while the name was being typed.
        await state.set_state(BroadcastStates.waiting_confirm)
        text, markup = await _audience_screen(at, audience, team, stored)
        await message.answer(text, reply_markup=markup)
        return

    await state.clear()
    await message.answer(at.MSG_BROADCAST_STARTED_NAMED.format(name=name))
    await _send_now(data, message.from_user.id, name)


@router.message(BroadcastStates.waiting_confirm, F.text)
async def process_broadcast_confirm_text(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts,
) -> None:
    """Typing on the picker sends nothing and cancels nothing.

    «так» used to send from here, which made the name step a formality with a
    side door. Cancelling on any other text was the old yes/no prompt's way out;
    the picker has its own button for that, and a name typed one step early
    would have thrown the whole post away.
    """
    if not _can_broadcast(message.from_user.id, config):
        return
    at = await _flow_texts(message.from_user.id, config)
    await message.answer(at.MSG_BROADCAST_USE_SEND)


# --------------- Admin utilities ---------------


@router.message(Command("chatid"))
async def cmd_chatid(message: Message, config: AppConfig) -> None:
    """Report this chat's id, for filling in support_chat_id.

    Works in a group as well as a DM. In a group the bot only receives commands
    addressed to it while privacy mode is on, so use /chatid@<botname> there.
    """
    if message.from_user.id not in config.env.admin_ids:
        return

    chat = message.chat
    current = config.support_chat_id
    lines = [
        f"Chat id: <code>{chat.id}</code>",
        f"Type: {chat.type}" + (f" · {chat.title}" if chat.title else ""),
        "",
        f"support_chat_id in config.yaml is currently {current}"
        + (" — this chat." if chat.id == current else "."),
    ]
    if chat.id != current and chat.type != "private":
        lines += ["", "To route support here, set support_chat_id to the id above."]
    await message.answer("\n".join(lines), parse_mode="HTML")


# --------------- Admin analytics ---------------


@router.message(Command("stats"))
async def cmd_stats(message: Message, config: AppConfig) -> None:
    """Summarise the instrumentation so the numbers are readable from the bot.

    Admin only, and in English: this is an operational readout, not customer
    copy, and it sits alongside logs and the runbook, which are English too.
    """
    if not _is_admin(message.from_user.id, config):
        return

    # Constructed here because there is no composition root yet: handlers still
    # import repositories (the handlers-see-ports-only contract is the one still
    # commented out in .importlinter). When one exists, this moves into it and
    # this line takes the store as an argument like everything else.
    report = await usage_report(SqliteUsageStats())

    lines = [f"\U0001f4ca <b>Last {report.days} days</b>", "",
             "<b>Funnel (people new in the window)</b>"]
    labels = {
        "start": "/start",
        "contact_shared": "shared contact",
        "registered": "registered",
        "orders_viewed": "viewed orders",
    }
    for step in report.funnel:
        share = f"  {step.share:.0f}%" if step.share is not None else ""
        lines.append(f"  {labels.get(step.key, step.key)}: {step.users}{share}")

    # The same function and windows as the chart in the analytics service, so
    # the two readouts cannot disagree. Kyiv days and no admins, unlike the
    # sections around it, which count UTC windows and everybody.
    activity = await activity_report(SqliteActivityLog(), days=7,
                                     exclude=config.env.admin_ids)
    lines += ["", "<b>Activity (Kyiv days, admins excluded)</b>",
              f"  users: {activity.users_total}, registered: "
              f"{activity.registered_total}",
              f"  registered, last 7 days: {activity.registered_7d} "
              f"(the 7 before: {activity.registered_prev_7d})",
              f"  active: {activity.wau} in {activity.wau_days} days, "
              f"{activity.mau} in {activity.mau_days} days"]

    lines += ["", "<b>Order lookups</b>"]
    if report.miss_rate is not None:
        lines.append(
            f"  found nothing: {report.lookups_without_orders} of {report.lookups} "
            f"({report.miss_rate:.0f}%)"
        )
        lines.append("  ^ Telegram phone did not match the one in the CRM")
    else:
        lines.append("  no lookups yet")

    lines += ["", "<b>Retention</b>",
              f"  active: {report.active_users}, of them on more than one day: "
              f"{report.returning_users}"]

    lines += ["", f"<b>Events, last {report.event_days} days</b>"]
    if report.events:
        for event, total, users in report.events:
            lines.append(f"  {event}: {total} ({users} users)")
    else:
        lines.append("  no events yet")

    campaigns = await campaign_report(SqliteCampaignOutcomes())
    lines += ["", f"<b>Proactive, last {campaigns.days} days</b>"]
    if campaigns.rows:
        lines.append(f"  <i>ordered within {campaigns.window_days}d of the "
                     f"message, against the same window before it</i>")
        for row in campaigns.rows:
            after = (f"{row.bought}/{row.people}"
                     + (f" ({row.share:.0f}%)" if row.share is not None else ""))
            if row.bought_before is None:
                before = "—"
            else:
                before = str(row.bought_before) + (
                    f" ({row.share_before:.0f}%)"
                    if row.share_before is not None else "")
            failed = f", {row.failed} undelivered" if row.failed else ""
            lines.append(f"  {row.key} ({row.type}): {after} after, "
                         f"{before} before{failed}")
        if campaigns.unreadable:
            lines.append(f"  {campaigns.unreadable} row(s) carried something "
                         f"that is not a campaign key — see the log")
    else:
        lines.append("  nothing sent yet")

    lines += ["", "Taps on the Website button are not reported by Telegram — "
              "look for utm_source=telegram in the shop's analytics.",
              "The same is true of every button above: no proactive message "
              "carries one this bot can see a tap on, so there is no click "
              "column and a \"—\" means unmeasurable, never zero.",
              "\"before\" is the ordinary rate for the same people, not a "
              "control group: they were chosen by the campaign, not at random."]

    await message.answer("\n".join(lines), parse_mode="HTML")
