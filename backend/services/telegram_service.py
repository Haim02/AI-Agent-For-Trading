import logging
import os
from typing import Any, Optional

from telegram import Bot
from telegram.error import TelegramError

from utils.text_clean import clean_response

logger = logging.getLogger(__name__)

URGENCY_EMOJI = {
    "high": "🔴",
    "medium": "🟡",
    "info": "🟢",
}


class TelegramServiceError(RuntimeError):
    """Raised when Telegram credentials are missing or a send fails fatally."""


class TelegramService:
    """One-way sender used by alerts and the scheduler."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        self.token = token or os.getenv("TELEGRAM_TOKEN")
        chat = chat_id or os.getenv("TELEGRAM_CHAT_ID")

        if not self.token:
            raise TelegramServiceError("TELEGRAM_TOKEN is not set")
        if not chat:
            raise TelegramServiceError("TELEGRAM_CHAT_ID is not set")

        try:
            self.chat_id: int | str = int(chat)
        except ValueError:
            self.chat_id = chat

        self._bot = Bot(token=self.token)

    async def send_message(self, text: str) -> bool:
        try:
            await self._bot.send_message(
                chat_id=self.chat_id,
                text=clean_response(text),
                disable_web_page_preview=True,
            )
            return True
        except TelegramError as exc:
            logger.error("Telegram send_message failed: %s", exc)
            return False

    async def send_alert(self, title: str, body: str, urgency: str = "info") -> bool:
        emoji = URGENCY_EMOJI.get(urgency.lower(), URGENCY_EMOJI["info"])
        body = clean_response(body)
        text = f"{emoji} {title}\n\n{body}"
        return await self.send_message(text)

    async def send_market_summary(self, summary: dict[str, Any]) -> bool:
        date = summary.get("date", "—")
        daily_pnl = summary.get("daily_pnl", 0.0)
        open_positions = summary.get("open_positions", 0)
        closed_positions = summary.get("closed_positions", 0)
        total_realized = summary.get("total_realized_pnl", 0.0)
        notes = summary.get("notes") or summary.get("agent_summary") or ""

        pnl_emoji = "🟢" if daily_pnl >= 0 else "🔴"
        lines = [
            f"📊 סיכום יומי — {date}",
            "",
            f"{pnl_emoji} P&L יומי: ${daily_pnl:,.2f}",
            f"💼 פוזיציות פתוחות: {open_positions}",
            f"✅ פוזיציות סגורות: {closed_positions}",
            f"💰 רווח מימוש מצטבר: ${total_realized:,.2f}",
        ]
        if notes:
            lines.extend(["", "📝 הערות:", notes])

        return await self.send_message("\n".join(lines))
