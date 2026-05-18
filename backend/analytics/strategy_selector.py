"""Strategy selection engine.

Picks the best options strategy given GEX regime, options-flow sentiment,
IV rank, and VIX. Returns specific strikes + greeks + risk/reward + action items
so the morning briefing and chat agent can present a complete trade plan.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class OptionsStrategy(Enum):
    IRON_CONDOR = "iron_condor"
    BULL_PUT_SPREAD = "bull_put_spread"
    BEAR_CALL_SPREAD = "bear_call_spread"
    SHORT_STRANGLE = "short_strangle"
    LONG_STRANGLE = "long_strangle"
    LONG_STRADDLE = "long_straddle"
    CALL_DEBIT_SPREAD = "call_debit_spread"
    PUT_DEBIT_SPREAD = "put_debit_spread"
    IRON_BUTTERFLY = "iron_butterfly"
    CALENDAR_SPREAD = "calendar_spread"
    DO_NOTHING = "do_nothing"


_DEFAULT_GREEKS = {"delta": 0.0, "theta_per_day": 0.0, "vega": 0.0, "gamma": 0.0, "probability": 0.0}

_STRATEGY_GREEKS: dict[str, dict[str, float]] = {
    OptionsStrategy.BULL_PUT_SPREAD.value: {
        "delta": 0.35, "theta_per_day": 1.5, "vega": -0.5, "gamma": -0.02, "probability": 0.68,
    },
    OptionsStrategy.BEAR_CALL_SPREAD.value: {
        "delta": -0.35, "theta_per_day": 1.5, "vega": -0.5, "gamma": -0.02, "probability": 0.68,
    },
    OptionsStrategy.IRON_CONDOR.value: {
        "delta": 0.02, "theta_per_day": 2.0, "vega": -0.3, "gamma": -0.01, "probability": 0.70,
    },
    OptionsStrategy.IRON_BUTTERFLY.value: {
        "delta": 0.0, "theta_per_day": 3.5, "vega": -0.6, "gamma": -0.04, "probability": 0.45,
    },
    OptionsStrategy.CALL_DEBIT_SPREAD.value: {
        "delta": 0.50, "theta_per_day": -0.8, "vega": 0.6, "gamma": 0.05, "probability": 0.55,
    },
    OptionsStrategy.PUT_DEBIT_SPREAD.value: {
        "delta": -0.50, "theta_per_day": -0.8, "vega": 0.6, "gamma": 0.05, "probability": 0.55,
    },
    OptionsStrategy.LONG_STRANGLE.value: {
        "delta": 0.0, "theta_per_day": -1.5, "vega": 1.2, "gamma": 0.08, "probability": 0.40,
    },
    OptionsStrategy.LONG_STRADDLE.value: {
        "delta": 0.0, "theta_per_day": -2.0, "vega": 1.5, "gamma": 0.10, "probability": 0.40,
    },
}


class StrategySelector:
    """Rules-based strategy selection (Tastytrade + GEX)."""

    def __init__(self) -> None:
        self._vix_cache: Optional[float] = None

    # ───────────────────────── public ─────────────────────────

    async def select_strategy(
        self,
        ticker: str = "SPY",
        gex_data: Optional[dict] = None,
        flow_data: Optional[dict] = None,
        iv_rank: Optional[float] = None,
        vix: Optional[float] = None,
    ) -> dict[str, Any]:
        if gex_data is None or flow_data is None:
            from analytics.market_structure import MarketStructureAnalyzer
            report = await MarketStructureAnalyzer().get_daily_report(ticker)
            gex_data = report.get("gex") or {}
            flow_data = report.get("flow") or {}

        if iv_rank is None:
            iv_rank = await self._get_iv_rank(ticker)
        if vix is None:
            vix = await self._get_vix()

        decision = self._make_decision(
            ticker=ticker, gex=gex_data, flow=flow_data, iv_rank=iv_rank, vix=vix
        )
        strikes = self._calculate_strikes(decision["strategy"], gex_data)
        greeks = _STRATEGY_GREEKS.get(decision["strategy"].value, _DEFAULT_GREEKS).copy()
        rr = self._calculate_risk_reward(decision["strategy"], strikes)

        return {
            "ticker": ticker,
            "strategy": decision["strategy"].value,
            "recommendation": decision["recommendation"],
            "confidence": decision["confidence"],
            "reasoning": decision["reasoning"],
            "iv_rank": iv_rank,
            "vix": vix,
            "gex_regime": gex_data.get("regime"),
            "gex_profile": gex_data.get("gex_profile"),
            "flow_sentiment": flow_data.get("overall_sentiment"),
            "strikes": strikes,
            "greeks": greeks,
            "risk_reward": rr,
            "timestamp": datetime.utcnow(),
            "action_items": self._format_action_items(decision["strategy"], strikes, rr),
        }

    # ───────────────────────── decision tree ─────────────────────────

    def _make_decision(
        self,
        ticker: str,
        gex: dict,
        flow: dict,
        iv_rank: float,
        vix: float,
    ) -> dict[str, Any]:
        regime = (gex or {}).get("regime") or "neutral"
        profile = (gex or {}).get("gex_profile") or "unknown"
        flow_sentiment = (flow or {}).get("overall_sentiment") or "neutral"

        # 1. No-trade gates
        if vix is not None and vix > 40:
            return {
                "strategy": OptionsStrategy.DO_NOTHING,
                "recommendation": "שוק בחירום (VIX > 40). המתן ליציבות.",
                "confidence": 0.95,
                "reasoning": "VIX extremes require caution",
            }
        if iv_rank is not None and iv_rank < 15:
            return {
                "strategy": OptionsStrategy.DO_NOTHING,
                "recommendation": "IV Rank נמוך מדי (< 15%). אין פרמיה להכנסה.",
                "confidence": 0.90,
                "reasoning": "IV rank too low for credit spreads",
            }

        # 2. Positive Gamma + high IV → sell premium
        if regime == "positive" and iv_rank > 50 and profile in {"wall", "pillar"}:
            if flow_sentiment == "bullish":
                return {
                    "strategy": OptionsStrategy.BULL_PUT_SPREAD,
                    "recommendation": "Positive Gamma + Bullish Flow + High IV = מכור Put Premium",
                    "confidence": 0.92,
                    "reasoning": "Walls יגנו, Flow משוער עלייה, IV גבוה = הכנסה טובה",
                }
            if flow_sentiment == "bearish":
                return {
                    "strategy": OptionsStrategy.BEAR_CALL_SPREAD,
                    "recommendation": "Positive Gamma + Bearish Flow + High IV = מכור Call Premium",
                    "confidence": 0.90,
                    "reasoning": "Flow דובי עם יציבות = פוטנציאל ירידה",
                }
            return {
                "strategy": OptionsStrategy.IRON_CONDOR,
                "recommendation": "Positive Gamma + Neutral Flow + High IV = Iron Condor",
                "confidence": 0.93,
                "reasoning": "תנאים אופטימליים למכירת פרמיה דו-כיוונית",
            }

        # 3. Positive Gamma + low IV + Pin profile → butterfly
        if regime == "positive" and iv_rank < 35 and profile == "pin":
            return {
                "strategy": OptionsStrategy.IRON_BUTTERFLY,
                "recommendation": "Positive Gamma + Pin profile + Low IV = Iron Butterfly",
                "confidence": 0.88,
                "reasoning": "Theta decay יחד עם pinning ATM",
            }

        # 4. Negative Gamma → buy premium
        if regime == "negative":
            if flow_sentiment == "bullish":
                return {
                    "strategy": OptionsStrategy.CALL_DEBIT_SPREAD,
                    "recommendation": "Negative Gamma + Bullish Flow = קנה Call Debit Spread",
                    "confidence": 0.87,
                    "reasoning": "Dealers מאיצים את הכיוון – Squeeze פוטנציאלי למעלה",
                }
            if flow_sentiment == "bearish":
                return {
                    "strategy": OptionsStrategy.PUT_DEBIT_SPREAD,
                    "recommendation": "Negative Gamma + Bearish Flow = קנה Put Debit Spread",
                    "confidence": 0.87,
                    "reasoning": "Cascade דובי צפוי – קנה כדי להרוויח מההאצה",
                }
            return {
                "strategy": OptionsStrategy.LONG_STRANGLE,
                "recommendation": "Negative Gamma + Flow לא ברור = Long Strangle",
                "confidence": 0.80,
                "reasoning": "ציפייה לתנועה גדולה בכל כיוון",
            }

        # 5. Default
        return {
            "strategy": OptionsStrategy.IRON_CONDOR,
            "recommendation": "תנאים ניטרליים. Iron Condor סטנדרטי.",
            "confidence": 0.70,
            "reasoning": "Baseline safe strategy",
        }

    # ───────────────────────── strikes ─────────────────────────

    def _calculate_strikes(self, strategy: OptionsStrategy, gex: dict) -> dict[str, Any]:
        spot = float((gex or {}).get("spot_price") or 0)
        if spot <= 0:
            return {}
        call_wall = (gex or {}).get("call_wall")
        put_wall = (gex or {}).get("put_wall")

        if strategy is OptionsStrategy.BULL_PUT_SPREAD:
            short_put = (put_wall + 2) if put_wall else spot - 5
            long_put = short_put - 7
            return {
                "type": "bull_put_spread",
                "short_put": round(short_put, 0),
                "long_put": round(long_put, 0),
                "width": round(short_put - long_put, 0),
            }
        if strategy is OptionsStrategy.BEAR_CALL_SPREAD:
            short_call = (call_wall - 2) if call_wall else spot + 5
            long_call = short_call + 7
            return {
                "type": "bear_call_spread",
                "short_call": round(short_call, 0),
                "long_call": round(long_call, 0),
                "width": round(long_call - short_call, 0),
            }
        if strategy is OptionsStrategy.IRON_CONDOR:
            short_call = (call_wall + 1) if call_wall else spot + 5
            short_put = (put_wall - 1) if put_wall else spot - 5
            return {
                "type": "iron_condor",
                "short_call": round(short_call, 0),
                "long_call": round(short_call + 7, 0),
                "short_put": round(short_put, 0),
                "long_put": round(short_put - 7, 0),
                "call_width": 7,
                "put_width": 7,
                "width": 7,
            }
        if strategy is OptionsStrategy.IRON_BUTTERFLY:
            atm = round(spot, 0)
            return {
                "type": "iron_butterfly",
                "short_call": atm,
                "long_call": atm + 2,
                "short_put": atm,
                "long_put": atm - 2,
                "width": 2,
            }
        if strategy is OptionsStrategy.CALL_DEBIT_SPREAD:
            long_call = round(spot, 0)
            return {
                "type": "call_debit_spread",
                "long_call": long_call,
                "short_call": long_call + 5,
                "width": 5,
            }
        if strategy is OptionsStrategy.PUT_DEBIT_SPREAD:
            long_put = round(spot, 0)
            return {
                "type": "put_debit_spread",
                "long_put": long_put,
                "short_put": long_put - 5,
                "width": 5,
            }
        if strategy is OptionsStrategy.LONG_STRANGLE:
            return {
                "type": "long_strangle",
                "long_call": round(spot + 5, 0),
                "long_put": round(spot - 5, 0),
                "width": 0,
            }
        return {}

    # ───────────────────────── risk/reward ─────────────────────────

    def _calculate_risk_reward(self, strategy: OptionsStrategy, strikes: dict) -> dict[str, Any]:
        if not strikes:
            return {}

        strategy_type = strikes.get("type")
        credit_strategies = {"bull_put_spread", "bear_call_spread", "iron_condor", "iron_butterfly"}
        debit_strategies = {"call_debit_spread", "put_debit_spread"}

        if strategy_type in credit_strategies:
            width = strikes.get("width") or strikes.get("call_width") or 7
            max_credit = width * 100 * 0.3  # rough — ~30% of width for credit spreads
            max_loss = (width - (max_credit / 100)) * 100
            return {
                "type": "credit",
                "max_credit": round(max_credit, 2),
                "max_loss": round(max_loss, 2),
                "profit_target": round(max_credit * 0.5, 2),
                "profit_target_pct": 50,
                "stop_loss": round(max_credit * 2.0, 2),
                "stop_loss_pct": 200,
                "risk_reward_ratio": "1:2",
                "recommended_size": "1-2 spreads",
            }
        if strategy_type in debit_strategies:
            width = strikes.get("width") or 5
            max_debit = width * 100 * 0.3
            max_profit = width * 100 - max_debit
            return {
                "type": "debit",
                "max_debit": round(max_debit, 2),
                "max_profit": round(max_profit, 2),
                "profit_target": round(max_debit * 0.75, 2),
                "profit_target_pct": 75,
                "stop_loss": round(max_debit * 0.5, 2),
                "stop_loss_pct": 50,
                "risk_reward_ratio": "1:2.5",
                "recommended_size": "1-2 spreads",
            }
        if strategy_type == "long_strangle":
            return {
                "type": "debit",
                "max_debit": 0.0,
                "max_profit": "ללא הגבלה",
                "profit_target_pct": 100,
                "stop_loss_pct": 50,
                "risk_reward_ratio": "ללא הגבלה (תיאורטית)",
                "recommended_size": "1 strangle",
            }
        return {}

    # ───────────────────────── action items ─────────────────────────

    def _format_action_items(
        self, strategy: OptionsStrategy, strikes: dict, rr: dict
    ) -> list[str]:
        if not strikes:
            return []
        pt = rr.get("profit_target_pct", 50)
        sl = rr.get("stop_loss_pct", 200)

        if strategy is OptionsStrategy.BULL_PUT_SPREAD:
            return [
                f"מכור Put ב-${strikes.get('short_put')}",
                f"קנה Put ב-${strikes.get('long_put')}",
                f"יעד רווח: {pt}%",
                f"Stop Loss: {sl}%",
            ]
        if strategy is OptionsStrategy.BEAR_CALL_SPREAD:
            return [
                f"מכור Call ב-${strikes.get('short_call')}",
                f"קנה Call ב-${strikes.get('long_call')}",
                f"יעד רווח: {pt}%",
                f"Stop Loss: {sl}%",
            ]
        if strategy in {OptionsStrategy.IRON_CONDOR, OptionsStrategy.IRON_BUTTERFLY}:
            return [
                f"מכור Call ב-${strikes.get('short_call')}",
                f"קנה Call ב-${strikes.get('long_call')}",
                f"מכור Put ב-${strikes.get('short_put')}",
                f"קנה Put ב-${strikes.get('long_put')}",
                f"יעד רווח: {pt}%",
                f"Stop Loss: {sl}%",
            ]
        if strategy is OptionsStrategy.CALL_DEBIT_SPREAD:
            return [
                f"קנה Call ב-${strikes.get('long_call')}",
                f"מכור Call ב-${strikes.get('short_call')}",
                f"יעד רווח: {pt}%",
                f"Stop Loss: {sl}%",
            ]
        if strategy is OptionsStrategy.PUT_DEBIT_SPREAD:
            return [
                f"קנה Put ב-${strikes.get('long_put')}",
                f"מכור Put ב-${strikes.get('short_put')}",
                f"יעד רווח: {pt}%",
                f"Stop Loss: {sl}%",
            ]
        if strategy is OptionsStrategy.LONG_STRANGLE:
            return [
                f"קנה Call ב-${strikes.get('long_call')}",
                f"קנה Put ב-${strikes.get('long_put')}",
                "יציאה: 100% רווח או 50% הפסד",
            ]
        return []

    # ───────────────────────── data fetch ─────────────────────────

    async def _get_iv_rank(self, ticker: str) -> float:
        try:
            from analytics.iv_rank_calculator import IVRankCalculator
            result = await asyncio.to_thread(IVRankCalculator().calculate_iv_rank, ticker)
        except Exception:  # noqa: BLE001
            logger.exception("IV rank lookup failed for %s", ticker)
            return 50.0
        if result is None:
            return 50.0
        try:
            return float(result.iv_rank)
        except (AttributeError, TypeError, ValueError):
            return 50.0

    async def _get_vix(self) -> float:
        """Cached VIX lookup via yfinance ^VIX."""
        if self._vix_cache is not None:
            return self._vix_cache

        def _fetch() -> Optional[float]:
            try:
                import yfinance as yf
                hist = yf.Ticker("^VIX").history(period="1d")
                if hist.empty:
                    return None
                return float(hist["Close"].iloc[-1])
            except Exception:  # noqa: BLE001
                logger.exception("VIX lookup failed")
                return None

        value = await asyncio.to_thread(_fetch)
        self._vix_cache = value if value is not None else 18.0
        return self._vix_cache


__all__ = ["OptionsStrategy", "StrategySelector"]
