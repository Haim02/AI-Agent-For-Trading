"""Intraday DEX (Delta Exposure) support/resistance monitor.

Polls FlashAlpha's DEX-by-strike endpoint during the US session, extracts the
dominant delta walls around spot, and alerts Haim on Telegram when a new
support/resistance level forms or an existing one moves meaningfully.
Snapshots are persisted to Mongo (``dex_levels``) so levels survive restarts
and can be charted later.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from db.connection import get_db
from services.telegram_service import TelegramService, TelegramServiceError

logger = logging.getLogger(__name__)

# A level has to move by more than this (in % of spot) to count as "new".
LEVEL_SHIFT_THRESHOLD_PCT = 0.15


def _extract_strike_rows(payload: dict[str, Any]) -> list[dict[str, float]]:
    """Normalize the FlashAlpha DEX payload into [{strike, dex}, ...]."""
    rows: list[dict[str, float]] = []
    for row in payload.get("strikes") or payload.get("data") or []:
        strike = row.get("strike")
        dex = row.get("net_dex", row.get("dex", row.get("net")))
        if strike is None or dex is None:
            continue
        try:
            rows.append({"strike": float(strike), "dex": float(dex)})
        except (TypeError, ValueError):
            continue
    return rows


def _find_walls(
    rows: list[dict[str, float]], spot: float
) -> tuple[Optional[dict], Optional[dict]]:
    """Return (support, resistance): the largest |DEX| strikes below/above spot.

    Only strikes within ±5% of spot matter for 0DTE decision-making.
    """
    if not rows or spot <= 0:
        return None, None
    near = [r for r in rows if abs(r["strike"] - spot) / spot <= 0.05]
    below = [r for r in near if r["strike"] < spot]
    above = [r for r in near if r["strike"] > spot]
    support = max(below, key=lambda r: abs(r["dex"]), default=None)
    resistance = max(above, key=lambda r: abs(r["dex"]), default=None)
    return support, resistance


class DexMonitor:
    def __init__(self) -> None:
        self.db = get_db()
        try:
            self._telegram: Optional[TelegramService] = TelegramService()
        except TelegramServiceError as exc:
            logger.warning("Telegram disabled in DexMonitor: %s", exc)
            self._telegram = None

    async def get_dex_levels(self, ticker: str = "SPX") -> dict[str, Any]:
        """Current DEX walls for a ticker. Used by the /dex Telegram command."""
        from tools.flashalpha_tool import FlashAlphaTool

        fa = FlashAlphaTool()
        payload = await fa.get_dex(ticker)
        if not payload or "error" in (payload or {}):
            return {"error": (payload or {}).get("error", "no_data"), "ticker": ticker}

        spot = float(
            payload.get("underlying_price") or payload.get("spot") or 0
        )
        if spot <= 0:
            quote = await fa.get_stock_quote(ticker)
            spot = float((quote or {}).get("price") or 0)

        rows = _extract_strike_rows(payload)
        support, resistance = _find_walls(rows, spot)
        return {
            "ticker": ticker,
            "spot": spot,
            "support": support,
            "resistance": resistance,
            "timestamp": datetime.utcnow(),
        }

    def format_hebrew(self, levels: dict[str, Any]) -> str:
        if "error" in levels:
            return (
                f"⚠️ אין נתוני DEX ל-{levels.get('ticker', '?')} כרגע "
                f"({levels['error']})"
            )
        spot = levels.get("spot") or 0
        support = levels.get("support")
        resistance = levels.get("resistance")
        lines = [
            f"🧲 רמות Delta (DEX) – {levels.get('ticker', '?')}",
            f"💰 ספוט: ${spot:,.2f}",
            "",
        ]
        if resistance:
            dist = (resistance["strike"] - spot) / spot * 100 if spot else 0
            lines.append(
                f"🔴 התנגדות Delta: ${resistance['strike']:,.0f} ({dist:+.2f}%)"
            )
        else:
            lines.append("🔴 התנגדות Delta: לא זוהתה בטווח ±5%")
        if support:
            dist = (support["strike"] - spot) / spot * 100 if spot else 0
            lines.append(
                f"🟢 תמיכה Delta: ${support['strike']:,.0f} ({dist:+.2f}%)"
            )
        else:
            lines.append("🟢 תמיכה Delta: לא זוהתה בטווח ±5%")
        return "\n".join(lines)

    async def check_and_alert(self, ticker: str = "SPX") -> None:
        """Scheduler entry point – alert only when a wall is new or moved."""
        levels = await self.get_dex_levels(ticker)
        if "error" in levels:
            logger.debug("DexMonitor: no data for %s (%s)", ticker, levels["error"])
            return

        spot = levels.get("spot") or 0
        prev = await self.db.dex_levels.find_one(
            {"ticker": ticker}, sort=[("timestamp", -1)]
        )

        changes: list[str] = []
        for side, label, emoji in (
            ("support", "תמיכה", "🟢"),
            ("resistance", "התנגדות", "🔴"),
        ):
            current = levels.get(side)
            if not current or spot <= 0:
                continue
            prev_level = (prev or {}).get(side) or {}
            prev_strike = prev_level.get("strike")
            if prev_strike is None or (
                abs(current["strike"] - float(prev_strike)) / spot * 100
                > LEVEL_SHIFT_THRESHOLD_PCT
            ):
                dist = (current["strike"] - spot) / spot * 100
                changes.append(
                    f"{emoji} {label} Delta חדשה: ${current['strike']:,.0f} "
                    f"({dist:+.2f}% מהספוט)"
                )

        try:
            await self.db.dex_levels.insert_one(
                {
                    "ticker": ticker,
                    "spot": spot,
                    "support": levels.get("support"),
                    "resistance": levels.get("resistance"),
                    "timestamp": datetime.utcnow(),
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("DexMonitor: snapshot insert failed")

        if not changes or self._telegram is None:
            return
        body = (
            f"🧲 עדכון רמות Delta – {ticker}\n"
            f"💰 ספוט: ${spot:,.2f}\n\n" + "\n".join(changes)
        )
        try:
            await self._telegram.send_alert(
                title=f"🧲 רמות Delta – {ticker}", body=body, urgency="medium"
            )
        except Exception:  # noqa: BLE001
            logger.exception("DexMonitor: telegram send failed")


__all__ = ["DexMonitor"]
