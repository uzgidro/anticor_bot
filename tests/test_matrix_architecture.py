"""Dependency direction: handlers and the Matrix bridge meet only in services."""
from pathlib import Path


def _sources(folder: str) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in Path(folder).glob("*.py")}


def test_matrix_package_never_imports_handlers():
    for name, src in _sources("bot/matrix").items():
        assert "bot.handlers" not in src, name


def test_handlers_never_import_matrix():
    for name, src in _sources("bot/handlers").items():
        assert "bot.matrix" not in src, name
