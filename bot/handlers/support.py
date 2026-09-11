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
from core.repos.support import (album_in_progress, mark_discount_answered,
                                remember_support_thread, start_album,
                                support_thread_owner)
from core.usecases.support import queue_reply
from bot.screen import ephemeral, seen
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

    thread_ids: list[int] = []

    # Every call to the support chat in one place, because they fail together
    # and they fail for one reason: the chat cannot be written to. Telegram
    # says so with "bot can't initiate conversation with a user" when
    # support_chat_id names an account that has never opened this bot — which
    # is a configuration mistake, not a customer's problem, and used to reach
    # the customer as silence and the operator as nothing at all.
    try:
        if first_of_album:
            # Send metadata line with chat_id (privacy-safe identifier)
            note = await bot.send_message(
                chat_id=config.support_chat_id,
                text=op.MSG_SUPPORT_ADMIN_NOTE.format(
                    who=await describe(message.from_user, message.chat.id)),
                parse_mode="HTML",
            )
            thread_ids.append(note.message_id)

        # Forward the actual message. forward_message carries whatever the
        # customer sent — photo, voice, video note, document — so attachments
        # reach the manager unchanged in this direction.
        forwarded = await bot.forward_message(
            chat_id=config.support_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        thread_ids.append(forwarded.message_id)

        if first_of_album:
            # Send instruction for replying
            instruction = await bot.send_message(
                chat_id=config.support_chat_id,
                text=op.MSG_SUPPORT_REPLY_INSTRUCTION,
            )
            thread_ids.append(instruction.message_id)
    except TelegramAPIError as exc:
        logger.error("Support relay to chat {} failed: {}",
                     config.support_chat_id, exc)
        await tell_admins_once(
            bot, config.env.admin_ids, "support_relay",
            f"Support relay is broken: {exc}\n\n"
            f"support_chat_id={config.support_chat_id}. A bot cannot write to a "
            f"user who has never opened it — that account must press Start, or "
            f"the id must name a group the bot is in. Customers are being told "
            f"to try again; their messages are not reaching anyone.",
        )
        # The state is deliberately left alone: whatever they send next is
        # still a support message, so a retry is one tap and not a new flow.
        await message.answer(t.MSG_SUPPORT_NOT_DELIVERED)
        return

    # Every bot-sent message of the request, because a manager replies to
    # whichever is under their thumb — most often the forwarded one, which is
    # exactly the one carrying no usable sender when the customer has
    # forwarding privacy on. Each part of an album is registered too, so a
    # reply to any photo reaches the right person.
    await remember_support_thread(thread_ids, message.chat.id)

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
    # The durable half of the confirmation is the reaction on their own
    # message: it sits where the customer is already looking and is still there
    # next week. The line of text is the transient half — it answers the tap
    # and is noise a day later, so it takes itself back.
    await seen(message)
    # No keyboard to attach: the menu is already under the input field.
    await ephemeral(message,
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


async def _reply_target(replied: Message | None) -> int | None:
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
    return await support_thread_owner(replied.message_id)


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
    user_chat_id = await _reply_target(replied)

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

    # And the manager gets the same mark the customer does, on their own reply:
    # it says the thread was matched to a customer and the answer is on its way.
    # Without it, a reply that found no target and one that did look identical
    # in the support chat — the only difference was a message that appears in
    # the first case.
    await seen(message)
