"""English-to-Hebrew translation helper backed by Claude Haiku.

Cheap and async-native. Skipped if the text already contains Hebrew characters
or ANTHROPIC_API_KEY is missing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_HEBREW_RANGE = (0x0590, 0x05EA)


def _has_hebrew(text: str) -> bool:
    return any(_HEBREW_RANGE[0] <= ord(c) <= _HEBREW_RANGE[1] for c in text)


async def translate_to_hebrew(text: str) -> str:
    if not text or _has_hebrew(text):
        return text or ""

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return text

    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "תרגם לעברית בקצרה, רק התרגום:\n\n" + text
                    ),
                }
            ],
        )
        chunks: list[str] = []
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "text":
                chunks.append(getattr(block, "text", ""))
        translated = "".join(chunks).strip()
        return translated or text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Translate failed: %s", exc)
        return text


__all__ = ["translate_to_hebrew"]
