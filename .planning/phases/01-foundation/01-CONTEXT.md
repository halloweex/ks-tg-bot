# Phase 1: Foundation - Context

**Gathered:** 2026-03-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Project skeleton, API clients (Shopify + KeyCRM), SQLite database, configuration system, and credential management. No user-facing bot features — just the infrastructure everything depends on. The bot should start and be able to reach both APIs, but onboarding flow and menus are Phase 2+.

</domain>

<decisions>
## Implementation Decisions

### Project Structure
- Claude's discretion on folder structure, entry point, dev tooling, and dependency management
- Python 3.14 environment with .venv already exists
- PyCharm IDE (.idea/) already configured

### Shopify API Access
- Shopify store is live with real orders — user has admin access
- Custom App needs to be created in Shopify Admin for API credentials
- Read-only access needed: orders and customers
- Search orders by customer phone number (phone is required at checkout — always present)
- Shopify API token will be added to .env once Custom App is created
- Bot must handle missing Shopify credentials gracefully at startup (log warning, continue with KeyCRM only)

### KeyCRM API Access
- API key already in .env: KEYCRM_API_KEY
- Search orders by phone number
- KeyCRM holds Instagram orders

### Configuration System
- .env for secrets: BOT_TOKEN, KEYCRM_API_KEY, SHOPIFY_API_TOKEN, ADMIN_USER_IDS
- config.yaml for non-secret settings: brand name, website URL, support chat ID, info page content (About Us, Contacts, Payment, Delivery)
- Info page content (About Us, Contacts, Payment, Delivery) is configurable in config.yaml
- Two admin IDs already configured (129462784, 183618567) — admins can send broadcasts

### Support Person Setup
- Support person is one of the existing admins (same Telegram user ID)
- Support chat ID configurable in config.yaml (SUPPORT_CHAT_ID or similar)

### Git Safety
- No .gitignore exists yet — CRITICAL: create .gitignore FIRST before any code commit
- Must exclude: .env, .venv/, .idea/, __pycache__/, *.pyc, db files

### Ukrainian Text Management
- Developer-maintained (only developer edits texts)
- Some texts ready (greeting), rest needs writing
- Warm personal tone for greeting, neutral/factual for everything else (order info, menus, errors)
- Storage approach: Claude's discretion (Python constants or similar)

### Claude's Discretion
- Project folder structure (flat vs modular packages)
- Entry point (python -m bot vs main.py)
- Dev tooling (Makefile, scripts, or just README)
- Dependency management (requirements.txt vs pyproject.toml)
- Ukrainian text storage format (Python file vs external file)
- SQLite schema design
- Error handling patterns
- Logging approach

</decisions>

<specifics>
## Specific Ideas

- Bot username is `koreanstory_bot` — brand is "Korean Story" (Korean beauty/cosmetics shop)
- The greeting follows a warm, personal tone: "I'll help you, I'll show you, and I'll lead you by the hand into the world of beauty"
- Bot must work even if Shopify credentials aren't set up yet (KeyCRM-only mode) — Shopify access will be added once Custom App is created

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield project, no existing code

### Established Patterns
- .env file already exists with BOT_TOKEN, BOT_USERNAME, KEYCRM_API_KEY, ADMIN_USER_IDS
- Python 3.14 with .venv virtual environment

### Integration Points
- .env is the entry point for all secrets
- config.yaml will be the entry point for all non-secret settings

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-foundation*
*Context gathered: 2026-03-02*
