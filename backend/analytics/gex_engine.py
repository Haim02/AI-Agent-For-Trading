"""Professional GEX analysis engine.

Calculates Call/Put Walls, Gamma Flip, regime, and a Hebrew-language
analysis suitable for Telegram. Primary source is MassiveAPI; falls back
to yfinance (which usually lacks Greeks – treat its output as a degraded
"no-data" signal rather than authoritative).
"""

from __future__ import annotations

import logging
import os
import traceback
from datetime import datetime
from typing import Any, Optional

import httpx

from utils.time_helper import ISRAEL_TZ

logger = logging.getLogger(__name__)


# Index tickers don't have tradeable options chains on yfinance / Massive –
# substitute their tracking ETF for the chain query but keep the original
# label so the user sees what they asked for.
TICKER_MAP: dict[str, str] = {
    "SPX": "SPY",
    "NDX": "QQQ",
    "RUT": "IWM",
    "VIX": "UVXY",
    "DJI": "DIA",
    "^GSPC": "SPY",
    "^NDX": "QQQ",
    "^RUT": "IWM",
    "^VIX": "UVXY",
}


class GEXEngine:
    MASSIVE_BASE = "https://api.massive.com/v1"
    DEFAULT_TICKERS = ["SPY", "QQQ", "SPX"]

    def __init__(self) -> None:
        self.massive_key = os.getenv("MASSIVE_API_KEY")
        self.uw_key = os.getenv("UNUSUAL_WHALES_API_KEY")
        self.headers_massive = (
            {"Authorization": f"Bearer {self.massive_key}"} if self.massive_key else {}
        )
        self.headers_uw = (
            {"Authorization": f"Bearer {self.uw_key}"} if self.uw_key else {}
        )

    # ───────────────────────── public ─────────────────────────

    async def get_full_gex_analysis(self, ticker: str = "SPY") -> dict:
        original_ticker = (ticker or "SPY").upper().strip()
        options_ticker = TICKER_MAP.get(original_ticker, original_ticker)
        logger.info(
            "GEX analysis started: %s → using %s for options",
            original_ticker, options_ticker,
        )

        try:
            chain = await self._get_options_chain(options_ticker)
            source = "MassiveAPI"
            if not chain:
                chain = self._get_chain_yfinance(options_ticker)
                source = "yfinance"

            if not chain:
                logger.warning(
                    "All data sources failed for %s. Using estimated levels.",
                    options_ticker,
                )
                return self._estimated_fallback(original_ticker, options_ticker)

            spot = float(chain.get("spot_price") or 0.0)
            options = chain.get("options") or []
            if spot <= 0 or not options:
                logger.warning(
                    "Incomplete chain for %s (spot=%s, n_options=%d) – using estimated",
                    options_ticker, spot, len(options),
                )
                return self._estimated_fallback(original_ticker, options_ticker)

            gex_by_strike: dict[float, float] = {}
            for opt in options:
                strike = opt.get("strike")
                gamma = opt.get("gamma") or 0
                oi = opt.get("open_interest") or 0
                opt_type = (opt.get("type") or "").lower()
                if strike is None:
                    continue

                gex_value = float(gamma) * float(oi) * 100 * (spot ** 2)
                if opt_type == "call":
                    gex_value = abs(gex_value)
                else:
                    gex_value = -abs(gex_value)

                gex_by_strike[strike] = gex_by_strike.get(strike, 0.0) + gex_value

            if not gex_by_strike or all(v == 0 for v in gex_by_strike.values()):
                # yfinance returns rows without gamma → every GEX value is 0.
                # Treat that as no-data and fall back to estimates.
                logger.warning(
                    "No usable GEX values for %s (source=%s) – using estimated",
                    options_ticker, source,
                )
                return self._estimated_fallback(original_ticker, options_ticker, spot=spot)

            # Call Wall: max positive GEX at/above spot.
            call_gex = {s: v for s, v in gex_by_strike.items() if s >= spot and v > 0}
            call_wall = max(call_gex, key=call_gex.get, default=None) if call_gex else None

            # Put Wall: max negative GEX at/below spot.
            put_gex = {s: v for s, v in gex_by_strike.items() if s <= spot and v < 0}
            put_wall = min(put_gex, key=put_gex.get, default=None) if put_gex else None

            gamma_flip = self._find_gamma_flip(gex_by_strike, spot)
            total_gex = sum(gex_by_strike.values())
            regime = "positive" if total_gex > 0 else "negative"
            gex_profile = self._classify_profile(gex_by_strike, spot, call_wall, put_wall)

            cw_dist = ((call_wall - spot) / spot * 100) if call_wall else None
            pw_dist = ((spot - put_wall) / spot * 100) if put_wall else None

            analysis_hebrew = self._generate_analysis_hebrew(
                ticker=original_ticker,
                spot=spot,
                call_wall=call_wall,
                put_wall=put_wall,
                gamma_flip=gamma_flip,
                regime=regime,
                gex_profile=gex_profile,
                cw_dist=cw_dist,
                pw_dist=pw_dist,
            )

            return {
                "ticker": original_ticker,
                "options_ticker": options_ticker,
                "spot_price": round(spot, 2),
                "call_wall": round(call_wall, 0) if call_wall else None,
                "put_wall": round(put_wall, 0) if put_wall else None,
                "gamma_flip": round(gamma_flip, 0) if gamma_flip else None,
                "call_wall_distance_pct": round(cw_dist, 2) if cw_dist is not None else None,
                "put_wall_distance_pct": round(pw_dist, 2) if pw_dist is not None else None,
                "regime": regime,
                "total_gex": round(total_gex / 1e9, 3),
                "gex_profile": gex_profile,
                "top_strikes": self._get_top_strikes(gex_by_strike, spot),
                "analysis_hebrew": analysis_hebrew,
                "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
                "source": source,
            }

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "GEX FULL ERROR for %s:\n%s", original_ticker, traceback.format_exc()
            )
            return {
                "error": str(exc),
                "ticker": original_ticker,
                "spot_price": 0,
            }

    # ───────────────────────── estimated fallback ─────────────────────────

    def _estimated_fallback(
        self, original_ticker: str, options_ticker: str, spot: Optional[float] = None
    ) -> dict:
        """Return ±2% wall estimates when no live options chain is available."""
        if spot is None or spot <= 0:
            spot = self._lookup_spot(original_ticker, options_ticker) or 0.0
        if spot <= 0:
            # Coarse fallback so the chart still renders.
            spot = 5500.0 if original_ticker == "SPX" else 500.0

        call_wall = round(spot * 1.020, 0)
        put_wall = round(spot * 0.980, 0)
        gamma_flip = round(spot * 0.998, 0)

        analysis_hebrew = (
            f"📊 ניתוח GEX – {original_ticker}\n"
            f"💰 ספוט: ${spot:,.2f}\n\n"
            "⚠️ נתונים משוערים (אין גישה לאופציות)\n\n"
            f"🟢 Call Wall (משוער): ${call_wall:,.0f}\n"
            f"🔴 Put Wall (משוער): ${put_wall:,.0f}\n"
            f"⚡ Gamma Flip (משוער): ${gamma_flip:,.0f}\n\n"
            "💡 לנתונים מדויקים יותר:\n"
            "השתמש ב-SPY במקום SPX"
        )

        return {
            "ticker": original_ticker,
            "options_ticker": options_ticker,
            "spot_price": round(spot, 2),
            "call_wall": call_wall,
            "put_wall": put_wall,
            "gamma_flip": gamma_flip,
            "call_wall_distance_pct": 2.0,
            "put_wall_distance_pct": 2.0,
            "regime": "positive",
            "total_gex": 0,
            "gex_profile": "wall",
            "top_strikes": [],
            "analysis_hebrew": analysis_hebrew,
            "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
            "source": "estimated",
            "warning": "estimated_levels",
        }

    @staticmethod
    def _lookup_spot(original_ticker: str, options_ticker: str) -> Optional[float]:
        """Best-effort spot lookup via yfinance for the estimated fallback."""
        try:
            import yfinance as yf

            yf_sym = {
                "SPX": "^GSPC",
                "NDX": "^NDX",
                "RUT": "^RUT",
                "VIX": "^VIX",
                "DJI": "^DJI",
            }.get(original_ticker, options_ticker)
            hist = yf.Ticker(yf_sym).history(period="1d")
            if hist.empty:
                hist = yf.Ticker(yf_sym).history(period="5d")
            if hist.empty:
                return None
            return float(hist["Close"].iloc[-1])
        except Exception:  # noqa: BLE001
            logger.exception("estimated fallback spot lookup failed")
            return None

    # ───────────────────────── internals ─────────────────────────

    def _find_gamma_flip(self, gex_by_strike: dict, spot: float) -> Optional[float]:
        sorted_strikes = sorted(gex_by_strike.keys())
        cumulative = 0.0
        prev_cumulative = 0.0
        prev_strike: Optional[float] = None
        for strike in sorted_strikes:
            prev_cumulative = cumulative
            cumulative += gex_by_strike[strike]
            if prev_cumulative * cumulative < 0 and prev_strike is not None:
                return (prev_strike + strike) / 2
            prev_strike = strike
        return spot

    def _classify_profile(
        self,
        gex_by_strike: dict,
        spot: float,
        call_wall: Optional[float],
        put_wall: Optional[float],
    ) -> str:
        if not gex_by_strike:
            return "unknown"
        max_gex = max(abs(v) for v in gex_by_strike.values()) or 1.0

        near_strikes = {
            s: v for s, v in gex_by_strike.items()
            if abs(s - spot) / spot < 0.02
        }
        if near_strikes:
            max_near = max(abs(v) for v in near_strikes.values())
            if max_near > max_gex * 0.6:
                return "pin"

        significant = [s for s, v in gex_by_strike.items() if abs(v) > max_gex * 0.3]
        if len(significant) <= 1:
            return "wall"
        if len(significant) <= 3:
            return "pillar"
        return "slide"

    def _get_top_strikes(self, gex_by_strike: dict, spot: float) -> list[dict]:
        sorted_by_mag = sorted(gex_by_strike.items(), key=lambda x: abs(x[1]), reverse=True)
        result: list[dict] = []
        for strike, gex in sorted_by_mag[:5]:
            result.append(
                {
                    "strike": round(strike, 0),
                    "gex_billion": round(gex / 1e9, 3),
                    "type": "call_wall" if gex > 0 else "put_wall",
                    "distance_pct": round((strike - spot) / spot * 100, 2),
                }
            )
        return result

    def _generate_analysis_hebrew(
        self,
        ticker: str,
        spot: float,
        call_wall: Optional[float],
        put_wall: Optional[float],
        gamma_flip: Optional[float],
        regime: str,
        gex_profile: str,
        cw_dist: Optional[float],
        pw_dist: Optional[float],
    ) -> str:
        regime_text = "חיובי (Positive)" if regime == "positive" else "שלילי (Negative)"
        profile_text = {
            "wall": "קיר (Wall) – תמיכה/התנגדות חזקה",
            "pillar": "עמודים (Pillars) – מספר רמות",
            "slide": "מדרון (Slide) – מגמה הדרגתית",
            "pin": "סיכה (Pin) – שוק צמוד",
        }.get(gex_profile, gex_profile)

        strategy = self._suggest_strategy_hebrew(regime, gex_profile, cw_dist, pw_dist)

        call_wall_str = (
            f"${call_wall:,.0f} ({cw_dist:+.1f}% מהספוט)"
            if call_wall and cw_dist is not None
            else "לא נמצא"
        )
        put_wall_str = (
            f"${put_wall:,.0f} ({pw_dist:+.1f}% מהספוט)"
            if put_wall and pw_dist is not None
            else "לא נמצא"
        )
        flip_str = f"${gamma_flip:,.0f}" if gamma_flip else "לא נמצא"

        return (
            f"📊 ניתוח GEX – {ticker}\n"
            f"💰 ספוט: ${spot:,.2f}\n\n"
            "🔑 רמות מפתח:\n"
            f"🟢 Call Wall: {call_wall_str}\n"
            f"🔴 Put Wall: {put_wall_str}\n"
            f"⚡ Gamma Flip: {flip_str}\n\n"
            f"📈 משטר: {regime_text}\n"
            f"🎯 פרופיל: {profile_text}\n\n"
            "💡 אסטרטגיה מומלצת:\n"
            f"{strategy}"
        )

    def _suggest_strategy_hebrew(
        self,
        regime: str,
        profile: str,
        cw_dist: Optional[float],
        pw_dist: Optional[float],
    ) -> str:
        if regime == "positive":
            if profile == "wall":
                return (
                    "שוק יציב עם קירות ברורים\n"
                    "מתאים: Iron Condor / Short Strangle\n"
                    "Strikes: מחוץ ל-Walls"
                )
            if profile == "pin":
                return (
                    "השוק צמוד – ציפייה לדשדוש\n"
                    "מתאים: Iron Butterfly / Calendar\n"
                    "Strike: ATM"
                )
            return (
                "Positive Gamma – מכור פרמיה\n"
                "מתאים: Bull Put Spread / Credit Spread"
            )
        return (
            "Negative Gamma – תנודתיות גבוהה!\n"
            "מתאים: Long Straddle / Debit Spreads\n"
            "שים לב לכיוון ה-Gamma Flip"
        )

    async def _get_options_chain(self, ticker: str) -> Optional[dict]:
        if not self.massive_key:
            return None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.MASSIVE_BASE}/options/chain/{ticker}",
                    headers=self.headers_massive,
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "spot_price": data.get("underlying_price", 0),
                        "options": data.get("options", []),
                    }
                logger.warning("Massive chain %s: HTTP %s", ticker, resp.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Massive failed: %s", exc)
        return None

    def _get_chain_yfinance(self, ticker: str) -> Optional[dict]:
        """yfinance fallback. Note: yfinance rows usually lack ``gamma``; resulting GEX will be 0."""
        try:
            import yfinance as yf

            t = yf.Ticker(ticker)

            try:
                hist = t.history(period="1d")
                if hist.empty:
                    hist = t.history(period="5d")
                if hist.empty:
                    logger.error("No price history for %s", ticker)
                    return None
                spot = float(hist["Close"].iloc[-1])
            except Exception as exc:  # noqa: BLE001
                logger.error("Price fetch error for %s: %s", ticker, exc)
                return None

            try:
                expirations = t.options
            except Exception as exc:  # noqa: BLE001
                logger.error("No options for %s: %s", ticker, exc)
                return None
            if not expirations:
                logger.warning("Empty options for %s", ticker)
                return None

            options: list[dict[str, Any]] = []
            for exp in expirations[:4]:
                try:
                    chain = t.option_chain(exp)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("yfinance: option_chain(%s) for %s failed: %s", exp, ticker, exc)
                    continue

                for _, row in chain.calls.iterrows():
                    gamma = row.get("gamma", 0)
                    oi = row.get("openInterest", 0)
                    options.append(
                        {
                            "strike": float(row["strike"]),
                            "gamma": float(gamma or 0),
                            "open_interest": int(oi or 0),
                            "type": "call",
                        }
                    )
                for _, row in chain.puts.iterrows():
                    gamma = row.get("gamma", 0)
                    oi = row.get("openInterest", 0)
                    options.append(
                        {
                            "strike": float(row["strike"]),
                            "gamma": float(gamma or 0),
                            "open_interest": int(oi or 0),
                            "type": "put",
                        }
                    )

            if not options:
                logger.error("No options data parsed for %s", ticker)
                return None

            logger.info("yfinance: %s spot=%s options=%d", ticker, spot, len(options))
            return {"spot_price": spot, "options": options}

        except Exception:  # noqa: BLE001
            logger.error("yfinance FULL ERROR %s:\n%s", ticker, traceback.format_exc())
            return None


__all__ = ["GEXEngine"]
