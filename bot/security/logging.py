"""PII-safe logging setup.

For an anti-corruption channel, logs must never contain submission text, names,
phones, or tg_ids (a log leak would deanonymize complainants). This module:

* installs a redacting filter that scrubs obvious PII patterns from log records,
* exposes ``setup_logging`` to configure the root logger.

Handlers should log identifiers like ``public_id`` and ``update_id`` only — never
the update body. The filter is a backstop, not a substitute for discipline.
"""
from __future__ import annotations

import logging
import re

# Phone-like: a leading + or contains separators (spaces/dashes/parens).
_PHONE_RE = re.compile(r"\+\d[\d\-\s()]{5,}\d|\d[\d\-\s()]{2,}[-\s()][\d\-\s()]*\d")
# Bare long digit runs (tg_ids are 6-12+ digits).
_LONG_DIGITS_RE = re.compile(r"\b\d{6,}\b")


def redact(text: str) -> str:
    # Phones first (they may contain separators), then any remaining long ids.
    text = _PHONE_RE.sub("[redacted-phone]", text)
    text = _LONG_DIGITS_RE.sub("[redacted-id]", text)
    return text


class PiiRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = redact(msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(*, debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.addFilter(PiiRedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # aiogram logs full updates at DEBUG — never allow that in this bot.
    logging.getLogger("aiogram.event").setLevel(max(level, logging.INFO))
