# Feature Research

**Domain:** Telegram e-commerce customer service bot (beauty shop, order tracking, support, broadcasts)
**Researched:** 2026-03-02
**Confidence:** MEDIUM — based on training knowledge (cutoff August 2025). Web search was unavailable. Core Telegram Bot API patterns are stable and well-established; e-commerce bot conventions are widely documented. Flagged where confidence is lower.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete or broken.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Welcome message with brand name | Every bot greets users; establishes trust immediately | LOW | Configurable via config file, not hardcoded |
| Phone number collection + validation | Required to look up orders; users expect to be identified somehow | LOW | Ukrainian format (+380XXXXXXXXX); validate before lookup |
| Order status lookup | Core value proposition — the reason the bot exists | MEDIUM | Google Sheets API call per lookup; cache to avoid quota |
| Order history list | Users expect to see all past orders, not just latest | MEDIUM | Multiple rows per phone number in Sheet; paginate if >5 orders |
| Inline keyboard navigation (buttons) | Telegram users expect tappable buttons, not typed commands | LOW | InlineKeyboardMarkup; ReplyKeyboardMarkup for persistent menu |
| Main menu (My Orders / Support / About) | Standard bot UX pattern; users navigate by tapping | LOW | Persistent reply keyboard or inline keyboard after auth |
| /start command handler | Telegram convention; every bot must handle /start | LOW | Re-greets returning users, initializes conversation |
| /stop and /unsubscribe commands | Legal/ethical requirement; users must be able to opt out | LOW | Remove user from broadcast list; confirm opt-out |
| Error handling with user-friendly messages | Users panic if bot goes silent or throws a stack trace | MEDIUM | Handle Google Sheets API errors, Telegram API errors gracefully |
| "Escalate to human" option | Users expect to reach a real person when bot can't help | LOW | Forward conversation to support Telegram account |
| FAQ / common questions auto-response | Deflects repetitive support requests; users expect instant answers | MEDIUM | Keyword matching or button-based FAQ navigation |
| All text in Ukrainian | Target audience is Ukrainian-speaking; other languages feel wrong | LOW | All strings in constants file; no hardcoded English messages |

### Differentiators (Competitive Advantage)

Features that set the product apart. Not required for launch, but valued by users or the shop owner.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Broadcast messaging (promos / arrivals) | Shop owner reaches customers at scale inside Telegram — familiar channel | MEDIUM | Must respect opt-out list; rate-limited by Telegram (30 msg/sec global, 1 msg/sec per user) |
| Order status change broadcast | Proactive notification — "Your order shipped!" without user asking | HIGH | Requires polling Sheet for status changes or webhook trigger from Sheet; needs change-detection logic |
| Personalized greeting ("Hello, [Name]") | Warm touch; makes bot feel less robotic | LOW | Store name from Sheet alongside phone; display on auth |
| Configurable brand name + greeting text | Shop owner can rebrand or update tone without touching code | LOW | All strings in config file or env vars; PROJECT.md already specifies this |
| Conversation forwarding to support with context | Support agent sees full conversation, not just "user needs help" | MEDIUM | Forward message thread or send context summary to support chat |
| Order search by order number | Customer may not remember phone or have multiple accounts | LOW | Additional Sheet lookup column; nice for returning customers |
| Deep link to specific order | Share order link inside Telegram; customer taps and sees that order | LOW | Telegram deep links (/start=order_12345); requires URL encoding |
| Admin broadcast targeting (all / filtered) | Send to all subscribers OR only those with pending orders | HIGH | Requires storing subscriber metadata; filter logic on send |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create serious problems for a small operation.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| OTP / SMS verification | "More secure" identification | Adds SMS cost, external service dependency, friction for users; PROJECT.md explicitly ruled this out | Match phone against Sheet (existing data); phone-based trust is sufficient for order display |
| Payment processing inside bot | "Complete the purchase in Telegram" | Requires PCI compliance, payment gateway integration, significant complexity; out of scope for customer-service bot | Keep payments in existing shop; bot is for post-purchase support only |
| Multi-language support | "What about Russian speakers?" | Creates maintenance burden, translation drift, UI complexity; target is Ukrainian shop with Ukrainian customers | Ukrainian only for v1; language choice is a business decision |
| Multiple support agents / queue | "What if one person is overloaded?" | Requires agent routing logic, status tracking, handoff protocol — full helpdesk system scope | Single agent with simple Telegram forward; scale when actually needed |
| Web dashboard for admin | "I want to see analytics" | Separate frontend, auth system, hosting — doubles scope; small shop doesn't need dashboards | Shop owner manages via Google Sheets (already familiar) + Telegram itself |
| Full product catalog / shop in bot | "Users could browse and order in bot" | Transforms customer-service bot into e-commerce platform — completely different product | Keep catalog in existing shop; bot handles post-purchase experience only |
| AI/LLM-based free-form Q&A | "Smart bot that answers anything" | API costs, hallucination risk on order data, complexity; overkill for known FAQ set | Structured FAQ with button navigation covers 90% of questions |
| Real-time order tracking (GPS/courier) | "Show exactly where the package is" | Requires courier API integration per carrier; carriers in Ukrainian market rarely have APIs | Show order status from Sheet (e.g., "Shipped", "In transit", "Delivered") |
| Automatic Google Sheets write-back | "Update order status from bot" | Creates two-way sync conflicts; shop team manages Sheet directly | Read-only from Sheet; all updates happen in Sheet by shop staff |

---

## Feature Dependencies

```
[Phone validation]
    └──requires──> [Google Sheets lookup]
                       └──enables──> [Order history display]
                       └──enables──> [Personalized greeting]

[/start command]
    └──triggers──> [Welcome message]
                       └──prompts──> [Phone collection]

[Main menu]
    └──requires──> [Phone validation] (menu appears after auth)

[Escalate to support]
    └──requires──> [Support Telegram account configured in config]

[Broadcast send]
    └──requires──> [Subscriber list] (users who started bot and not opted out)
    └──requires──> [/stop handler] (list must respect opt-outs)

[Order status change broadcast]
    └──requires──> [Broadcast send]
    └──requires──> [Sheet change-detection logic] (new dependency, increases complexity)

[FAQ auto-response]
    └──enhances──> [Escalate to support] (FAQ deflects before human needed)

[/stop command]
    └──conflicts──> [Broadcast send] (opted-out users must never receive broadcasts)
```

### Dependency Notes

- **Phone validation requires Google Sheets lookup:** The phone check IS the Sheets call. No separate validation step — validation is "does this phone exist in the Sheet?"
- **Main menu requires authentication:** Menu should not be shown to unidentified users. Gate the menu behind successful phone lookup.
- **Broadcast requires opt-out respect:** The /stop and /unsubscribe handlers must write to a persistent opt-out list. Broadcast must filter against this list before sending.
- **Order status change broadcast is a separate, complex feature:** It requires detecting changes in a Sheet (polling or trigger), which is a significant architectural addition. Treat as v1.x or v2, not MVP.
- **FAQ deflects before support escalation:** Put FAQ before "Talk to human" in the user flow. Reduces support load.

---

## MVP Definition

### Launch With (v1)

Minimum viable product — what's needed for the bot to be useful to real customers.

- [ ] /start command + welcome message with configurable brand name — entry point, sets tone
- [ ] Phone number collection + Ukrainian format validation — identifies the customer
- [ ] Google Sheets lookup by phone — links identity to order data
- [ ] Order history display (status, product names, amounts, delivery date) — core value
- [ ] Main menu (My Orders / Support / About) via inline keyboard — navigation
- [ ] FAQ button-based auto-responses — deflects top 5-10 common questions
- [ ] Escalate to human support (forward to support Telegram account) — fallback
- [ ] /stop and /unsubscribe commands with opt-out persistence — legal/ethical requirement
- [ ] Broadcast send to all opted-in users (promo / new arrivals) — marketing need called out in PROJECT.md
- [ ] All UI in Ukrainian — target audience requirement
- [ ] Error handling: Sheet API down, phone not found, no orders found

### Add After Validation (v1.x)

Features to add once core is proven working with real users.

- [ ] Personalized greeting with customer name from Sheet — trigger: users respond positively to bot
- [ ] Conversation context forwarding to support — trigger: support agent complains about missing context
- [ ] Targeted broadcast (filter by order status) — trigger: shop owner wants to send "your order is delayed" only to affected customers
- [ ] Order search by order number — trigger: users report confusion when phone lookup fails

### Future Consideration (v2+)

Features to defer until there's clear demand.

- [ ] Order status change proactive broadcast — requires Sheet change-detection infrastructure; significant complexity; validate need first
- [ ] Deep link to specific order — nice UX but low urgency; add when sharing orders becomes a common user request
- [ ] Admin broadcast targeting (segmented audiences) — add when shop has marketing sophistication to use segmentation

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| /start + welcome message | HIGH | LOW | P1 |
| Phone collection + validation | HIGH | LOW | P1 |
| Google Sheets lookup | HIGH | MEDIUM | P1 |
| Order history display | HIGH | MEDIUM | P1 |
| Main menu (inline keyboard) | HIGH | LOW | P1 |
| /stop + opt-out | HIGH | LOW | P1 |
| Broadcast to all subscribers | HIGH | MEDIUM | P1 |
| FAQ auto-responses | HIGH | MEDIUM | P1 |
| Escalate to support | HIGH | LOW | P1 |
| Error handling (Sheet down, no orders) | HIGH | MEDIUM | P1 |
| Personalized greeting (name) | MEDIUM | LOW | P2 |
| Context forwarding to support | MEDIUM | MEDIUM | P2 |
| Targeted broadcast (filtered) | MEDIUM | HIGH | P2 |
| Order search by order number | LOW | LOW | P2 |
| Order status change broadcast | HIGH | HIGH | P3 |
| Deep link to order | LOW | LOW | P3 |
| Admin broadcast targeting | MEDIUM | HIGH | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

---

## Competitor Feature Analysis

Note: Direct competitor analysis was not possible (web search unavailable). The following is based on widely observed patterns in Telegram bots for small e-commerce shops as of training data (August 2025). Confidence: MEDIUM.

| Feature | Common in Generic Bots | Common in E-commerce Bots | Our Approach |
|---------|------------------------|---------------------------|--------------|
| Welcome + auth flow | Present but often clunky (OTP) | Phone lookup against CRM | Phone matched against Sheet — no OTP needed |
| Order status | Usually requires external CRM | Varies; many use inline DBs | Google Sheets as source of truth — meets user where data already lives |
| Broadcast | Often not present or basic | Present in mature bots | Built into v1 — core marketing need |
| FAQ | Often free-form AI (expensive) | Button-based navigation | Button-based FAQ — reliable, zero API cost |
| Support escalation | Often missing | Usually available | Forward to single support Telegram account |
| Opt-out (/stop) | Often missing (compliance gap) | Present in compliant bots | Required in v1 — legal and ethical baseline |
| Personalization | Rarely done | Name-based greetings common | v1.x — easy win deferred for focus |
| Multi-agent support | Rare in small shop bots | Present in large operations | Deliberately out of scope — single agent |

---

## Sources

- Telegram Bot API official documentation (patterns and capabilities): training knowledge, cutoff August 2025 — MEDIUM confidence
- Google Sheets API integration patterns with Python: training knowledge — MEDIUM confidence
- Small e-commerce Telegram bot patterns (Ukrainian market): training knowledge, no direct market data available — LOW confidence for market-specific claims
- PROJECT.md requirements: direct source — HIGH confidence for project constraints
- Web search unavailable; Brave Search API key not configured

**Confidence flags:**
- Feature list shape (table stakes vs differentiators): MEDIUM — standard for this bot type, patterns are stable
- Ukrainian e-commerce market specifics (carrier integrations, payment processors): LOW — not verified against current market
- Telegram API rate limits (30 msg/sec global broadcast, 1 msg/sec per user): MEDIUM — these have been stable Telegram limits, but verify against current API docs before implementing broadcasts

---
*Feature research for: Telegram e-commerce customer service bot (beauty shop)*
*Researched: 2026-03-02*
