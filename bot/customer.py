"""Who is writing, in the words a manager needs.

Every message the bot puts in the support chat is about somebody, and until now
it said so with a chat id — an identifier that is safe, stable, and tells the
person reading it nothing at all. A manager looking at a discount request could
not say whose it was without going and looking the number up.

Three sources answer the question, in this order: the name on the CRM's buyer
card (which is the name the shop knows them by, and the one that will match
their orders), the name on their Telegram account, and nothing. The Telegram
username and the verified phone are added where we have them, because they are
the two ways to act on the answer — write to them, or find them in the CRM.
"""
from __future__ import annotations

from aiogram.types import User

from core import texts
from core.repos.users import get_user


def telegram_name(user: User | None) -> str:
    """First and last name as the account carries them, or ""."""
    if user is None:
        return ""
    return " ".join(part for part in (user.first_name, user.last_name) if part)


async def describe(user: User | None, chat_id: int) -> str:
    """One line naming this customer, ready to be put in a support message.

    `chat_id` rather than user.id, and both are passed, because they are the
    same number in a private chat and it is the chat the thread is keyed on —
    an assumption worth stating where it is relied upon rather than three
    call sites away.
    """
    profile = await get_user(chat_id) or {}
    return texts.customer_ref(
        chat_id,
        name=(profile.get("full_name") or "").strip() or telegram_name(user),
        username=(user.username or "") if user is not None else "",
        phone=profile.get("phone") or "",
    )
