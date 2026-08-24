"""Telegram's message effects, by id.

An effect is the animation a message arrives with — confetti falling over the
bubble. `message_effect_id` takes one of these ids, and the ids are the awkward
part: Telegram documents the parameter but not the values, and there is no Bot
API method that lists them. They are read off a client and pinned here.

Which is why nothing may depend on one working. Every sender that uses these
drops the effect and sends the message plain when Telegram rejects the id — see
bot/outbox.py and bot/screen.py. An id that stops being recognised costs an
animation, never a notification.

Private chats only, by Telegram's rule. Everything here goes to a customer, so
that is not a constraint anything in this bot has to think about.
"""
from __future__ import annotations

# 🎉 — the only one in use, and the only one confirmed against production: it
# has ridden the back-in-stock notification since that message went through the
# outbox. A second id belongs here the day a second moment deserves one, and it
# should be checked on a real client before it ships.
CONFETTI = "5046509860389126442"
