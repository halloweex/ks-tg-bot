"""No customer phone number reaches the log, by any of the four routes.

Guards commits 2a61ece (sink + diagnose=False), 4923aeb (excepthook) and
c38001f (salt).
"""
from __future__ import annotations

import asyncio
import re
import sys

import pytest
from loguru import logger

from bot.logs import setup_logging
from bot.tasks import drain, spawn

PHONE = "380660146763"
CHAT_ID = 129462784
SHOPIFY_ID = "13025577828684"


@pytest.fixture()
def logging_configured():
    """Install the masked sink; capture is pytest's job, not ours.

    Monkeypatching sys.stderr does not work here: pytest suspends and resumes
    its own capture around the test body, so a replacement installed in a
    fixture survives only until the body starts. capsys is the supported way.
    """
    setup_logging("INFO", phone_salt="test-salt")
    yield
    setup_logging()


async def _scenario() -> None:
    # 1. logged on purpose — core/adapters/keycrm/client.py
    logger.warning("KeyCRM rate limit hit (429) for phone {}", PHONE)
    # 2. inside an exception message together with the URL — httpx does this
    logger.error(
        "KeyCRM HTTP error for phone {}: {}",
        PHONE,
        f"Server error '500' for url 'https://openapi.keycrm.app/v1/order"
        f"?filter%5Bbuyer_phone%5D={PHONE}&limit=50'",
    )

    # 3. traceback of a failed background task — bot/tasks.py:34
    async def failing(chat_id: int, phone: str) -> None:
        secret_local = phone  # noqa: F841 — the point is that it stays unprinted
        raise RuntimeError(f"boom while fetching for +{phone}")

    spawn(failing(CHAT_ID, PHONE), name="refresh_orders")
    await asyncio.sleep(0.05)
    await drain(timeout=2.0)

    # 4. three spellings of one number
    logger.info("formats: +{p} / {p} / 0{tail}", p=PHONE, tail=PHONE[3:])
    # 5. what must survive untouched
    logger.info("chat_id={} shopify_order={}", CHAT_ID, SHOPIFY_ID)


@pytest.fixture()
def log_output(logging_configured, capsys) -> str:
    asyncio.run(_scenario())
    return capsys.readouterr().err


@pytest.mark.parametrize(
    "needle",
    [PHONE, PHONE[1:], "0660146763", "660146763"],
    ids=["full", "no-plus", "local", "no-country-code"],
)
def test_no_spelling_of_the_number_survives(log_output, needle):
    assert needle not in log_output


def test_one_stable_mask_for_one_number(log_output):
    masks = set(re.findall(r"<phone:[0-9a-f]{6}>", log_output))
    assert len(masks) == 1, f"same number produced different digests: {masks}"
    assert log_output.count("<phone:") >= 5


@pytest.mark.parametrize("keep", [str(CHAT_ID), SHOPIFY_ID], ids=["chat_id", "shopify_id"])
def test_identifiers_that_must_not_be_masked(log_output, keep):
    assert keep in log_output


def test_no_local_variables_in_tracebacks(log_output):
    """diagnose=False. The ordinary traceback stays — it is how you find the line."""
    assert "secret_local" not in log_output
    assert "└─" not in log_output, "loguru's extended backtrace is off"
    assert "Traceback (most recent call last)" in log_output


def test_uncaught_exception_is_masked_too(logging_configured, capsys):
    """sys.excepthook, not loguru: an uncaught exception never reaches the sink."""
    try:
        raise RuntimeError(f"config error for +{PHONE}")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    out = capsys.readouterr().err
    assert PHONE not in out
    assert "<phone:" in out


def test_salt_changes_the_digest(capsys):
    def digest(salt: str) -> str:
        setup_logging("INFO", phone_salt=salt)
        from bot.logs import _masked_stderr

        _masked_stderr(f"phone {PHONE}")
        return capsys.readouterr().err.strip()

    assert digest("one") == digest("one"), "digest must be stable for one salt"
    assert digest("one") != digest("two"), "salt must key the digest"
