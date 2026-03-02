---
phase: 01-foundation
plan: 01
subsystem: infra
tags: [python, aiogram, pydantic-settings, pyyaml, configuration, ukrainian]

# Dependency graph
requires: []
provides:
  - .gitignore with secrets exclusion (protects .env from git)
  - requirements.txt with 7 pinned dependencies
  - bot/ package skeleton (handlers/, services/ subdirs)
  - EnvSettings (pydantic-settings) loading BOT_TOKEN, KEYCRM_API_KEY, admin IDs from .env
  - AppConfig dataclass combining .env secrets + config.yaml settings
  - load_config() function returning AppConfig
  - config.yaml with Korean Story brand settings and info page placeholders
  - bot/texts.py with all Ukrainian UI strings as module-level constants
affects: [01-02, 01-03, 02-onboarding, 03-features, 04-production]

# Tech tracking
tech-stack:
  added:
    - aiogram==3.25.0 (Telegram bot framework, async)
    - aiosqlite==0.22.1 (async SQLite)
    - python-dotenv==1.2.2 (env file loading)
    - pyyaml==6.0.3 (config.yaml parsing)
    - httpx==0.28.1 (async HTTP client)
    - loguru==0.7.3 (structured logging)
    - pydantic-settings>=2.0.0 (BaseSettings for .env loading)
  patterns:
    - Two-file config: .env for secrets (pydantic-settings), config.yaml for non-secrets (yaml.safe_load)
    - All user-facing strings in bot/texts.py as module-level constants (no string literals in handlers)
    - admin_ids as comma-separated env string parsed to list[int] via property

key-files:
  created:
    - .gitignore
    - requirements.txt
    - .env.example
    - config.yaml
    - bot/__init__.py
    - bot/handlers/__init__.py
    - bot/services/__init__.py
    - bot/config.py
    - bot/texts.py
  modified: []

key-decisions:
  - "pydantic-settings BaseSettings for .env loading (not pydantic v1 BaseSettings)"
  - "Two-file config pattern: .env secrets + config.yaml non-secrets"
  - "All Ukrainian strings centralized in bot/texts.py as module-level constants"
  - ".env untracked from git (was previously committed before .gitignore existed)"

patterns-established:
  - "Config pattern: EnvSettings for secrets, AppConfig dataclass for combined config"
  - "Text pattern: All UI strings as module-level constants in bot/texts.py, imported by handlers"
  - "Format placeholder pattern: GREETING uses {brand_name} filled at runtime via .format()"

requirements-completed: [CONF-01, CONF-02, CONF-03, CONF-04, CONF-05]

# Metrics
duration: 2min
completed: 2026-03-02
---

# Phase 1 Plan 01: Project Skeleton and Configuration Summary

**Project skeleton with pydantic-settings two-file config (.env + config.yaml) and centralized Ukrainian string constants in bot/texts.py**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-02T16:56:35Z
- **Completed:** 2026-03-02T16:58:46Z
- **Tasks:** 3
- **Files modified:** 9 created, 0 modified

## Accomplishments
- Created .gitignore (first, before any code) excluding .env, .venv/, IDE files, db files
- Established two-file config system: EnvSettings (pydantic-settings) for .env secrets, load_config() returns AppConfig combining both sources
- Centralized all Ukrainian UI strings in bot/texts.py: greeting, 4 error messages, 7 menu buttons, 3 status messages
- Untracked .env from git history (it was committed before .gitignore existed)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create .gitignore, requirements.txt, and bot package skeleton** - `e74c089` (chore)
2. **Task 2: Create config.py with two-file config loading** - `6861cb5` (feat)
3. **Task 3: Create texts.py with all Ukrainian UI strings** - `8298bef` (feat)

**Plan metadata:** TBD (docs: complete plan)

## Files Created/Modified
- `.gitignore` - Excludes .env, .venv/, __pycache__/, .idea/, db files, .planning/research/
- `requirements.txt` - 7 pinned dependencies: aiogram, aiosqlite, python-dotenv, pyyaml, httpx, loguru, pydantic-settings
- `.env.example` - Template with BOT_TOKEN, BOT_USERNAME, KEYCRM_API_KEY, SHOPIFY_*, ADMIN_USER_IDS
- `config.yaml` - Korean Story brand settings and placeholder info page content
- `bot/__init__.py` - Package marker
- `bot/handlers/__init__.py` - Package marker
- `bot/services/__init__.py` - Package marker
- `bot/config.py` - EnvSettings, AppConfig dataclass, load_config() function
- `bot/texts.py` - All Ukrainian UI strings as module-level constants

## Decisions Made
- Used pydantic-settings (separate package) not pydantic v1 BaseSettings — pydantic v2 moved BaseSettings to pydantic-settings package
- Two-file config approach: secrets in .env (never committed), non-secrets in config.yaml (safe to commit)
- Ukrainian strings as Python module-level constants in bot/texts.py — developer-maintained, type-checked, importable

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed missing pyyaml and pydantic-settings dependencies**
- **Found during:** Task 2 (config.py implementation)
- **Issue:** pyyaml and pydantic-settings not installed in .venv — `import yaml` and `from pydantic_settings import BaseSettings` both failed
- **Fix:** Ran `.venv/bin/pip install pyyaml==6.0.3` and `.venv/bin/pip install pydantic-settings` (installs 2.13.1, satisfies >=2.0.0 requirement)
- **Files modified:** None (virtual environment only, requirements.txt already listed them)
- **Verification:** `from bot.config import load_config; load_config()` runs without error
- **Committed in:** `6861cb5` (Task 2 commit)

**2. [Rule 1 - Bug] Untracked .env from git index**
- **Found during:** Overall verification (after all 3 tasks)
- **Issue:** .env was previously committed to git before .gitignore existed — git status showed it as tracked, violating the must-have "Credentials (.env) are excluded from git tracking"
- **Fix:** Ran `git rm --cached .env` to remove from git index while preserving local file; .gitignore now prevents future tracking
- **Files modified:** git index only (.env file preserved locally)
- **Verification:** `git status` no longer shows .env; `git ls-files .env` returns empty; file still exists locally for bot to read
- **Committed in:** `f9011b2` (separate fix commit)

---

**Total deviations:** 2 auto-fixed (1 blocking dependency install, 1 bug: secret in git)
**Impact on plan:** Both fixes required for correctness and security. No scope creep.

## Issues Encountered
- .env was already tracked in git before this plan ran — untracked via `git rm --cached .env` as a separate commit

## User Setup Required
None - no external service configuration required. The .env file already exists locally with real credentials.

## Next Phase Readiness
- Config system ready for use by all subsequent plans (bot/config.py, config.yaml)
- Ukrainian strings module ready for handler imports (bot/texts.py)
- Bot package structure ready for handler and service modules
- Plan 01-02 can proceed immediately (database setup)

## Self-Check: PASSED

All 9 created files verified present on disk. All 4 commits (e74c089, 6861cb5, 8298bef, f9011b2) confirmed in git log.

---
*Phase: 01-foundation*
*Completed: 2026-03-02*
