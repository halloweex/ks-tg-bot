# Phase 4: Broadcast — Research

**Researched:** 2026-03-04

## Existing Infrastructure

### Database
- `opt_out` table already exists in `bot/db.py` with `chat_id INTEGER PRIMARY KEY` and `opted_out_at TEXT`
- `users` table has all registered users with `chat_id` and `phone`
- Per-operation aiosqlite connections (established pattern)

### Config
- `config.env.admin_ids` — list of admin Telegram user IDs (parsed from comma-separated `ADMIN_USER_IDS` env var)
- `config.support_chat_id` — single support chat ID

### Existing Patterns
- Router-per-feature pattern (each handler file has its own Router)
- Text constants in `bot/texts.py`
- DI via `dp["config"]`, `dp["keycrm"]`, `dp["shopify"]`
- FSM states in `bot/states.py`

## Implementation Research

### BRDC-01: Opt-out (/stop, /unsubscribe)

**Approach:** Command handlers for `/stop` and `/unsubscribe` that INSERT into `opt_out` table.

DB functions needed:
- `opt_out_user(chat_id)` — INSERT OR IGNORE into opt_out
- `opt_in_user(chat_id)` — DELETE from opt_out (for future /start re-subscribe)
- `is_opted_out(chat_id)` — check if user is in opt_out table

**Re-subscribe:** When user sends /start again, remove from opt_out (implicit opt-in by restarting the bot).

### BRDC-02: Admin Broadcast

**Approach:** Admin sends a command like `/broadcast <message>` in their private chat with the bot. Bot queries all users NOT in opt_out, sends message to each with rate limiting.

**Telegram rate limits:**
- 30 messages per second to different chats (Telegram Bot API limit)
- Use `asyncio.sleep(0.05)` between sends (20/sec, safe margin)
- Catch `TelegramRetryAfter` exception — if hit, sleep for the retry_after duration
- Catch `TelegramForbiddenError` — user blocked the bot, skip silently (optionally remove from users)

**Admin check:** Filter by `message.from_user.id in config.env.admin_ids`

**Broadcast flow:**
1. Admin sends `/broadcast` — bot enters FSM state waiting for broadcast message
2. Admin types/sends the message (can be text, photo, etc.)
3. Bot confirms with user count and asks for confirmation
4. Admin confirms — bot starts sending
5. Bot reports progress/completion

**Simplification for v1:** Text-only broadcast. Forward or copy_message for richer content can be v2.

### Pitfalls

1. **Flood control:** Must rate-limit sends. asyncio.sleep between messages.
2. **Blocked users:** Users who blocked the bot will throw TelegramForbiddenError. Must catch and skip.
3. **Large user base:** For thousands of users, broadcast takes minutes. Should send progress updates.
4. **Admin-only access:** Must verify sender is admin before allowing broadcast.
5. **Accidental broadcast:** Confirmation step before sending to all users.

### Router Order Impact
- Broadcast router should be registered early (after common_router) since /stop and /unsubscribe are commands
- Or add /stop and /unsubscribe to common_router alongside /start

## Decisions for Planner

1. Opt-out commands (/stop, /unsubscribe) → insert into opt_out table, confirm to user
2. /start should remove from opt_out (re-subscribe)
3. Broadcast: text-only v1, FSM flow with confirmation
4. Rate limit: 20 msg/sec with asyncio.sleep(0.05)
5. Handle TelegramForbiddenError (blocked users) and TelegramRetryAfter (rate limit hit)
6. Admin check via config.env.admin_ids

---
*Phase: 04-broadcast*
*Researched: 2026-03-04*
