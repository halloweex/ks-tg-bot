# KS Telegram Bot

## What This Is

A Telegram bot for a beauty e-commerce shop that helps customers check their order status and details, get answers to common questions, and connect with a live support person when needed. All communication is in Ukrainian. The bot identifies customers by phone number matched against a Google Sheets order database.

## Core Value

Customers can instantly check the status and details of their orders through Telegram without contacting support.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Customer greeting with configurable brand name and intro message
- [ ] Phone number input and validation (format +380...)
- [ ] Phone lookup against Google Sheets — match existing customers
- [ ] Interactive menu with buttons (My Orders, Support, About)
- [ ] Order display: status, product names, amounts, delivery date
- [ ] Full order history for a customer
- [ ] FAQ auto-responses for common questions
- [ ] Escalation to live support person via Telegram
- [ ] Broadcast messages: promotions, new products, order status updates
- [ ] /stop and /unsubscribe commands to opt out of communication
- [ ] All UI text in Ukrainian
- [ ] Brand name and greeting text configurable via config

### Out of Scope

- SMS verification — phone is matched against sheet, no OTP needed
- Multi-language support — Ukrainian only for v1
- Payment processing — orders are placed outside the bot
- Multiple support agents — single person handles all support
- Web dashboard — all admin through Telegram and Google Sheets

## Context

- The shop already has an active Google Sheet with real order data
- Python 3.14 environment with PyCharm IDE is set up
- Virtual environment (.venv) already created
- The bot greeting follows a warm, personal tone ("I'll help you, I'll show you, and I'll lead you by the hand into the world of beauty")
- Support is handled by one person who receives forwarded conversations
- The shop wants to use broadcasts for marketing (promos, new arrivals) and operational updates (order status changes)

## Constraints

- **Language**: Python — development environment already configured
- **Data source**: Google Sheets — existing order data, must integrate via Google Sheets API
- **Language (UI)**: Ukrainian only — all bot messages in Ukrainian
- **Hosting**: TBD — will choose best fit (VPS, serverless, or similar)
- **Telegram API**: Must use Telegram Bot API for all bot interactions

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Google Sheets as data source | Orders already tracked there, no migration needed | — Pending |
| Phone number as customer ID | Simple identification, no SMS costs, matches existing sheet structure | — Pending |
| Single support person | Small operation, one person handles all inquiries | — Pending |
| Configurable brand name | Bot can be reused or rebranded without code changes | — Pending |
| Ukrainian only | Target audience is Ukrainian-speaking customers | — Pending |

---
*Last updated: 2026-03-02 after initialization*
