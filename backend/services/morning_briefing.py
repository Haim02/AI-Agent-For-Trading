"""Daily morning briefing (14:00 Israel = 11:00 UTC).

Scans Haim's watchlist, runs each ticker through the StrategySelector, and
delivers a Hebrew Telegram digest of actionable trade setups.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from analytics.market_structure import MarketStructureAnalyzer
from analytics.strategy_selector import StrategySelector
from db.connection import get_db
from services.telegram_service import TelegramService, TelegramServiceError
from utils.time_helper import ISRAEL_TZ, now_israel

logger = logging.getLogger(__name__)


class MorningBriefing:
    """Builds and sends the daily trade-recommendation digest."""

    WATCHLIST = [
        "SPY", "QQQ", "IWM",
        "NVDA", "TSLA", "META",
        "AAPL", "MSFT",
        "GLD", "TLT",
    ]

    def __init__(self, telegram: Optional[TelegramService] = None) -> None:
        self.selector = StrategySelector()
        self.analyzer = MarketStructureAnalyzer()
        if telegram is not None:
            self._telegram: Optional[TelegramService] = telegram
        else:
            try:
                self._telegram = TelegramService()
            except TelegramServiceError as exc:
                logger.warning("Telegram disabled in MorningBriefing: %s", exc)
                self._telegram = None

    # ───────────────────────── public ─────────────────────────

    async def generate_daily_briefing(self) -> list[dict[str, Any]]:
        now = now_israel()
        date_str = now.strftime("%d/%m/%Y %H:%M")
        logger.info("📊 יוצר דוח יומי: %s", date_str)

        # VIX is fetched once and cached by the selector for the rest of the run.
        recommendations: list[dict[str, Any]] = []
        for ticker in self.WATCHLIST:
            try:
                rec = await self._analyze_ticker(ticker)
            except Exception:  # noqa: BLE001
                logger.exception("Morning briefing: %s failed", ticker)
                continue
            if rec.get("strategy") != "do_nothing":
                recommendations.append(rec)

        recommendations.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        briefing = self._format_briefing(recommendations, date_str)

        if self._telegram is not None:
            try:
                await self._telegram.send_alert(
                    title="📊 דוח יומי - המלצות מסחר",
                    body=briefing,
                    urgency="high",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Morning briefing: telegram send failed")

        await self._save_briefing(recommendations, date_str)
        return recommendations

    # ───────────────────────── internals ─────────────────────────

    async def _analyze_ticker(self, ticker: str) -> dict[str, Any]:
        report = await self.analyzer.get_daily_report(ticker)
        gex = report.get("gex") or {}
        flow = report.get("flow") or {}
        if "error" in gex or "error" in flow:
            return {"ticker": ticker, "strategy": "do_nothing"}

        iv_rank = await self.selector._get_iv_rank(ticker)
        vix = await self.selector._get_vix()

        return await self.selector.select_strategy(
            ticker=ticker,
            gex_data=gex,
            flow_data=flow,
            iv_rank=iv_rank,
            vix=vix,
        )

    def _format_briefing(self, recommendations: list[dict], date_str: str) -> str:
        if not recommendations:
            return (
                f"דוח יומי – {date_str}\n\n"
                "אין המלצות היום.\n"
                "תנאים לא מתאימים למסחר."
            )

        parts: list[str] = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📊 דוח יומי – המלצות מסחר",
            f"🕐 {date_str}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        for i, rec in enumerate(recommendations[:5], start=1):
            confidence = rec.get("confidence", 0)
            emoji = "🟢🟢" if confidence > 0.90 else "🟢" if confidence > 0.80 else "🟡"
            parts.append(
                f"\n{i}. {emoji} {rec.get('ticker', '?')} – "
                f"{str(rec.get('strategy', '?')).upper()}\n\n"
                f"   📋 {rec.get('recommendation', '')}\n\n"
                f"   💭 {rec.get('reasoning', '')}"
            )

            strikes = rec.get("strikes") or {}
            if strikes:
                strike_lines = ["   🎯 Strikes:"]
                for key, val in strikes.items():
                    if key in {"type", "width", "call_width", "put_width"}:
                        continue
                    strike_lines.append(f"      {key}: ${val}")
                parts.append("\n".join(strike_lines))

            actions = rec.get("action_items") or []
            if actions:
                action_lines = ["   ✅ צעדים:"]
                for action in actions[:6]:
                    action_lines.append(f"      • {action}")
                parts.append("\n".join(action_lines))

        parts.append(
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 זכור:\n"
            "- בדוק VIX לפני כניסה\n"
            "- בדוק כל Strike בפלטפורמה\n"
            "- תמיד שמור Stop Loss\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return "\n".join(parts)

    async def _save_briefing(self, recommendations: list[dict], date_str: str) -> None:
        try:
            db = get_db()
            await db.morning_briefings.insert_one(
                {
                    "date": datetime.utcnow(),
                    "date_israel": date_str,
                    "recommendations": _jsonable(recommendations),
                    "count": len(recommendations),
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("Morning briefing: db insert failed")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


__all__ = ["MorningBriefing"]
