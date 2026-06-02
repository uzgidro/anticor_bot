"""i18n completeness: every message key (and attribute) must exist in all locales.

Also verifies the FluentRuntimeCore can load each locale and that explicit-locale
lookup works (used for cross-user messages — replying to a citizen in *their*
language, not the responder's).
"""
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).resolve().parent.parent / "bot" / "locales"
LOCALES = ["ru", "kaa", "uz_cyrl", "uz_latn", "en"]


def _parse_keys(ftl_path: Path) -> set[str]:
    """Return the set of message ids and ``id.attr`` names defined in an .ftl."""
    from fluent.syntax import FluentParser
    from fluent.syntax import ast as fast

    resource = FluentParser().parse(ftl_path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for entry in resource.body:
        if isinstance(entry, fast.Message):
            name = entry.id.name
            keys.add(name)
            for attr in entry.attributes:
                keys.add(f"{name}.{attr.id.name}")
    return keys


def test_all_locales_present():
    for loc in LOCALES:
        assert (LOCALES_DIR / loc / "LC_MESSAGES" / "bot.ftl").exists(), f"missing {loc}"


def test_locales_have_identical_keys():
    per_locale = {
        loc: _parse_keys(LOCALES_DIR / loc / "LC_MESSAGES" / "bot.ftl") for loc in LOCALES
    }
    reference = per_locale["ru"]
    assert reference, "ru locale has no keys"
    for loc in LOCALES:
        missing = reference - per_locale[loc]
        extra = per_locale[loc] - reference
        assert not missing, f"{loc} missing keys: {sorted(missing)}"
        assert not extra, f"{loc} has extra keys: {sorted(extra)}"


@pytest.mark.asyncio
async def test_core_loads_and_explicit_locale_lookup():
    from aiogram_i18n.cores.fluent_runtime_core import FluentRuntimeCore

    core = FluentRuntimeCore(path=str(LOCALES_DIR / "{locale}" / "LC_MESSAGES"))
    await core.startup()
    # Explicit-locale lookup is how we render messages for *other* users.
    for loc in LOCALES:
        text = core.get("btn-cancel", loc)
        assert isinstance(text, str) and text
