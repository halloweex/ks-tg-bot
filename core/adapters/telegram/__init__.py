"""The Telegram side of the shop, for the parts of it that are not the bot.

This package is the one place in `core` allowed to import aiogram (see
docs/architecture.md v4 §3 and the import-linter contract that names the other
adapters rather than the whole package): what lives here is a client for
Telegram's API, in the same sense that core.adapters.keycrm is a client for
the CRM's.
"""
