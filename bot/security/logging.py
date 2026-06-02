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

# Phone-like: international (+998...) or a separated run with >=7 digits total.
# Kept deliberately specific so ticket numbers (OBR-2026-0001) and dates
# (2026-06-02) are NOT mangled.
_PHONE_RE = re.compile(r"\+\d[\d\-\s()]{6,}\d")
# Bare long digit runs (tg_ids are 6-12+ digits). \b ensures we don't touch the
# digits inside OBR-2026-0001 (preceded by a letter/dash, not a word boundary
# start) — but 2026 alone is 4 digits and below the {7,} threshold anyway.
_LONG_DIGITS_RE = re.compile(r"(?<![\d-])\d{7,}(?![\d-])")


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
            # Fail safe: if we can't format/inspect the message, drop it rather
            # than risk emitting un-redacted PII.
            record.msg = "[log record suppressed: unformattable]"
            record.args = ()
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
    # SQLAlchemy echo would log bound params (submission text/name/phone) — keep
    # it at WARNING regardless of debug so PII never reaches logs via SQL.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
