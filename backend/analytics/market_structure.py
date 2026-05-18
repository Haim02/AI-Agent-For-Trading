"""Combined GEX + Options-Flow market-structure analysis."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from analytics.flow_engine import OptionsFlowEngine
from analytics.gex_engine import GEXEngine
from db.connection import get_db
from utils.time_helper import ISRAEL_TZ

logger = logging.getLogger(__name__)


class MarketStructureAnalyzer:
    def __init__(self) -> None:
        self.gex = GEXEngine()
        self.flow = OptionsFlowEngine()
        self.db = get_db()

    async def get_daily_report(self, ticker: str = "SPY") -> dict:
        gex_data, flow_data = await asyncio.gather(
            self.gex.get_full_gex_analysis(ticker),
            self.flow.analyze_ticker_flow(ticker),
            return_exceptions=True,
        )
        if isinstance(gex_data, Exception):
            logger.exception("GEX analysis failed for %s", ticker, exc_info=gex_data)
            gex_data = {"error": str(gex_data)}
        if isinstance(flow_data, Exception):
            logger.exception("Flow analysis failed for %s", ticker, exc_info=flow_data)
            flow_data = {"error": str(flow_data)}

        combined = self._combine_signals(gex_data, flow_data)
        report = self._generate_report_hebrew(ticker, gex_data, flow_data, combined)

        try:
            await self.db.market_structure.insert_one(
                {
                    "ticker": ticker,
                    "date": datetime.utcnow(),
                    "gex": gex_data,
                    "flow": flow_data,
                    "combined": combined,
                    "report": report,
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("market_structure insert failed for %s", ticker)

        return {
            "ticker": ticker,
            "gex": gex_data,
            "flow": flow_data,
            "combined": combined,
            "report_hebrew": report,
        }

    def _combine_signals(self, gex: dict, flow: dict) -> dict:
        regime = (gex.get("regime") if isinstance(gex, dict) else None) or "unknown"
        flow_sentiment = (
            flow.get("overall_sentiment") if isinstance(flow, dict) else None
        ) or "neutral"

        if regime == "positive" and flow_sentiment == "bullish":
            conviction = "strong_bullish"
            strategy = "Bull Put Spread / Iron Condor"
        elif regime == "negative" and flow_sentiment == "bearish":
            conviction = "strong_bearish"
            strategy = "Buy Puts / Bear Call Spread"
        elif regime == "positive" and flow_sentiment == "bearish":
            conviction = "conflict_warning"
            strategy = "המתן לבהירות – יש סתירה"
        elif regime == "negative" and flow_sentiment == "bullish":
            conviction = "conflict_warning"
            strategy = "המתן לבהירות – יש סתירה"
        else:
            conviction = "neutral"
            strategy = "אין כיוון ברור"

        return {
            "conviction": conviction,
            "strategy": strategy,
            "gex_regime": regime,
            "flow_sentiment": flow_sentiment,
            "trade_recommended": conviction not in {"conflict_warning", "neutral"},
        }

    def _generate_report_hebrew(
        self, ticker: str, gex: dict, flow: dict, combined: dict
    ) -> str:
        conviction_emoji = {
            "strong_bullish": "🟢🟢 חזק מאוד שורי",
            "strong_bearish": "🔴🔴 חזק מאוד דובי",
            "conflict_warning": "⚠️ סתירה – המתן",
            "neutral": "🟡 ניטרלי",
        }.get(combined.get("conviction", "neutral"), "🟡")

        cw = gex.get("call_wall") if isinstance(gex, dict) else None
        pw = gex.get("put_wall") if isinstance(gex, dict) else None
        gf = gex.get("gamma_flip") if isinstance(gex, dict) else None
        spot = gex.get("spot_price") if isinstance(gex, dict) else None

        cw_text = f"${cw:,.0f}" if isinstance(cw, (int, float)) else "—"
        pw_text = f"${pw:,.0f}" if isinstance(pw, (int, float)) else "—"
        gf_text = f"${gf:,.0f}" if isinstance(gf, (int, float)) else "—"
        spot_text = f"${spot:,.2f}" if isinstance(spot, (int, float)) else "—"

        sweep_lines: list[str] = []
        for s in (flow.get("sweeps") if isinstance(flow, dict) else []) or []:
            direction = "🟢" if s.get("sentiment") == "bullish" else "🔴"
            premium = s.get("premium") or 0
            sweep_lines.append(
                f"{direction} ${s.get('strike', '—')} "
                f"exp {s.get('expiry', '—')} "
                f"פרמיה: ${premium:,}"
            )
        sweep_text = ("\n" + "\n".join(sweep_lines)) if sweep_lines else " אין"

        return (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"📊 ניתוח שוק מלא – {ticker}\n"
            f"🕐 {datetime.now(ISRAEL_TZ).strftime('%H:%M %d/%m/%Y')}\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 ספוט: {spot_text}\n\n"
            "🎯 רמות GEX מפתח:\n"
            f"🟢 Call Wall: {cw_text}\n"
            f"🔴 Put Wall: {pw_text}\n"
            f"⚡ Gamma Flip: {gf_text}\n\n"
            f"📈 משטר GEX: {str(gex.get('regime', '?')).upper() if isinstance(gex, dict) else '?'}\n"
            f"🎭 פרופיל: {str(gex.get('gex_profile', '?')).upper() if isinstance(gex, dict) else '?'}\n\n"
            "🌊 Options Flow:\n"
            f"שורי: {(flow.get('bull_pct', 0) if isinstance(flow, dict) else 0):.0f}% | "
            f"דובי: {(flow.get('bear_pct', 0) if isinstance(flow, dict) else 0):.0f}%\n\n"
            f"🔍 Sweeps עיקריים:{sweep_text}\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"{conviction_emoji}\n"
            f"💡 {combined.get('strategy', 'אין המלצה')}\n"
            "━━━━━━━━━━━━━━━━━━━"
        )


__all__ = ["MarketStructureAnalyzer"]
