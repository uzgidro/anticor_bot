"""Text helpers: Telegram 4096-char splitting and HTML escaping."""
from __future__ import annotations

import html

TELEGRAM_MAX = 4096
MAX_INPUT_LEN = 4000  # leave headroom for labels when echoing user text


def escape(text: str) -> str:
    """Escape user-supplied text for HTML parse mode (prevents markup injection)."""
    return html.escape(text, quote=False)


def split_text(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Split text into <=limit chunks, preferring newline then space boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
