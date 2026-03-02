# Requirements: KS Telegram Bot

**Defined:** 2026-03-02
**Core Value:** Customers can instantly check the status and details of their orders through Telegram without contacting support

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Onboarding

- [ ] **ONBR-01**: User receives welcome message with configurable brand name on /start
- [ ] **ONBR-02**: User can enter phone number in +380... format with validation
- [ ] **ONBR-03**: Bot looks up phone in Shopify API to find web orders
- [ ] **ONBR-04**: Bot looks up phone in KeyCRM API to find Instagram orders
- [ ] **ONBR-05**: User sees helpful message if phone not found in either system
- [ ] **ONBR-06**: Bot stores Telegram chat_id ↔ phone mapping in SQLite

### Orders

- [ ] **ORDR-01**: User can see order details: status, product names, amounts, delivery date
- [ ] **ORDR-02**: User can see full order history merged from Shopify + KeyCRM
- [ ] **ORDR-03**: Each order shows its source (web / Instagram)
- [ ] **ORDR-04**: User sees friendly error when no orders found or API is unavailable

### Navigation

- [ ] **NAVG-01**: Main menu with 5 buttons: Orders, Find more, Contact support, Website, Settings
- [ ] **NAVG-02**: "Find more" submenu: About us, Contacts, Payment, Delivery + Back button
- [ ] **NAVG-03**: "Settings" submenu: Change phone, Language + Back button
- [ ] **NAVG-04**: Website button opens shop URL in browser
- [ ] **NAVG-05**: Back buttons in all submenus return to parent menu

### Info Pages

- [ ] **INFO-01**: "About us" page displays shop description
- [ ] **INFO-02**: "Contacts" page displays phone, email, social media links
- [ ] **INFO-03**: "Payment" page displays payment methods and terms
- [ ] **INFO-04**: "Delivery" page displays delivery options and timelines

### Support

- [ ] **SUPP-01**: "Contact manager" button forwards user message to support person via Telegram
- [ ] **SUPP-02**: Support person can reply through the bot back to the customer

### Broadcast

- [ ] **BRDC-01**: User can opt out with /stop or /unsubscribe, status persisted in SQLite
- [ ] **BRDC-02**: Admin can broadcast messages to all opted-in users

### Settings

- [ ] **SETT-01**: User can change their linked phone number (re-validates against Shopify/KeyCRM)
- [ ] **SETT-02**: Language selector shows Ukrainian as active (structure ready for future languages)

### Configuration

- [ ] **CONF-01**: All bot UI text is in Ukrainian
- [ ] **CONF-02**: Brand name and greeting text configurable via config file
- [ ] **CONF-03**: Shopify and KeyCRM API credentials stored in environment variables
- [ ] **CONF-04**: Support person Telegram chat ID configurable
- [ ] **CONF-05**: Shop website URL configurable

### Infrastructure

- [ ] **INFR-01**: SQLite database stores user data, opt-out status, and settings
- [ ] **INFR-02**: Shopify API service with phone-based order lookup
- [ ] **INFR-03**: KeyCRM API service with phone-based order lookup
- [ ] **INFR-04**: aiogram 3.x bot framework with long-polling mode

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Enhancements

- **ENHC-01**: Personalized greeting with customer name from Shopify/KeyCRM
- **ENHC-02**: Order search by order number
- **ENHC-03**: Proactive order status change notifications
- **ENHC-04**: Targeted broadcasts (filter by order status or segment)
- **ENHC-05**: Conversation context forwarding to support agent
- **ENHC-06**: Deep links to specific orders
- **ENHC-07**: Multi-language support (Russian, English)

## Out of Scope

| Feature | Reason |
|---------|--------|
| SMS/OTP verification | Phone matched against Shopify/KeyCRM, no verification needed |
| Payment processing in bot | Orders placed outside the bot on Shopify/Instagram |
| Multiple support agents | Single person handles support, scale later if needed |
| Web admin dashboard | Admin works through Telegram, Shopify, and KeyCRM |
| Product catalog in bot | Bot is post-purchase support, not a shop |
| AI/LLM free-form Q&A | Structured info pages cover the need, avoids hallucination risk |
| Real-time GPS order tracking | Show status from Shopify/KeyCRM, no courier API integration |
| Google Sheets integration | Data comes from Shopify + KeyCRM APIs directly |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| (populated during roadmap creation) | | |

**Coverage:**
- v1 requirements: 28 total
- Mapped to phases: 0
- Unmapped: 28 ⚠️

---
*Requirements defined: 2026-03-02*
*Last updated: 2026-03-02 after initial definition*
