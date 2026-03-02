# Phase 1: Foundation - Research

**Researched:** 2026-03-02
**Domain:** Python Telegram bot skeleton — aiogram 3, Shopify GraphQL API, KeyCRM REST API, SQLite, credential management
**Confidence:** HIGH — stack versions verified live from PyPI; KeyCRM API tested against real live account; Shopify GraphQL pattern verified from official docs; aiogram patterns verified from installed package and official docs

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Project Structure**
- Claude's discretion on folder structure, entry point, dev tooling, and dependency management
- Python 3.14 environment with .venv already exists
- PyCharm IDE (.idea/) already configured

**Shopify API Access**
- Shopify store is live with real orders — user has admin access
- Custom App needs to be created in Shopify Admin for API credentials
- Read-only access needed: orders and customers
- Search orders by customer phone number (phone is required at checkout — always present)
- Shopify API token will be added to .env once Custom App is created
- Bot must handle missing Shopify credentials gracefully at startup (log warning, continue with KeyCRM only)

**KeyCRM API Access**
- API key already in .env: KEYCRM_API_KEY
- Search orders by phone number
- KeyCRM holds Instagram orders

**Configuration System**
- .env for secrets: BOT_TOKEN, KEYCRM_API_KEY, SHOPIFY_API_TOKEN, ADMIN_USER_IDS
- config.yaml for non-secret settings: brand name, website URL, support chat ID, info page content (About Us, Contacts, Payment, Delivery)
- Info page content (About Us, Contacts, Payment, Delivery) is configurable in config.yaml
- Two admin IDs already configured (129462784, 183618567) — admins can send broadcasts

**Support Person Setup**
- Support person is one of the existing admins (same Telegram user ID)
- Support chat ID configurable in config.yaml (SUPPORT_CHAT_ID or similar)

**Git Safety**
- No .gitignore exists yet — CRITICAL: create .gitignore FIRST before any code commit
- Must exclude: .env, .venv/, .idea/, __pycache__/, *.pyc, db files

**Ukrainian Text Management**
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

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INFR-01 | SQLite database stores user data, opt-out status, and settings | aiosqlite 0.22.1 provides async SQLite; schema design and initialization pattern documented below |
| INFR-02 | Shopify API service with phone-based order lookup | Shopify GraphQL Admin API with `customers(query: "phone:+380...")` filter — verified from official docs |
| INFR-03 | KeyCRM API service with phone-based order lookup | `GET /v1/order?include=buyer&filter[buyer_phone]=380...` — VERIFIED live against real KeyCRM account |
| INFR-04 | aiogram 3.x bot framework with long-polling mode | aiogram 3.25.0 — latest version, Python 3.14 supported, `dp.start_polling(bot)` pattern |
| CONF-01 | All bot UI text is in Ukrainian | `bot/texts.py` module with Ukrainian string constants — Claude's discretion on format |
| CONF-02 | Brand name and greeting text configurable via config file | `config.yaml` loaded at startup, injected via config object — pattern documented below |
| CONF-03 | Shopify and KeyCRM API credentials stored in environment variables | python-dotenv 1.2.2 + pydantic Settings — .env loading pattern documented below |
| CONF-04 | Support person Telegram chat ID configurable | Part of config.yaml non-secret settings |
| CONF-05 | Shop website URL configurable | Part of config.yaml non-secret settings |
</phase_requirements>

---

## Summary

Phase 1 establishes the entire technical skeleton the bot runs on. Three domains are in scope: (1) the Python project structure and packaging, (2) the API clients for Shopify and KeyCRM, and (3) the SQLite database initialization. A fourth cross-cutting concern — credential management and .gitignore safety — must be resolved before any code is committed.

The stack is simple and deliberately narrow. aiogram 3.25.0 is the Telegram framework (async, router-based, FSM built-in). Shopify uses the GraphQL Admin API queried directly via httpx (no Shopify SDK needed — the REST SDK is unmaintained and the official recommendation shifted to GraphQL in 2024). KeyCRM uses its REST API at `https://openapi.keycrm.app/v1` with a Bearer token — this was tested live and the phone-based order filter (`filter[buyer_phone]`) is confirmed working. SQLite is managed via the built-in `sqlite3` module wrapped by aiosqlite for async access.

A critical finding from live API testing: KeyCRM stores phone numbers in mixed formats — some orders have `380XXXXXXXXX` (no plus sign) and others have `+380XXXXXXXXX`. Phone normalization must be applied before comparison: strip `+`, strip spaces, and strip leading zeros to produce a canonical form. Bot users will enter numbers in `+380...` format; the normalization must handle both sides.

**Primary recommendation:** Build in strict order — .gitignore first, then config/env loading, then database schema, then Shopify client (graceful degradation), then KeyCRM client, then the minimal bot entrypoint with /start handler. Do not skip any step; each is a hard dependency for downstream phases.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| aiogram | 3.25.0 | Telegram bot framework | Async-first, FSM built-in, router-based handlers, Python 3.14 explicitly supported; dominant choice for new Python bots as of 2025-2026 |
| aiosqlite | 0.22.1 | Async SQLite access | Official async bridge to stdlib sqlite3; no external DB server needed; Requires-Python >=3.9, confirmed Python 3.14 compatible |
| python-dotenv | 1.2.2 | .env loading | Zero-dependency; standard pattern for keeping secrets out of source |
| pyyaml | 6.0.3 | config.yaml parsing | Standard YAML library; Python 3.14 native wheel available |
| httpx | 0.28.1 | Async HTTP client for APIs | Used for both Shopify GraphQL (POST) and KeyCRM REST (GET); async-first, supports both sync and async; preferred over aiohttp for service clients because of cleaner API |
| loguru | 0.7.3 | Structured logging | Drop-in replacement for stdlib logging; async-safe; no setup boilerplate |

### Supporting (auto-installed by aiogram)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| aiohttp | 3.13.3 | aiogram's internal HTTP transport | Pulled in by aiogram automatically; do NOT install separately — let aiogram control the version |
| pydantic | 2.12.5 | Config validation | Auto-installed by aiogram; use for the Settings model that validates .env values at startup |
| aiofiles | 25.1.0 | Async file I/O | Auto-installed by aiogram |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| httpx | aiohttp directly | aiohttp is already installed via aiogram but its API is more verbose; httpx has cleaner client API and is explicitly async-native |
| httpx | ShopifyAPI (PyPI) | ShopifyAPI SDK is REST-only and partially abandoned; Shopify officially deprecated REST API for public apps; GraphQL via httpx is 10 lines of code |
| aiosqlite | SQLAlchemy async | SQLAlchemy is correct for large schemas with complex relations; for 2 simple tables (users, opt_out) it is 10x overkill |
| pyyaml | python-decouple | python-decouple merges .env and config; keeping them separate (secrets in .env, settings in yaml) is clearer for this project |

**Installation:**
```bash
pip install aiogram==3.25.0 aiosqlite==0.22.1 python-dotenv==1.2.2 pyyaml==6.0.3 httpx==0.28.1 loguru==0.7.3
```

Dry-run verified on Python 3.14.2 — no conflicts.

---

## Architecture Patterns

### Recommended Project Structure

```
ks-tg-bot/
├── bot/
│   ├── __init__.py          # empty — makes `python -m bot` work
│   ├── __main__.py          # entry point: asyncio.run(main())
│   ├── config.py            # Settings: loads .env + config.yaml
│   ├── db.py                # Database: schema creation + connection lifecycle
│   ├── texts.py             # All Ukrainian UI strings (constants)
│   ├── handlers/
│   │   ├── __init__.py
│   │   └── common.py        # /start handler (Phase 1 scope)
│   └── services/
│       ├── __init__.py
│       ├── shopify.py       # ShopifyClient: GraphQL phone lookup
│       └── keycrm.py        # KeyCRMClient: REST phone lookup
├── .env                     # BOT_TOKEN, KEYCRM_API_KEY, SHOPIFY_* — never committed
├── .env.example             # Template with placeholder values — committed
├── config.yaml              # brand_name, website_url, support_chat_id, etc.
├── .gitignore               # Must exist before first commit
└── requirements.txt         # Pinned dependencies
```

**Why `python -m bot` works:** The `bot/` directory is a package. `bot/__main__.py` is Python's convention for runnable packages. Running `python -m bot` executes `bot/__main__.py`.

### Pattern 1: Config Loading (Two-file pattern)

**What:** Secrets from `.env` via pydantic Settings; non-secret settings from `config.yaml` via PyYAML. Both loaded at startup into a single `AppConfig` object injected into the Dispatcher.

**When to use:** Always. This is the only pattern that satisfies CONF-02 (configurable brand name in a file) and CONF-03 (credentials in environment variables).

**Example:**
```python
# bot/config.py
from pydantic_settings import BaseSettings
import yaml
from pathlib import Path
from dataclasses import dataclass

class EnvSettings(BaseSettings):
    """Loads from .env file via python-dotenv"""
    bot_token: str
    keycrm_api_key: str
    shopify_api_token: str | None = None  # Optional — bot runs without Shopify
    shopify_store_url: str | None = None
    admin_user_ids: str = ""  # "129462784,183618567"

    @property
    def admin_ids(self) -> list[int]:
        return [int(x.strip()) for x in self.admin_user_ids.split(",") if x.strip()]

    class Config:
        env_file = ".env"

@dataclass
class AppConfig:
    """Combined config: env secrets + yaml settings"""
    env: EnvSettings
    brand_name: str
    website_url: str
    support_chat_id: int
    # info page content for Phase 3
    about_text: str
    contacts_text: str
    payment_text: str
    delivery_text: str

def load_config() -> AppConfig:
    env = EnvSettings()
    with open("config.yaml", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f)
    return AppConfig(
        env=env,
        brand_name=yaml_data["brand_name"],
        website_url=yaml_data["website_url"],
        support_chat_id=int(yaml_data["support_chat_id"]),
        about_text=yaml_data.get("about_text", ""),
        contacts_text=yaml_data.get("contacts_text", ""),
        payment_text=yaml_data.get("payment_text", ""),
        delivery_text=yaml_data.get("delivery_text", ""),
    )
```

Note: `pydantic_settings` is a separate package from `pydantic` starting with pydantic v2. Install separately: `pip install pydantic-settings`. Alternative: use `python-dotenv` directly to load `.env` and `os.environ.get()` for simpler needs — avoids the extra package.

### Pattern 2: SQLite Schema Initialization (aiosqlite)

**What:** Create the database file and schema on bot startup using `CREATE TABLE IF NOT EXISTS`. Two tables for Phase 1: `users` (phone-to-chat_id mapping) and `opt_out` (broadcast opt-out list).

**When to use:** In the `on_startup` handler registered with `dp.startup`.

**Example:**
```python
# bot/db.py
import aiosqlite
from pathlib import Path

DB_PATH = Path("bot_data.db")

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    phone      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_OPT_OUT_TABLE = """
CREATE TABLE IF NOT EXISTS opt_out (
    chat_id    INTEGER PRIMARY KEY,
    opted_out_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

async def init_db() -> None:
    """Create DB file and schema. Safe to call multiple times (IF NOT EXISTS)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_USERS_TABLE)
        await db.execute(CREATE_OPT_OUT_TABLE)
        await db.commit()

async def get_db() -> aiosqlite.Connection:
    """Open a connection. Caller must close it (use as context manager)."""
    return await aiosqlite.connect(DB_PATH)
```

**Important:** aiosqlite connections must be closed. Use `async with aiosqlite.connect(...) as db:` in every operation function, not a long-lived shared connection. SQLite handles concurrent reads fine; writes serialize automatically.

### Pattern 3: Bot Entry Point with Startup Hook

**What:** `bot/__main__.py` initializes the config, creates the Bot and Dispatcher, registers startup handlers, includes routers, and calls `dp.start_polling(bot)`.

**Example:**
```python
# bot/__main__.py
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from bot.config import load_config
from bot.db import init_db
from bot.handlers.common import router as common_router
from bot.services.shopify import ShopifyClient
from bot.services.keycrm import KeyCRMClient

async def main() -> None:
    config = load_config()

    bot = Bot(
        token=config.env.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Dependency injection via dispatcher workflow_data
    dp["config"] = config
    dp["keycrm"] = KeyCRMClient(api_key=config.env.keycrm_api_key)

    if config.env.shopify_api_token:
        dp["shopify"] = ShopifyClient(
            store_url=config.env.shopify_store_url,
            api_token=config.env.shopify_api_token,
        )
    else:
        logger.warning("Shopify credentials not configured — running in KeyCRM-only mode")
        dp["shopify"] = None

    @dp.startup()
    async def on_startup() -> None:
        logger.info("Initializing database...")
        await init_db()
        logger.info("Bot started. Brand: {}", config.brand_name)

    dp.include_router(common_router)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
```

### Pattern 4: KeyCRM Client (VERIFIED live)

**What:** Async httpx client that queries KeyCRM REST API. Phone-based order lookup uses `filter[buyer_phone]` query parameter.

**Critical finding:** KeyCRM stores phone numbers in mixed formats — some with `+380...` and some with `380...` (no plus sign). When filtering, pass the number WITHOUT the `+` prefix (e.g., `380671234567`). The filter does exact match — it will not match `+380671234567` if stored as `380671234567`.

**VERIFIED endpoints:**
- `GET /v1/order?include=buyer,products,status&filter[buyer_phone]={phone}` — returns paginated orders for a buyer's phone, with buyer info, product list, and status embedded
- Rate limit: 60 requests/minute per API key

**Example:**
```python
# bot/services/keycrm.py
import httpx
from loguru import logger
from dataclasses import dataclass

BASE_URL = "https://openapi.keycrm.app/v1"

@dataclass
class KeyCRMOrder:
    id: int
    status_name: str
    grand_total: float
    ordered_at: str
    products: list[dict]
    buyer_name: str

def normalize_phone_for_keycrm(phone: str) -> str:
    """
    KeyCRM stores phones as '380XXXXXXXXX' (without +).
    Strip '+', spaces, and dashes. Return digits only.
    User input '+380671234567' -> '380671234567'
    """
    return phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

class KeyCRMClient:
    def __init__(self, api_key: str) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def get_orders_by_phone(self, phone: str) -> list[KeyCRMOrder]:
        normalized = normalize_phone_for_keycrm(phone)
        params = {
            "include": "buyer,products,status",
            "filter[buyer_phone]": normalized,
            "limit": 50,
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/order",
                    headers=self._headers,
                    params=params,
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()
                return [_parse_order(o) for o in data.get("data", [])]
            except httpx.HTTPError as e:
                logger.error("KeyCRM API error: {}", e)
                return []

def _parse_order(raw: dict) -> KeyCRMOrder:
    status = raw.get("status") or {}
    buyer = raw.get("buyer") or {}
    products = raw.get("products") or []
    return KeyCRMOrder(
        id=raw["id"],
        status_name=status.get("name", "unknown"),
        grand_total=raw.get("grand_total", 0),
        ordered_at=raw.get("ordered_at", ""),
        products=[{"name": p["name"], "qty": p["quantity"]} for p in products],
        buyer_name=buyer.get("full_name", ""),
    )
```

### Pattern 5: Shopify GraphQL Client

**What:** Async httpx client that queries Shopify GraphQL Admin API. Customer lookup by phone, then fetch orders via the customer's `orders` connection.

**Important context:** The Shopify Custom App does not exist yet. The client must handle missing credentials gracefully (`shopify_api_token` is None → client is None → handlers skip Shopify lookup without crashing).

**Shopify API notes (verified from official docs):**
- GraphQL endpoint: `https://{store}.myshopify.com/admin/api/2025-01/graphql.json`
- Auth header: `X-Shopify-Access-Token: {token}`
- Required scopes for Custom App: `read_customers`, `read_orders` (and optionally `read_all_orders` to access orders older than 60 days)
- Phone filter syntax: `query: "phone:+380671234567"` (use WITH `+` for Shopify — opposite of KeyCRM)
- Token is generated once when the Custom App is installed; shown only once in Shopify Admin

**Example:**
```python
# bot/services/shopify.py
import httpx
from loguru import logger
from dataclasses import dataclass

GRAPHQL_QUERY = """
query GetCustomerByPhone($phone: String!) {
  customers(first: 1, query: $phone) {
    edges {
      node {
        id
        displayName
        orders(first: 50, sortKey: CREATED_AT, reverse: true) {
          edges {
            node {
              id
              name
              displayFinancialStatus
              displayFulfillmentStatus
              totalPriceSet {
                shopMoney { amount currencyCode }
              }
              createdAt
              lineItems(first: 10) {
                edges {
                  node { name quantity }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

@dataclass
class ShopifyOrder:
    id: str
    name: str
    financial_status: str
    fulfillment_status: str
    total_price: str
    currency: str
    created_at: str
    line_items: list[dict]

class ShopifyClient:
    def __init__(self, store_url: str, api_token: str) -> None:
        # store_url: "yourstore.myshopify.com"
        self._endpoint = f"https://{store_url}/admin/api/2025-01/graphql.json"
        self._headers = {
            "X-Shopify-Access-Token": api_token,
            "Content-Type": "application/json",
        }

    async def get_orders_by_phone(self, phone: str) -> list[ShopifyOrder]:
        """phone should be in +380... format"""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self._endpoint,
                    headers=self._headers,
                    json={
                        "query": GRAPHQL_QUERY,
                        "variables": {"phone": f"phone:{phone}"},
                    },
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()
                customers = data.get("data", {}).get("customers", {}).get("edges", [])
                if not customers:
                    return []
                orders_edges = customers[0]["node"]["orders"]["edges"]
                return [_parse_shopify_order(e["node"]) for e in orders_edges]
            except httpx.HTTPError as e:
                logger.error("Shopify API error: {}", e)
                return []

def _parse_shopify_order(node: dict) -> ShopifyOrder:
    money = node.get("totalPriceSet", {}).get("shopMoney", {})
    items = [
        {"name": e["node"]["name"], "qty": e["node"]["quantity"]}
        for e in node.get("lineItems", {}).get("edges", [])
    ]
    return ShopifyOrder(
        id=node["id"],
        name=node["name"],
        financial_status=node.get("displayFinancialStatus", ""),
        fulfillment_status=node.get("displayFulfillmentStatus", ""),
        total_price=money.get("amount", "0"),
        currency=money.get("currencyCode", "UAH"),
        created_at=node.get("createdAt", ""),
        line_items=items,
    )
```

### Pattern 6: Ukrainian Text Module

**What:** All Ukrainian strings in `bot/texts.py` as module-level constants. Configurable values (brand name) injected at runtime via `.format()` or f-string.

**Example:**
```python
# bot/texts.py

# Greeting — warm personal tone per CONTEXT.md
GREETING = (
    "Вітаємо у {brand_name}! 🌸\n"
    "Я допоможу вам, покажу та проведу за руку у світ краси."
)

# Fallback/errors — neutral/factual tone
ERR_GENERIC = "Виникла технічна помилка. Спробуйте пізніше."
ERR_API_UNAVAILABLE = "Сервіс тимчасово недоступний. Спробуйте через кілька хвилин."
```

### Anti-Patterns to Avoid

- **Hardcoding strings in handlers:** Every `await message.answer("Вітаємо...")` inside a handler is a violation of CONF-01 and makes copy review impossible. All strings go in `bot/texts.py`.
- **Calling httpx synchronously in async handlers:** Both ShopifyClient and KeyCRMClient use `async with httpx.AsyncClient()` — never use `httpx.get()` (sync) inside an async handler.
- **Long-lived httpx.AsyncClient:** Opening a new `AsyncClient()` per request is correct for this scale. A shared session requires cleanup and adds complexity without performance benefit at low request rates.
- **Storing secrets in config.yaml:** config.yaml is committed to git; `.env` is not. API tokens and bot tokens go in `.env` only.
- **Creating .gitignore after the first commit:** .gitignore must be the very first file committed, before `.env` is ever staged.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| .env loading | Manual `os.environ` reads | python-dotenv + pydantic Settings | Type validation, required field checking, clear error messages on missing keys |
| Async SQLite | Run sqlite3 in a thread | aiosqlite | Designed for this; no thread pool boilerplate |
| HTTP client for APIs | Raw socket/urllib | httpx | Connection pooling, timeout handling, response parsing, async support — all built in |
| YAML config | Custom config format | pyyaml | Standard format; non-technical user can edit config.yaml without learning syntax |

**Key insight:** The "infrastructure" problems in this phase (env loading, async DB, HTTP) all have small, well-maintained libraries that handle exactly the edge cases you'd miss.

---

## Common Pitfalls

### Pitfall 1: Credentials Committed to Git Before .gitignore

**What goes wrong:** The `.env` file (containing the live BOT_TOKEN and KEYCRM_API_KEY) is committed to the repository. Once committed, removing it from history requires `git filter-repo` (complex, destructive) and the token must be revoked immediately.

**Why it happens:** Developer sets up `.env`, tests the bot, runs `git add .` without thinking.

**How to avoid:** `.gitignore` must be the FIRST file committed. Nothing else gets staged until `.gitignore` excludes `.env`. The current state (no `.gitignore` exists yet) makes this the single highest-priority action in Phase 1.

**Warning signs:** `git status` shows `.env` as an untracked file that can be staged.

---

### Pitfall 2: KeyCRM Phone Format Mismatch (VERIFIED from live data)

**What goes wrong:** User enters phone as `+380671234567`. Code passes `+380671234567` to `filter[buyer_phone]`. KeyCRM returns 0 results because the buyer's phone is stored as `380671234567` (without `+`). Every lookup returns "not found" even when the customer has orders.

**Why it happens:** Live inspection of real KeyCRM data showed phones stored in BOTH formats — some entries have `+380...`, others have `380...`. The `filter[buyer_phone]` does exact match — it will NOT match across formats.

**How to avoid:** Normalize the phone before querying: strip `+`, spaces, dashes, parentheses. Pass the result to KeyCRM. Store the normalized form in the SQLite `users` table for consistency.

**Resolution:** The normalization function `normalize_phone_for_keycrm(phone: str) -> str` is documented in the Code Examples section. This must be in `keycrm.py` from the first version.

---

### Pitfall 3: Shopify GraphQL vs REST API — Use GraphQL

**What goes wrong:** Developer installs `ShopifyAPI` (PyPI) SDK and uses REST endpoints. The REST API is deprecated for new apps since October 2024. Phone-based customer lookup on REST has documented reliability issues. Shopify community forums confirm the REST API customer search by phone is unreliable.

**Why it happens:** The PyPI `ShopifyAPI` package is well-known and appears in tutorials.

**How to avoid:** Use Shopify's GraphQL Admin API directly via httpx. The `customers(query: "phone:+380...")` filter is the current, supported approach per official docs. No SDK needed — the entire GraphQL client is ~50 lines.

**Phone format for Shopify:** Unlike KeyCRM, Shopify requires the `+` prefix: `phone:+380671234567`.

---

### Pitfall 4: Shopify Custom App Token — One-Time Display

**What goes wrong:** Admin API token is generated in Shopify Admin when installing the Custom App. Shopify shows it exactly once. If not copied immediately, the app must be deleted and recreated to get a new token.

**Why it happens:** Unfamiliarity with Shopify's Custom App credential flow.

**How to avoid:** When creating the Custom App, copy the token immediately to `.env` as `SHOPIFY_API_TOKEN`. Required scopes: `read_customers`, `read_orders`. Optionally add `read_all_orders` to access orders older than 60 days.

---

### Pitfall 5: Missing Shopify Config at Startup Crashes Bot

**What goes wrong:** Bot code tries to initialize ShopifyClient with `None` values. Raises TypeError or AttributeError on first API call.

**Why it happens:** Shopify credentials aren't set up yet (the Custom App needs to be created). The bot must work without them.

**How to avoid:** `SHOPIFY_API_TOKEN` and `SHOPIFY_STORE_URL` are Optional in the pydantic Settings model. The startup code checks if they are set; if not, it logs a warning and sets `dp["shopify"] = None`. All handlers that call Shopify check `if shopify is None: skip`. This is documented in the entry point pattern above.

---

### Pitfall 6: aiosqlite Connection Left Open

**What goes wrong:** Using a module-level `aiosqlite.connect()` shared across all handlers causes "cannot reuse connection" errors or connection leaks.

**How to avoid:** Every database operation function opens and closes its own connection using `async with aiosqlite.connect(DB_PATH) as db:`. The `init_db()` function (called once on startup) is the only exception — it opens, creates tables, commits, and closes.

---

## Code Examples

Verified patterns from official sources and live API testing:

### KeyCRM Order Lookup (VERIFIED live 2026-03-02)

```python
# Confirmed working against real KeyCRM account
# Phone format: 380671234567 (no + prefix)
# Returns paginated orders with buyer, products, and status embedded

async with httpx.AsyncClient() as client:
    response = await client.get(
        "https://openapi.keycrm.app/v1/order",
        headers={"Authorization": f"Bearer {api_key}"},
        params={
            "include": "buyer,products,status",
            "filter[buyer_phone]": "380986297610",  # no + prefix
            "limit": 50,
        },
        timeout=10.0,
    )
    data = response.json()
    # data["total"] = total order count for this phone
    # data["data"] = list of order objects
    # data["last_page"] = total pages
```

### KeyCRM Order Response Schema (VERIFIED)

```python
# Real response structure from live KeyCRM account
order = {
    "id": 35969,
    "status_id": 1,
    "grand_total": 22464,        # in UAH (kopecks? no — UAH as integer)
    "ordered_at": "2026-03-02T11:33:32.000000Z",
    "payment_status": "not_paid", # or "paid"
    "buyer": {
        "id": 8451,
        "full_name": "...",
        "phone": "380986297610",  # no + prefix in storage
        "email": None,
        "orders_count": 120,
    },
    "status": {
        "id": 1,
        "name": "new",            # status name — needs translation to Ukrainian
        "alias": "new",
    },
    "products": [
        {
            "name": "Product Name",
            "quantity": 2,
            "price": 980,          # UAH
            "price_sold": 882,     # after discount
        }
    ],
    # shipping fields available via include=shipping:
    # shipping_date, shipping_date_actual, tracking_code
}
```

### Shopify GraphQL Customer + Orders (from official docs)

```python
# Source: https://shopify.dev/docs/api/admin-graphql/latest/queries/customers
query = """
query GetCustomerByPhone($phone: String!) {
  customers(first: 1, query: $phone) {
    edges {
      node {
        id
        displayName
        orders(first: 50, sortKey: CREATED_AT, reverse: true) {
          edges {
            node {
              name
              displayFinancialStatus
              displayFulfillmentStatus
              createdAt
              totalPriceSet { shopMoney { amount currencyCode } }
              lineItems(first: 10) {
                edges { node { name quantity } }
              }
            }
          }
        }
      }
    }
  }
}
"""

# Call with: variables={"phone": "phone:+380671234567"}
# Auth header: X-Shopify-Access-Token: {token}
# Endpoint: https://{store}.myshopify.com/admin/api/2025-01/graphql.json
```

### SQLite Schema (Phase 1 scope)

```sql
-- users table: maps Telegram chat_id to verified phone
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    phone      TEXT NOT NULL,           -- normalized: 380XXXXXXXXX format
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- opt_out table: tracks broadcast opt-outs
CREATE TABLE IF NOT EXISTS opt_out (
    chat_id      INTEGER PRIMARY KEY,
    opted_out_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### aiogram 3 Startup Pattern (from installed package + docs)

```python
# Source: aiogram 3.25.0 docs + live package inspection
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

bot = Bot(
    token=config.env.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# Dependency injection — available in all handlers as named parameters
dp["config"] = config
dp["keycrm"] = keycrm_client

@dp.startup()
async def on_startup() -> None:
    await init_db()

dp.include_router(common_router)

# Long-polling — runs until Ctrl+C
await dp.start_polling(bot)
```

### .gitignore (Phase 1 — must be first commit)

```gitignore
# Secrets — never commit
.env

# Python
.venv/
__pycache__/
*.pyc
*.pyo
*.pyd
*.egg-info/
dist/
build/

# IDE
.idea/

# Database
*.db
*.sqlite
*.sqlite3

# OS
.DS_Store
```

### config.yaml Structure

```yaml
brand_name: "Korean Story"
website_url: "https://koreanstory.com.ua"
support_chat_id: 129462784  # Telegram user ID of support person

# Info pages — text shown in Phase 3 menus
about_text: |
  Ми — Korean Story, магазин корейської краси...
contacts_text: |
  Телефон: +38 ...
payment_text: |
  Оплата: Monobank, PrivatBank...
delivery_text: |
  Доставка: Нова Пошта, Укрпошта...
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Shopify REST API for order/customer data | Shopify GraphQL Admin API | October 2024 (Shopify deprecation) | REST SDK (ShopifyAPI PyPI) is no longer recommended; new private/custom apps should use GraphQL |
| aiogram 2.x | aiogram 3.x | aiogram 3.0 released 2023 | Completely different API — router-based, async-first, FSM built-in |
| Python-dotenv + manual os.environ | pydantic-settings BaseSettings | pydantic v2 (2023) | Type-safe config with validation at startup; clear error messages on missing keys |

**Deprecated/outdated:**
- `aiogram.executor.start_polling(dp, bot)`: aiogram 2 pattern. Use `await dp.start_polling(bot)` in aiogram 3.
- `gspread` / Google Sheets: Was in earlier research files (STACK.md etc.) as the data source. Decision was changed to Shopify + KeyCRM APIs + SQLite. Google Sheets is NOT part of this project.
- `ShopifyAPI` PyPI package: The REST-based SDK. Shopify deprecated REST Admin API for new apps; do not use.

---

## Open Questions

1. **KeyCRM status names — translation needed**
   - What we know: KeyCRM status `name` field returns English strings from the real API (e.g., `"new"`, `"pending"`, `"completed"`). Other statuses are unknown until inspected.
   - What's unclear: The full list of status names used in this specific KeyCRM account.
   - Recommendation: In Phase 3, add a small status translation dict in `texts.py`. For Phase 1, log the status names from test orders to know the full set. The Phase 1 /start handler doesn't display orders, so this is not blocking.

2. **Shopify store URL format for GraphQL endpoint**
   - What we know: Endpoint is `https://{store}.myshopify.com/admin/api/2025-01/graphql.json`. The `store` part is the Shopify subdomain.
   - What's unclear: The exact myshopify.com subdomain for this store. User needs to provide this alongside the API token.
   - Recommendation: Add `SHOPIFY_STORE_URL` to `.env` (value: `yourstore.myshopify.com`). Document in `.env.example`.

3. **pydantic-settings as separate install**
   - What we know: In pydantic v2, `BaseSettings` moved to the separate `pydantic-settings` package. aiogram installs `pydantic` but NOT `pydantic-settings`.
   - What's unclear: Whether the project wants the pydantic-settings dependency or prefers simpler dotenv loading.
   - Recommendation: Either install `pydantic-settings` OR use a simpler approach — load `.env` with `python-dotenv` and read `os.environ` directly. Document the chosen approach in `config.py`. The pydantic-settings approach is cleaner but adds one more dependency.

---

## Sources

### Primary (HIGH confidence)
- PyPI live metadata — aiogram 3.25.0 wheel METADATA (verified 2026-03-02): `Requires-Python: <3.15,>=3.10`, Python 3.14 supported
- PyPI live metadata — aiosqlite 0.22.1 (verified 2026-03-02): `Requires-Python: >=3.9`
- PyPI live metadata — httpx 0.28.1, python-dotenv 1.2.2, pyyaml 6.0.3, loguru 0.7.3 (all verified 2026-03-02)
- Dry-run dependency resolution — `pip install --dry-run aiogram==3.25.0 aiosqlite==0.22.1 python-dotenv==1.2.2 pyyaml==6.0.3 httpx==0.28.1 loguru==0.7.3` — no conflicts on Python 3.14.2 (run 2026-03-02)
- KeyCRM live API testing — `GET /v1/order?include=buyer,products,status&filter[buyer_phone]=380986297610` against real account (2026-03-02): filter confirmed working, response schema documented, phone format confirmed (mixed +380/380)
- aiogram 3.25.0 installed package inspection — `dp.startup`, `dp.shutdown`, `dp.workflow_data` confirmed present and working
- Shopify GraphQL Admin API — `customers` query with `phone:` filter: https://shopify.dev/docs/api/admin-graphql/latest/queries/customers (verified 2026-03-02)
- Shopify Custom App token generation: https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/generate-app-access-tokens-admin (verified 2026-03-02)
- aiogram quick start — `dp.start_polling(bot)` pattern: https://docs.aiogram.dev/en/latest/quick_start.html (verified 2026-03-02)

### Secondary (MEDIUM confidence)
- aiogram 3 middleware DI pattern: https://docs.aiogram.dev/en/latest/dispatcher/middlewares.html — `dp["key"] = value` injection into handlers confirmed via live package test
- aiogram 3 Router pattern: https://docs.aiogram.dev/en/latest/dispatcher/router.html — `dp.include_router()` confirmed
- Shopify REST API deprecation (October 2024 for public apps): https://shopify.dev/docs/api/admin-rest/latest/resources/customer — training knowledge, consistent with official docs direction

### Tertiary (LOW confidence)
- KeyCRM available `include` options: discovered from live API error message (`"Allowed include(s) are: attachments, buyer, products, manager, status, payments, expenses, marketing, shipping..."`)  — HIGH confidence for this specific account, but the full API spec is not publicly documented
- KeyCRM API rate limit (60 req/min): from official API script setup page — MEDIUM confidence

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified live via PyPI and dry-run install on Python 3.14.2
- KeyCRM API: HIGH — tested live against real account; phone filter verified; response schema documented from real data
- Shopify GraphQL API: MEDIUM-HIGH — query syntax verified from official docs; endpoint not tested (Custom App not yet created)
- Architecture patterns: HIGH — aiogram startup, DI, and router patterns verified from installed package
- Phone normalization: HIGH — discovered from live data inspection (mixed format confirmed)
- Pitfalls: HIGH for credential safety and phone format; MEDIUM for Shopify REST deprecation

**Research date:** 2026-03-02
**Valid until:** 2026-06-01 (90 days — all stack components are stable; re-verify Shopify GraphQL API version before Phase 3)
