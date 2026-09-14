"""setup_logging: third-party loggers that would flood the log or leak
identifiers stay at WARNING regardless of the app level."""
import logging

from bot.security.logging import setup_logging


def test_noisy_libraries_are_capped_at_warning():
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    try:
        setup_logging(debug=True)
        assert root.level == logging.DEBUG
        # nio logs every room state event (with member ids) at INFO.
        assert logging.getLogger("nio").getEffectiveLevel() == logging.WARNING
        assert logging.getLogger("sqlalchemy.engine").getEffectiveLevel() == logging.WARNING
        assert logging.getLogger("aiogram.event").getEffectiveLevel() == logging.INFO
    finally:
        root.setLevel(saved[0])
        root.handlers[:] = saved[1]
