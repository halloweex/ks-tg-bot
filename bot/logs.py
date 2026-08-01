"""Log configuration: one sink, no locals in tracebacks, phone numbers masked.

loguru is unconfigured by default, and its defaults are wrong for production:
level DEBUG, `backtrace=True` and — the expensive one — `diagnose=True`, which
prints the value of every local variable in every frame of every traceback.
Any unhandled exception anywhere below a frame holding a customer's number
therefore wrote that number to the log, on top of the places that log it on
purpose. Docker keeps up to 50 MB of that on disk (docker-compose.yml:17-21).

Masking is done in the sink rather than in a `logger.configure(patcher=...)`
because a patcher only reaches `record["message"]`. The traceback is rendered
from `record["exception"]` afterwards, and that is exactly where the leak was:
httpx puts the request URL into HTTPStatusError, and the KeyCRM order lookup
carries the number in `filter[buyer_phone]`. A sink sees the final string.
"""
from __future__ import annotations

import hashlib
import re
import sys
import threading
import traceback

from loguru import logger

# Ukrainian mobile numbers in every shape they reach a log line: +380671234567,
# 380671234567, 0671234567. The guards keep the pattern off longer digit runs,
# so a Telegram chat id (9-10 digits, no leading 0 after an optional 38) and a
# Shopify order id (14 digits) survive — chat_id is the identifier the code
# logs deliberately, and masking it would cost the ability to follow a session.
_PHONE_RE = re.compile(r"(?<![\d+])\+?(?:38)?0\d{9}(?!\d)")


def _mask(match: re.Match[str]) -> str:
    """Replace a number with a stable short digest of it.

    Stable, so two lines about the same customer can still be tied together;
    a digest, so the number cannot be read back out.
    """
    digits = re.sub(r"\D", "", match.group(0))[-10:]
    return f"<phone:{hashlib.sha256(digits.encode()).hexdigest()[:6]}>"


def _masked_stderr(message: object) -> None:
    sys.stderr.write(_PHONE_RE.sub(_mask, str(message)))


def _masked_excepthook(exc_type, exc, tb) -> None:
    """Route an uncaught exception through the same masker as the log.

    Without this the fix looks complete and is not: an exception nobody caught
    never reaches loguru at all — the interpreter hands it to sys.excepthook,
    which writes the traceback straight to stderr. Verified: a RuntimeError
    carrying a phone number printed it in full with the sink installed.

    That path is not hypothetical. It is how a failure inside load_config()
    surfaces, and pydantic-settings puts the offending value into the error.
    """
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(_PHONE_RE.sub(_mask, text))


def _masked_thread_excepthook(args) -> None:
    """Same, for threads — aiosqlite runs every connection on one."""
    if args.exc_type is SystemExit:
        return
    _masked_excepthook(args.exc_type, args.exc_value, args.exc_traceback)


def setup_logging(level: str = "INFO") -> None:
    """Replace loguru's default handler. Idempotent — safe to call again.

    Called twice on startup: once before the config is read, so a crash inside
    load_config() cannot print the environment it was reading, and once after,
    to apply LOG_LEVEL.
    """
    logger.remove()
    logger.add(
        _masked_stderr,
        level=level.upper(),
        backtrace=False,
        diagnose=False,
        colorize=False,
    )
    sys.excepthook = _masked_excepthook
    threading.excepthook = _masked_thread_excepthook
