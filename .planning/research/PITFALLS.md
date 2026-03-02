# Pitfalls Research

**Domain:** Telegram e-commerce bot with Google Sheets backend (beauty shop, Ukrainian market)
**Researched:** 2026-03-02
**Confidence:** MEDIUM — based on training knowledge of Telegram Bot API, Google Sheets API, and python-telegram-bot/aiogram ecosystem. WebSearch/WebFetch unavailable; findings from training data only. Flag for validation before execution phases.

---

## Critical Pitfalls

### Pitfall 1: Google Sheets API Quota Exhaustion on Every User Request

**What goes wrong:**
Every user interaction that checks order status triggers a live Sheets API call. With concurrent users, or a single user navigating the order menu repeatedly, the bot rapidly hits the Google Sheets API quota (60 read requests/minute per user per project on the free tier). The bot starts throwing `HttpError 429` errors, users see broken responses or timeouts, and the shop owner cannot diagnose why because there are no visible errors in Telegram.

**Why it happens:**
Developers treat Google Sheets as if it were a database with low-latency, unlimited reads. The Sheets API is designed for human-driven spreadsheet access, not as an application backend. Calling the API on every message handler is the natural first implementation — it feels clean — but does not account for quota.

**How to avoid:**
Implement an in-memory cache with a short TTL (30–120 seconds) in front of every Sheets read. On each lookup, serve cached data if fresh; only call the API when cache is stale. For a small shop, a simple `dict` keyed by phone number with a timestamp is sufficient. Do NOT skip this in MVP — it is foundational, not an optimization.

Additionally, use batch reads: fetch the entire relevant sheet range in one API call and filter in Python, rather than making per-row queries.

**Warning signs:**
- Bot responds normally for the first few interactions, then starts timing out silently
- Logs show `HttpError 429` or `quota exceeded` from the Google API client
- Bot works fine during off-hours but fails during peak usage

**Phase to address:**
Google Sheets integration phase (Phase 1/2 — foundational data layer). Cache must be in place before any feature that reads order data.

---

### Pitfall 2: Telegram Broadcast Flood Control Banning the Bot

**What goes wrong:**
When sending broadcast messages to all customers, developers naively loop over user IDs and call `bot.send_message()` as fast as possible. Telegram's Bot API enforces flood control: a bot may send at most 30 messages per second globally, and no more than 1 message per second to any individual chat. Exceeding this triggers `RetryAfter` errors and can temporarily restrict the bot, causing all broadcasts to fail mid-send and leaving some customers notified and others not.

**Why it happens:**
Broadcast is often implemented last, quickly, with a simple `for user_id in users: send_message(...)`. The loop completes in milliseconds for small lists but the Telegram server rejects messages. Error handling for `RetryAfter` is non-trivial — it requires reading the retry-after header and sleeping, which is easy to miss.

**How to avoid:**
Implement broadcasts with rate limiting from the start: send no faster than 25 messages/second (buffer below the 30/s limit), respect `RetryAfter` errors by sleeping the specified duration, and use a queue-based approach (e.g., asyncio task queue) rather than a synchronous loop. Track delivery status per recipient so a failed broadcast can be retried only for those who did not receive it.

With python-telegram-bot v20+, use `Application.job_queue` to schedule sends at controlled intervals rather than looping directly.

**Warning signs:**
- Broadcast partially delivers to some users but not others with no visible error
- Logs contain `TelegramError: Flood control exceeded` or `RetryAfter` exceptions
- Bot becomes unresponsive for all users during a broadcast

**Phase to address:**
Broadcast feature phase. Must be designed with rate limiting before any real send to real users.

---

### Pitfall 3: State Machine Breakage From Unhandled Entry Points

**What goes wrong:**
The bot uses a ConversationHandler with states (e.g., `AWAITING_PHONE`, `MAIN_MENU`, `VIEWING_ORDERS`). A user who bookmarks the bot or opens it days later types `/start` mid-conversation, or taps a keyboard button that no longer matches the current state. The bot silently does nothing, or responds with a confusing "I don't understand" message, because the incoming update does not match any handler in the current state.

**Why it happens:**
ConversationHandler state machines are designed around a happy path. Developers test the happy path and miss: re-entrant `/start`, inline keyboard button presses from old messages, users who press the same button twice, or users who send free text when the bot expects a button tap.

**How to avoid:**
- Register a fallback handler in every state that catches unrecognized input and sends a clear "please use the buttons below" message with the keyboard re-displayed.
- Handle `/start` as an entry point that resets state from anywhere — never assume a clean start.
- Use `per_user=True, per_chat=True` in ConversationHandler (the default) to isolate conversations correctly.
- After every state transition, always re-send the keyboard so the user can always make a valid action.

**Warning signs:**
- Users report "the bot stopped responding" after they left and came back
- Logs show updates being dropped with no handler matched
- Bot works perfectly in linear demos but fails for testers who "play around"

**Phase to address:**
Core bot setup phase (conversation flow design). Must be addressed as soon as ConversationHandler is introduced.

---

### Pitfall 4: Phone Number Stored as Float in Google Sheets

**What goes wrong:**
Google Sheets automatically interprets a column of phone numbers like `+380671234567` as numbers or strips the `+` prefix, converting them to floats (`3.8067123E+11`). When the bot reads the sheet and compares the user's input phone number against stored values, the comparison fails for all records — zero matches, every customer gets "order not found" even when their order exists.

**Why it happens:**
Sheets silently coerces numeric-looking strings to numbers unless the column is explicitly formatted as "Plain text." The shop owner likely typed phone numbers manually and did not set column format. Developers write the lookup code assuming strings but the API returns floats.

**How to avoid:**
- In the Google Sheets data reader, normalize all phone values: convert to string, strip whitespace, remove any formatting characters, and re-add `+` prefix if missing before comparison.
- Also normalize the user's input in the same way before comparison.
- Verify raw API response format in a test script before building lookup logic — inspect `values[row][col]` type explicitly.
- Instruct the shop owner to format the phone column as "Plain text" in Sheets to prevent future coercion.

**Warning signs:**
- All lookups return "not found" even for known customers
- Phone values in API response look like `3.8067e+11` instead of `+380671234567`
- Test with a known phone number fails but that row visually exists in the sheet

**Phase to address:**
Google Sheets integration phase — data normalization must be part of the sheet reader from day one.

---

### Pitfall 5: Bot Token and Service Account Credentials Committed to Git

**What goes wrong:**
The Telegram bot token and Google Sheets service account JSON key are placed in the source code or config files and committed to the repository. If the repository is public, or is later made public, these credentials are exposed. Even in private repositories, this creates security debt and makes credential rotation painful.

**Why it happens:**
Early development moves fast. The developer puts the token in `config.json` or `settings.py` to make it work, commits "just this once," and never removes it. The PROJECT.md already has a `config.json` file in the project — this is the most likely place a token gets hardcoded.

**How to avoid:**
- Use environment variables (via `python-dotenv` or system env) for all secrets from the very first commit.
- Add `.env`, `config.json` (if secrets-bearing), and `service_account.json` to `.gitignore` before the first commit.
- Keep a `config.example.json` or `.env.example` with placeholder values as the committed reference.
- Never put the actual bot token or Google credentials in any file that gets committed, even temporarily.

**Warning signs:**
- `config.json` or `credentials.json` appears in `git status` as tracked files
- The bot token appears in `git log` output
- `.gitignore` was created after the first commit

**Phase to address:**
Project setup phase (pre-first-commit). This must be resolved before any code is committed.

---

### Pitfall 6: No Error Boundary on Sheets API Failures — Bot Crashes Silently

**What goes wrong:**
When the Google Sheets API returns an error (network timeout, quota exceeded, credentials expired, sheet renamed), the unhandled exception propagates up and crashes the handler. In python-telegram-bot, an unhandled exception in a handler is logged but the bot continues running — the user receives no response at all. The shop owner has no idea the bot is broken because Telegram shows no error state.

**Why it happens:**
Happy-path development. The Sheets API works during development and testing, so no error handling is added. Production issues (quota, network flakiness, sheet renamed by the owner) are not anticipated.

**How to avoid:**
- Wrap all Sheets API calls in try/except blocks that catch `Exception` (or specific `googleapiclient.errors.HttpError`) and return a user-friendly Ukrainian error message ("Виникла технічна помилка, спробуйте пізніше").
- Add a global error handler via `Application.add_error_handler()` that logs full tracebacks and optionally notifies the support person via Telegram.
- Implement a health-check script that verifies Sheets connectivity on startup.

**Warning signs:**
- Users report "bot doesn't answer" but the process is still running
- Logs show tracebacks but no follow-up user message was sent
- Bot works in dev but fails silently after deploying to a new environment with different credentials

**Phase to address:**
Google Sheets integration phase and deployment phase. Error boundaries before first real-user test.

---

### Pitfall 7: Broadcast Opt-Out List Not Enforced — GDPR/Privacy Violation

**What goes wrong:**
The bot has `/stop` and `/unsubscribe` commands (per PROJECT.md), but the broadcast logic ignores the opt-out list. Users who unsubscribed continue to receive promotional messages. This is not just a bad UX problem — in the EU/Ukraine, sending unsolicited commercial messages to users who opted out is a legal violation.

**Why it happens:**
The opt-out command is implemented early (as a simple "ok, I'll stop" reply) but there is no persistent storage of the opt-out state. Broadcast is implemented later and queries users from Sheets without checking against the opt-out list, which was never persisted anywhere.

**How to avoid:**
- From the start, decide where opt-out state is stored: either a dedicated "opted_out" column in the Google Sheet, or a local file/SQLite database. Do not defer this decision.
- The broadcast function must filter out opted-out user IDs before sending — this is not optional.
- Test the full flow: opt out, trigger broadcast, verify no message sent to opted-out user.

**Warning signs:**
- `/stop` command sends a confirmation but there is no record written anywhere
- Broadcast function iterates over all Sheets rows without any filter
- Opted-out users appear in broadcast recipient lists

**Phase to address:**
Broadcast feature phase. Opt-out persistence must be designed before broadcast sending is implemented.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Hardcoded Ukrainian strings in handler functions | Fast to write | Impossible to review all user-facing text, inconsistent tone | Never — use a constants/strings module from day one |
| Single global `bot` object without async-safe patterns | Simple synchronous code | Race conditions on concurrent updates; breaks with python-telegram-bot v20+ async | Never — use the Application pattern throughout |
| Polling instead of webhook in production | No server config needed | Higher latency, higher resource usage, polling can miss updates on restart | Acceptable during development; switch to webhook for production |
| Sheet read on every handler call (no cache) | Zero infrastructure needed | Quota exhaustion, slow responses | Never — implement cache in Phase 1 |
| Storing user state in Python module-level variables | Easy debugging | State lost on restart; no multi-process safety | Only for single-session prototyping; replace before any real users |
| No logging | Cleaner output | Cannot debug production issues | Never — set up structured logging from the start |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Google Sheets API | Using `gspread` with user OAuth credentials that expire | Use a Service Account with `service_account.json`; credentials never expire unless revoked |
| Google Sheets API | Reading the full sheet on every request | Read once, cache in memory with TTL; only re-read on cache expiry or explicit refresh |
| Google Sheets API | Assuming row order is stable (row 5 is always customer X) | Filter by column value (phone number), never rely on row index |
| Google Sheets API | Sheet name hardcoded as "Sheet1" | Make sheet name configurable; "Sheet1" gets renamed by non-technical owners |
| Telegram Bot API | Calling `bot.send_message()` with a deleted/blocked chat_id | Catch `Forbidden` (user blocked bot) and `ChatNotFound` exceptions; remove from recipient list |
| Telegram Bot API | Using `reply_markup` with hardcoded button text | Extract all keyboard layouts to constants; text must match Ukrainian throughout |
| Telegram Bot API | Not handling `CallbackQuery.answer()` after inline button tap | Every inline button press must be answered (even with empty string) or Telegram shows loading spinner indefinitely |
| Telegram Bot API | Forwarding messages to support without context | Include user phone number and conversation summary in the forwarded message so support agent has context |
| python-telegram-bot v20+ | Using synchronous Sheets calls inside async handlers | Google API client is sync; wrap in `asyncio.to_thread()` or use `run_in_executor` to avoid blocking event loop |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Synchronous Google Sheets call inside async handler | Bot handles one user at a time; others wait | Wrap sync API calls in `asyncio.to_thread()` | Breaks with 2+ concurrent users |
| No Sheets caching | Quota errors during busy periods | In-memory cache with TTL | Breaks at ~5+ concurrent users or sustained traffic |
| Broadcast loop without rate limiting | Partial delivery, bot gets flood-controlled | Queue-based send with 25 msg/s ceiling | Breaks at ~30+ recipients sent instantly |
| Polling with long interval | Delayed response to user messages | Use webhook in production, or polling with 0 interval in dev | Degrades UX immediately; noticeable at any scale |
| Loading entire Sheet on every cache miss | Slow first response after cache expiry | Acceptable for small sheets (<500 rows); pre-warm cache on startup | Breaks at ~1000+ rows (3-5 second load times) |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Bot token in source code / config.json committed to git | Anyone with repo access can impersonate the bot, send messages to all users, delete webhooks | Use env vars; gitignore all secret files before first commit |
| Service account JSON key committed to git | Full read/write access to all Sheets in the Google account | Same as above — env var or secrets manager |
| No validation of phone number format before Sheets lookup | Malformed input causes API errors; potential for injection if sheet formulas are used | Validate `+380XXXXXXXXX` format with regex before any lookup |
| Support escalation forwards full conversation without consent notice | Privacy concern; in Ukraine, GDPR-equivalent law applies | Note in bot that conversations may be reviewed by support team |
| No check that `chat_id` matches expected user before showing orders | User A could theoretically trigger order display for User B if they know their phone | Phone lookup returns data only for the authenticated phone — verify ownership at session start, not per-request |

---

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Bot shows "I don't understand" with no keyboard after user types free text | User is stuck with no way forward | Always re-display the relevant keyboard with every error response |
| Order status shown as raw English string from Sheet ("pending", "shipped") | Confuses Ukrainian-speaking users; feels unfinished | Map English status values to Ukrainian display strings in a lookup table |
| Phone number input fails silently if format is wrong | User re-enters the same wrong number repeatedly | Provide explicit format example: "Введіть номер у форматі +380XXXXXXXXX" |
| "No orders found" shown to a customer who has orders but entered wrong number | Customer thinks their order is lost | Add "Check the number you registered your order with" hint and offer to try again |
| Support escalation sends conversation to support agent but user gets no confirmation | User doesn't know if their request was received | Always confirm: "Ваш запит передано оператору, відповідь надійде найближчим часом" |
| Broadcast message has no unsubscribe reminder | Annoys users; may trigger bot block | Every broadcast should end with "Щоб відписатись, натисніть /stop" |
| Bot greeting re-fires on every `/start` even for returning users | Returning users sit through lengthy onboarding again | Detect known users (phone already on file) and skip or shorten the greeting |

---

## "Looks Done But Isn't" Checklist

- [ ] **Phone lookup:** Works for new phone format entry but also handles copy-paste with spaces, parentheses, or dash separators — verify normalization handles `+38 (067) 123-45-67`
- [ ] **Order display:** Shows all orders for a customer, not just the most recent — verify with a test customer who has 5+ orders
- [ ] **Broadcast send:** Respects opt-out list — verify by opting out, triggering broadcast, confirming no delivery
- [ ] **Support escalation:** Forwarded message includes phone number and user's question — verify support agent can identify the customer from the forwarded message alone
- [ ] **Unsubscribe persistence:** `/stop` is handled but verify opt-out survives bot restart — confirm state is written to persistent storage (Sheet or file), not only to memory
- [ ] **Error handling:** Break the Sheets connection (rename the sheet tab) and verify the bot sends a Ukrainian error message instead of going silent
- [ ] **Rate limiting in broadcast:** Broadcast to a list of 50 test IDs and verify no `RetryAfter` errors in logs
- [ ] **Inline button answers:** Tap every inline button and verify the spinner disappears — means `CallbackQuery.answer()` is called in every handler
- [ ] **Webhook vs polling:** In production, verify updates are not being processed twice (polling AND webhook active simultaneously causes duplicate handling)

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Bot token leaked in git | HIGH | Revoke token immediately via @BotFather, generate new token, rotate in all environments, audit git history with `git log -S "token_value"` to confirm removal |
| Service account credentials leaked | HIGH | Revoke key in Google Cloud Console, generate new key, update all environments |
| Broadcast sent to opted-out users | MEDIUM | Send a personal apology message to affected users, fix opt-out enforcement before next broadcast |
| Sheets quota exceeded (bot silently broken) | LOW | Wait for quota reset (1 minute), implement caching before restarting bot |
| ConversationHandler state corrupted for a user | LOW | User sends `/start` to reset; no persistent state loss for other users |
| Phone normalization bug — zero matches | MEDIUM | Push hotfix to normalize phone comparison; no data loss but users get broken experience until fix deployed |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Sheets quota exhaustion | Phase: Sheets integration (data layer) | Load test with 10 concurrent lookups; confirm no 429 errors |
| Broadcast flood control | Phase: Broadcast feature | Send test broadcast to 50 IDs at full speed; verify rate limiting kicks in |
| ConversationHandler state breakage | Phase: Core bot / conversation flow | Test `/start` mid-conversation, back button, old message buttons |
| Phone number stored as float | Phase: Sheets integration | Inspect raw API response for phone column; verify type is string |
| Credentials in git | Phase: Project setup (pre-first-commit) | Run `git log --all -S "token"` and verify no secrets in history |
| No error boundary on Sheets failure | Phase: Sheets integration | Rename sheet tab, trigger lookup, verify Ukrainian error message sent |
| Opt-out not enforced in broadcast | Phase: Broadcast feature | Opt out, run broadcast, verify no message delivered |

---

## Sources

- Telegram Bot API official documentation — flood control and rate limits section (training knowledge, MEDIUM confidence)
- Google Sheets API Quotas documentation — 60 requests/minute/user/project on free tier (training knowledge, MEDIUM confidence; verify at developers.google.com/sheets/api/limits)
- python-telegram-bot v20 migration guide — async handlers, Application pattern, job_queue (training knowledge, MEDIUM confidence; verify at python-telegram-bot.org)
- aiogram 3.x documentation — async patterns for Telegram bots in Python (training knowledge, MEDIUM confidence)
- GDPR and Ukrainian data protection law equivalents — opt-out requirements for commercial messaging (training knowledge, LOW confidence; verify with legal counsel)
- gspread library documentation — service account auth, range reading patterns (training knowledge, MEDIUM confidence)

---
*Pitfalls research for: Telegram e-commerce bot with Google Sheets backend*
*Researched: 2026-03-02*
*Note: WebSearch and WebFetch were unavailable during this research session. All findings are from training data (knowledge cutoff August 2025). Flag MEDIUM/LOW confidence items for verification against current official docs before implementation.*
