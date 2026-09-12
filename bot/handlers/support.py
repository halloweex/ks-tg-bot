"""Support relay — user-to-admin forwarding and admin-to-user reply."""
from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from core.domain.quiet import within_hours
from core.i18n import Texts, operator_texts
from bot.alerts import tell_admins_once
from bot.analytics import track
from bot.customer import describe
from core.config import AppConfig
from core.repos.outbox import SqliteMessageQueue
from core.repos.support import (album_in_progress, current_focus,
                                forget_focus, mark_discount_answered,
                                remember_focus, remember_support_thread,
                                start_album,
                                support_thread_owner)
from core.usecases.support import queue_reply
from bot.screen import seen
from bot.states import SupportStates

router = Router()


def support_prompt(t: Texts, config: AppConfig,
                   now: datetime | None = None) -> tuple[str, bool]:
    """What to show when support opens, and whether it named the hour.

    The pair to `forwarded_confirmation`, and deliberately shaped the same way:
    the two are one decision made twice, and letting them drift is how a
    customer gets told about opening hours once, twice, or never depending on
    which door she came through.

    The bool is what stops the second telling. If the prompt already said
    «будемо на зв'язку з 09:00», the confirmation a minute later says the
    ordinary line instead — two "nobody is here" messages in one minute is
    worse than one.
    """
    window = config.support_window
    if window is None or within_hours(*window, now=now):
        return t.MSG_SUPPORT_PROMPT, False
    return (t.MSG_SUPPORT_PROMPT_OFF_HOURS.format(
        time=window[0].strftime("%H:%M")), True)


async def begin_support(state: FSMContext, t: Texts, config: AppConfig) -> str:
    """Put her in the support state and return what she should read.

    One function because there are **five** doors into this screen — the key
    under the input field, the ⚙ in the message menu, the button on the "no
    orders found" screen, and the two escapes from a phone that would not
    parse. A screen whose entrances disagree is the defect this bot has now
    paid for three times, most recently when two of seven screens were rich and
    the plain ones silently destroyed them.

    `hours_named` is written on every pass, never only when true: a stale flag
    from an earlier conversation would silence the hour for a customer who was
    never told it.
    """
    text, named = support_prompt(t, config)
    await state.set_state(SupportStates.waiting_message)
    await state.update_data(hours_named=named)
    return text


def relay_destinations(config: AppConfig, writer_chat_id: int) -> list[int]:
    """Every chat a customer's support message goes to, in order, deduplicated.

    The manager first, because that is whose job it is, then the admins — the
    owner asked to see support as well as the manager, and until now it went to
    the manager alone. An id appearing in both lists is sent to once.

    The writer's own chat is skipped. An admin writing to support would
    otherwise be forwarded their own message back, which reads as a bug and
    puts a thread row on a message they are looking at from the wrong side.
    """
    seen = dict.fromkeys([config.support_chat_id, *config.env.admin_ids])
    return [chat for chat in seen if chat != writer_chat_id]


async def _relay_to(bot, destination: int, message: Message, op,
                    *, with_note: bool) -> list[int]:
    """One customer message into one chat. Returns the ids it put there.

    Raises if the chat cannot be written to, so the caller can drop that one
    destination and keep the others: before this there was only ever one, and
    its failure was the whole relay failing.
    """
    ids: list[int] = []
    if with_note:
        # The metadata line, carrying the chat_id as a privacy-safe identifier.
        note = await bot.send_message(
            chat_id=destination,
            text=op.MSG_SUPPORT_ADMIN_NOTE.format(
                who=await describe(message.from_user, message.chat.id)),
            parse_mode="HTML",
        )
        ids.append(note.message_id)

    # forward_message carries whatever the customer sent — photo, voice, video
    # note, document — so attachments arrive unchanged in this direction.
    forwarded = await bot.forward_message(
        chat_id=destination,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )
    ids.append(forwarded.message_id)

    if with_note:
        instruction = await bot.send_message(
            chat_id=destination, text=op.MSG_SUPPORT_REPLY_INSTRUCTION)
        ids.append(instruction.message_id)
    return ids


def forwarded_confirmation(t: Texts, config: AppConfig,
                           now: datetime | None = None,
                           hours_named: bool = False) -> str:
    """What the customer is told once their message is on its way.

    Inside working hours the old line stands — «відповімо тут» is true and
    soon. Outside them it is a promise nobody is awake to keep, so the message
    names the hour instead. Configuring no hours keeps the old behaviour: the
    bot would rather say nothing about timing than invent a time.

    `hours_named` says the prompt already told her, in which case this does not
    tell her again. It defaults to False so every existing caller and every
    existing test keeps the behaviour it had.
    """
    window = config.support_window
    if hours_named or window is None or within_hours(*window, now=now):
        return t.MSG_SUPPORT_FORWARDED
    return t.MSG_SUPPORT_FORWARDED_OFF_HOURS.format(
        time=window[0].strftime("%H:%M"))


@router.message(SupportStates.waiting_message)
async def forward_to_support(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    t: Texts,
) -> None:
    """Forward user's message to admin chat with metadata.

    An album is several messages sharing a media_group_id, and Telegram gives no
    signal for the last one. Only the first claims the album; the rest are
    forwarded into the same thread without repeating the metadata line, the
    instruction or the confirmation to the customer. Before this, the state was
    cleared by the first part and every later photo matched no handler at all,
    so a customer sending three photos had two of them disappear.
    """
    bot = message.bot
    album = message.media_group_id
    first_of_album = True
    if album:
        first_of_album = await start_album(message.chat.id, album)

    # These two go to the support chat, not to the customer. `t` is the
    # customer's language — using it here made the managers' own note and
    # instructions change language depending on who happened to write in.
    op = operator_texts()

    delivered = 0
    for destination in relay_destinations(config, message.chat.id):
        try:
            ids = await _relay_to(bot, destination, message, op,
                                  with_note=first_of_album)
        except TelegramAPIError as exc:
            logger.error("Support relay to chat {} failed: {}", destination, exc)
            await tell_admins_once(
                bot, config.env.admin_ids, f"support_relay:{destination}",
                f"Support relay to {destination} is broken: {exc}\n\n"
                f"A bot cannot write to a user who has never opened it — that "
                f"account must press Start, or the id must name a group the bot "
                f"is in. Other destinations are unaffected.",
            )
            continue
        delivered += 1
        # **A new request ends the no-reply mode in that chat.**
        #
        # This is the whole safety of the feature. She is answering one person
        # without replying to anything; a second request arrives; her next line
        # was written for whichever of the two she was reading. The bot cannot
        # know which, and guessing sends a stranger somebody else's answer.
        #
        # Only when the focus is on somebody ELSE: a second message from the
        # same customer mid-conversation is the conversation, not a new one.
        focused = await current_focus(destination)
        if focused is not None and focused != message.chat.id:
            await forget_focus(destination)
            try:
                await bot.send_message(destination,
                                       op.MSG_SUPPORT_FOCUS_OFF)
            except TelegramAPIError:
                # The relay itself got through; a notice about it is not worth
                # failing the delivery that just succeeded.
                logger.debug("Could not announce the focus reset to {}",
                             destination)
        # Registered per destination, because the id is only half the identity:
        # see core/repos/support.py. A reply in ANY of these chats reaches the
        # customer, which is the point of sending to more than one.
        await remember_support_thread(ids, message.chat.id, destination)

    if not delivered:
        # Nobody got it. The state is deliberately left alone: whatever she
        # sends next is still a support message, so a retry is one tap and not
        # a new flow.
        await message.answer(t.MSG_SUPPORT_NOT_DELIVERED)
        return

    if not first_of_album:
        # A later part of an album: already confirmed, state already cleared.
        return

    # Confirm to user and return to main menu.
    #
    # Read BEFORE the clear, which takes the FSM data with it. Whether the
    # prompt already named the opening hour decides what the confirmation says,
    # and one line later that fact is gone.
    hours_named = bool((await state.get_data()).get("hours_named"))
    await state.clear()
    track(message.chat.id, "support_message_sent")
    await seen(message)
    # **It used to take itself back after 45 seconds**, on the reasoning that a
    # line answering a tap is noise a day later and the 👀 on her own message is
    # the durable half.
    #
    # Watched from her side, that reasoning is wrong. She writes out a problem,
    # reads «твоє повідомлення вже у нас», and a minute later the only words the
    # shop said to her are gone — while the answer, when it comes, comes from
    # somewhere else entirely. What looks tidy from the code looks like being
    # ignored from the chat.
    #
    # It stays. No keyboard to attach: the menu is already under the input field.
    await message.answer(
        forwarded_confirmation(t, config, hours_named=hours_named))


@router.message(StateFilter(None), F.media_group_id)
async def forward_album_tail(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    t: Texts,
) -> None:
    """Later parts of an album whose first part already cleared the state.

    Without this they match nothing: the state filter above no longer applies
    and there is no other handler for a photo. Scoped to an album this chat is
    already sending, so an unrelated album sent later is not silently forwarded
    to support.
    """
    if message.chat.id == config.support_chat_id:
        return
    if not await album_in_progress(message.chat.id, message.media_group_id):
        return
    await forward_to_support(message, state, config, t)


@router.message(StateFilter(None), ~F.reply_to_message, ~F.contact)
async def answer_without_a_reply(message: Message, config: AppConfig) -> None:
    """A bare line in a chat that is already answering somebody.

    **Why this exists.** Every answer used to need a reply-to-message, and a
    consultation is ten of them. That is why the relay went unused for weeks:
    the manager read the forward and moved to her own chat, where typing is just
    typing — and the customer, who had been told «відповімо тут», got her answer
    from a different name in a different chat.

    **Why it is narrow.** It routes only where a reply has already been made
    within FOCUS_MINUTES, and the bot said out loud whose conversation that is.
    A new request into the same chat clears it, because at that moment two
    people are in front of her and the bot has no business guessing. Anything
    else — no focus, an expired one — falls through to silence, exactly as a
    bare line did before.

    Note what it must never touch: a customer writing to the bot. Those are in
    `SupportStates.waiting_message`, so `StateFilter(None)` excludes them, and
    an admin using the bot as a customer taps buttons rather than typing.
    """
    if (message.chat.id != config.support_chat_id
            and message.chat.id not in config.env.admin_ids):
        return
    if not (message.text or message.caption or message.photo or message.voice
            or message.document or message.video or message.video_note):
        return

    user_chat_id = await current_focus(message.chat.id)
    if user_chat_id is None:
        return

    queue = SqliteMessageQueue()
    if message.text:
        await queue_reply(queue, user_chat_id, text=message.text)
    else:
        await queue_reply(queue, user_chat_id,
                          copy_from=(message.chat.id, message.message_id))
    # Refreshed on every line, so a conversation does not time out mid-sentence.
    await remember_focus(message.chat.id, user_chat_id)
    logger.info("Support reply queued for chat_id={} (no reply needed)",
                user_chat_id)
    await seen(message)


async def _reply_target(replied: Message | None,
                        admin_chat_id: int) -> int | None:
    """Which customer this reply is aimed at. The table, and nothing else.

    Two guesses used to sit under this — `forward_from`, and a regex for the
    chat_id in the metadata line — and both were removed rather than kept as a
    fallback. They are what the failure was: `forward_from` is empty whenever
    the customer has forwarding privacy on, and the metadata line is only
    readable if the manager replied to that exact message. Keeping them meant a
    reply could still be routed by a guess, and a guess that is right most of
    the time is worse than an error, because the time it is wrong the message
    goes to a stranger.

    Threads that predate the table are not migrated: there were three users when
    it shipped. Replying to one now produces the visible error below, which is
    the correct outcome — the manager retries in the current thread.
    """
    if replied is None:
        return None
    return await support_thread_owner(replied.message_id, admin_chat_id)


# Narrow on purpose. support_chat_id is often an admin's own DM with the bot, and
# this router sits ahead of onboarding — so a bare `F.reply_to_message` swallowed
# anything the admin sent as a reply in that chat, including the contact shared
# during /start, which then never reached registration.
#   StateFilter(None) — never intercept a flow in progress (onboarding, settings)
#   ~F.contact        — a shared contact is never a support reply
@router.message(StateFilter(None), F.reply_to_message, ~F.contact)
async def admin_reply(
    message: Message,
    config: AppConfig,
    t: Texts,
) -> None:
    """Route a reply back to the customer the thread belongs to.

    The support chat, and an admin's own chat with the bot: a discount ask is
    copied to every admin, and a copy nobody can answer is a copy that wastes
    the reader's time. Which message was replied to still decides everything —
    an id that belongs to no thread routes nowhere, here as anywhere else.
    """
    if (message.chat.id != config.support_chat_id
            and message.chat.id not in config.env.admin_ids):
        return

    bot = message.bot
    replied = message.reply_to_message
    user_chat_id = await _reply_target(replied, message.chat.id)

    if not user_chat_id:
        # Only complain about a reply that was plausibly aimed at a customer:
        # replying to something else in this chat is not a support action, and
        # answering it would be noise.
        if replied and replied.from_user and replied.from_user.is_bot:
            await message.answer(operator_texts().MSG_SUPPORT_NO_REPLY_TARGET)
        return

    # Nothing is added to what the manager typed. It used to arrive under
    # "Відповідь від менеджера:", which announced the relay every single time —
    # the customer wrote to the shop and the shop answers, and a label saying so
    # is only in the way. What they see now is the answer, from the chat they
    # wrote to, as if the person were sitting in it.
    # Chosen here for the same reason as everywhere else in bot/: there is no
    # composition root yet, and the scenario must not pick its own storage.
    queue = SqliteMessageQueue()

    # If what they replied to was a discount ask, it is answered now. Nothing
    # here knows or cares whether it was — the id either matches a request or
    # matches nothing, and the customer stops being told to wait either way.
    await mark_discount_answered(replied.message_id)

    if message.text:
        await queue_reply(queue, user_chat_id, text=message.text)
    else:
        # A photo, a voice note or a document. An older version sent
        # `message.text` regardless, and for anything but text that is None —
        # so the customer received the prefix followed by the word "None" and
        # the manager had no way of knowing. The copy carries whatever was
        # actually sent, caption included, and hides that it came from the
        # support chat.
        await queue_reply(
            queue,
            user_chat_id,
            copy_from=(message.chat.id, message.message_id),
        )

    # Queued, not sent: the sender has it within five seconds, retries it if
    # Telegram is busy, and shelves it with an alert if it truly cannot be
    # delivered — instead of raising inside this handler where nobody sees it.
    logger.info("Support reply queued for chat_id={}", user_chat_id)

    # From here her bare lines go to this customer, for FOCUS_MINUTES. Said out
    # loud, once, and naming the person: the whole risk of this is a line meant
    # for somebody else, and she is the only one who can catch it.
    if await current_focus(message.chat.id) != user_chat_id:
        await message.answer(operator_texts().MSG_SUPPORT_FOCUS_ON.format(
            who=await describe(None, user_chat_id)))
    await remember_focus(message.chat.id, user_chat_id)

    # And the manager gets the same mark the customer does, on their own reply:
    # it says the thread was matched to a customer and the answer is on its way.
    # Without it, a reply that found no target and one that did look identical
    # in the support chat — the only difference was a message that appears in
    # the first case.
    await seen(message)
