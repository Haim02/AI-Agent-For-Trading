"""Real-time Call/Put Wall and Gamma-Flip cross detector."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from analytics.gex_engine import GEXEngine
from db.connection import get_db
from services.telegram_service import TelegramService, TelegramServiceError

logger = logging.getLogger(__name__)


class WallBreakDetector:
    def __init__(self) -> None:
        self.gex = GEXEngine()
        self.db = get_db()
        try:
            self._telegram: Optional[TelegramService] = TelegramService()
        except TelegramServiceError as exc:
            logger.warning("Telegram disabled in WallBreakDetector: %s", exc)
            self._telegram = None
        self.last_levels: dict[str, dict] = {}

    async def check_for_breaks(self, ticker: str = "SPY") -> None:
        try:
            gex = await self.gex.get_full_gex_analysis(ticker)
        except Exception as exc:  # noqa: BLE001
            logger.error("Wall break check error: %s", exc)
            return

        if "error" in gex:
            logger.debug("WallBreakDetector: no GEX data for %s (%s)", ticker, gex.get("error"))
            return

        spot = float(gex.get("spot_price") or 0)
        call_wall = gex.get("call_wall")
        put_wall = gex.get("put_wall")
        gamma_flip = gex.get("gamma_flip")

        prev = self.last_levels.get(ticker, {})
        prev_spot = prev.get("spot", spot)

        alerts: list[dict] = []

        if call_wall and prev_spot < call_wall <= spot:
            alerts.append(
                {
                    "type": "call_wall_break",
                    "urgency": "high",
                    "message": (
                        f"🚨 שבירת Call Wall – {ticker}!\n\n"
                        f"💰 ספוט: ${spot:,.2f}\n"
                        f"🟢 Call Wall שנשבר: ${call_wall:,.0f}\n\n"
                        "🔥 Gamma Squeeze אפשרי!\n"
                        "כשהמחיר עובר מעל ה-Call Wall,\n"
                        "Dealers חייבים לקנות – תנועה עלולה להאיץ!\n\n"
                        "💡 שקול: Long Calls / Bull Spread"
                    ),
                }
            )

        if put_wall and prev_spot > put_wall >= spot:
            alerts.append(
                {
                    "type": "put_wall_break",
                    "urgency": "high",
                    "message": (
                        f"🚨 שבירת Put Wall – {ticker}!\n\n"
                        f"💰 ספוט: ${spot:,.2f}\n"
                        f"🔴 Put Wall שנשבר: ${put_wall:,.0f}\n\n"
                        "❗ Gamma Cascade אפשרי!\n"
                        "שבירת ה-Put Wall מסירה את התמיכה.\n"
                        "Dealers עוברים למכירה – ירידה עלולה להאיץ!\n\n"
                        "💡 שקול: Long Puts / Bear Spread"
                    ),
                }
            )

        if gamma_flip:
            if prev_spot > gamma_flip >= spot:
                alerts.append(
                    {
                        "type": "gamma_flip_cross_down",
                        "urgency": "high",
                        "message": (
                            f"⚡ Gamma Flip נחצה כלפי מטה – {ticker}!\n\n"
                            f"💰 ספוט: ${spot:,.2f}\n"
                            f"⚡ Gamma Flip: ${gamma_flip:,.0f}\n\n"
                            "⚠️ השוק עבר ל-NEGATIVE GAMMA!\n"
                            "תנודתיות צפויה לעלות.\n"
                            "Dealers יתחילו לגדר בכיוון התנועה.\n\n"
                            "💡 הימנע ממכירת פרמיה עירומה!"
                        ),
                    }
                )
            elif prev_spot < gamma_flip <= spot:
                alerts.append(
                    {
                        "type": "gamma_flip_cross_up",
                        "urgency": "medium",
                        "message": (
                            f"✅ Gamma Flip נחצה כלפי מעלה – {ticker}!\n\n"
                            f"💰 ספוט: ${spot:,.2f}\n"
                            f"⚡ Gamma Flip: ${gamma_flip:,.0f}\n\n"
                            "💪 השוק חזר ל-POSITIVE GAMMA!\n"
                            "תנודתיות צפויה לרדת.\n"
                            "תנאים טובים יותר למכירת פרמיה.\n\n"
                            "💡 שקול: Iron Condor / Credit Spreads"
                        ),
                    }
                )

        for alert in alerts:
            if self._telegram is not None:
                try:
                    await self._telegram.send_alert(
                        title=f"🚨 {ticker} Wall Break!",
                        body=alert["message"],
                        urgency=alert["urgency"],
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("WallBreakDetector: telegram failed")
            try:
                await self.db.wall_breaks.insert_one(
                    {
                        "ticker": ticker,
                        "type": alert["type"],
                        "spot": spot,
                        "call_wall": call_wall,
                        "put_wall": put_wall,
                        "gamma_flip": gamma_flip,
                        "timestamp": datetime.utcnow(),
                    }
                )
            except Exception:  # noqa: BLE001
                logger.exception("WallBreakDetector: db insert failed")

        self.last_levels[ticker] = {
            "spot": spot,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "gamma_flip": gamma_flip,
        }


__all__ = ["WallBreakDetector"]
