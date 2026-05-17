from __future__ import annotations

import re


def clean_response(text: str) -> str:
    """Strip every Markdown control character before sending to Telegram.

    Telegram's parsers (Markdown / MarkdownV2 / HTML) all bite on a stray ``*``
    or ``_``. We just rip the formatting out and send plain text.
    """
    if not text:
        return "מצטער, נסה שוב."

    text = re.sub(r"#{1,6}\s*", "", text)
    text = text.replace("**", "").replace("*", "")
    text = re.sub(r"-{2,}", "", text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"_{1,2}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
