"""FlashAlpha Lab API client.

Clean REST API for GEX, key levels, 0DTE data, and max pain. Used as the
primary source by ``GEXEngine``; UW / Massive / yfinance remain as fallbacks
for tickers the user's plan doesn't cover.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

import httpx

from utils.time_helper import ISRAEL_TZ

logger = logging.getLogger(__name__)


class FlashAlphaTool:
    BASE_URL = "https://lab.flashalpha.com"

    def __init__(self) -> None:
        self.api_key = os.getenv("FLASHALPHA_API_KEY", "")
        self.headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
        }

    async def _get(
        self, endpoint: str, params: Optional[dict] = None
    ) -> Optional[dict]:
        if not self.api_key:
            logger.warning("FLASHALPHA_API_KEY not set")
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.BASE_URL}{endpoint}",
                    headers=self.headers,
                    params=params or {},
                )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 403:
                logger.warning(
                    "FlashAlpha 403: plan upgrade needed for %s", endpoint
                )
                return {"error": "plan_required"}
            if resp.status_code == 404:
                logger.warning("FlashAlpha 404: %s", endpoint)
                return {"error": "not_found"}
            if resp.status_code == 429:
                logger.warning("FlashAlpha rate limit hit")
                return {"error": "rate_limit"}
            logger.warning("FlashAlpha %s on %s", resp.status_code, endpoint)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error("FlashAlpha error: %s", exc)
            return None

    # ───────────────────────── raw endpoints ─────────────────────────

    async def get_levels(self, symbol: str) -> Optional[dict]:
        """Key levels: gamma_flip, call_wall, put_wall, highest_oi, zero_dte_magnet."""
        return await self._get(f"/v1/exposure/levels/{symbol}")

    async def get_gex(
        self,
        symbol: str,
        expiration: Optional[str] = None,
        min_oi: int = 0,
    ) -> Optional[dict]:
        """Full GEX by strike. Full-chain needs Growth plan; supply expiration on lower tiers."""
        params: dict[str, Any] = {}
        if expiration:
            params["expiration"] = expiration
        if min_oi:
            params["min_oi"] = min_oi
        return await self._get(f"/v1/exposure/gex/{symbol}", params=params)

    async def get_zero_dte(self, symbol: str) -> Optional[dict]:
        return await self._get(f"/v1/exposure/zero-dte/{symbol}")

    async def get_max_pain(self, symbol: str) -> Optional[dict]:
        return await self._get(f"/v1/maxpain/{symbol}")

    # ───────────────────────── combined analysis ─────────────────────────

    async def get_full_analysis(self, symbol: str) -> dict:
        levels_data, gex_data = await asyncio.gather(
            self.get_levels(symbol),
            self.get_gex(symbol),
            return_exceptions=True,
        )
        if isinstance(levels_data, Exception):
            levels_data = None
        if isinstance(gex_data, Exception):
            gex_data = None

        if not levels_data or "error" in (levels_data or {}):
            err = (levels_data or {}).get("error", "no_data")
            return {
                "error": err,
                "symbol": symbol,
                "message": self._error_message_hebrew(err, symbol),
            }

        levels = levels_data.get("levels", {}) or {}
        spot = float(levels_data.get("underlying_price") or 0)
        call_wall = levels.get("call_wall")
        put_wall = levels.get("put_wall")
        gamma_flip = levels.get("gamma_flip")
        oi_strike = levels.get("highest_oi_strike")
        zero_dte = levels.get("zero_dte_magnet")

        net_gex = 0.0
        regime = "positive"
        if gex_data and "error" not in gex_data:
            net_gex = float(gex_data.get("net_gex") or 0)
            regime = gex_data.get("net_gex_label") or (
                "positive" if net_gex >= 0 else "negative"
            )

        cw_dist = (
            ((call_wall - spot) / spot * 100) if call_wall and spot else None
        )
        pw_dist = (
            ((spot - put_wall) / spot * 100) if put_wall and spot else None
        )

        top_strikes: list[dict[str, Any]] = []
        if gex_data and "strikes" in (gex_data or {}):
            sorted_strikes = sorted(
                gex_data["strikes"],
                key=lambda s: abs(s.get("net_gex") or 0),
                reverse=True,
            )
            for s in sorted_strikes[:6]:
                strike = s.get("strike") or 0
                gex_val = float(s.get("net_gex") or 0)
                top_strikes.append(
                    {
                        "strike": strike,
                        "gex_billion": round(gex_val / 1e9, 3),
                        "type": "call_wall" if gex_val > 0 else "put_wall",
                        "distance_pct": (
                            round((strike - spot) / spot * 100, 2) if spot else 0
                        ),
                    }
                )

        analysis_hebrew = self._format_hebrew(
            symbol=symbol,
            spot=spot,
            call_wall=call_wall,
            put_wall=put_wall,
            gamma_flip=gamma_flip,
            zero_dte=zero_dte,
            oi_strike=oi_strike,
            regime=regime,
            cw_dist=cw_dist,
            pw_dist=pw_dist,
        )

        return {
            "ticker": symbol,
            "spot_price": round(spot, 2),
            "call_wall": call_wall,
            "put_wall": put_wall,
            "gamma_flip": gamma_flip,
            "highest_oi_strike": oi_strike,
            "zero_dte_magnet": zero_dte,
            "call_wall_distance_pct": round(cw_dist, 2) if cw_dist is not None else None,
            "put_wall_distance_pct": round(pw_dist, 2) if pw_dist is not None else None,
            "regime": regime,
            "total_gex": round(net_gex / 1e9, 3),
            "top_strikes": top_strikes,
            "gex_profile": self._classify_profile(top_strikes, spot),
            "analysis_hebrew": analysis_hebrew,
            "source": "flashalpha",
            "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
        }

    # ───────────────────────── helpers ─────────────────────────

    def _classify_profile(self, top_strikes: list[dict], spot: float) -> str:
        if not top_strikes or not spot:
            return "unknown"
        near = [s for s in top_strikes if abs(s.get("distance_pct", 0)) < 1.0]
        if near and abs(near[0].get("gex_billion") or 0) > 0:
            return "pin"
        significant = [
            s for s in top_strikes if abs(s.get("gex_billion") or 0) > 0.1
        ]
        if len(significant) <= 1:
            return "wall"
        if len(significant) <= 3:
            return "pillar"
        return "slide"

    @staticmethod
    def _format_hebrew(**k: Any) -> str:
        symbol = k["symbol"]
        spot = k["spot"]
        cw = k["call_wall"]
        pw = k["put_wall"]
        gf = k["gamma_flip"]
        zdte = k["zero_dte"]
        oi = k["oi_strike"]
        regime = k["regime"]
        cw_dist = k["cw_dist"]
        pw_dist = k["pw_dist"]

        regime_he = (
            "חיובי (Positive Gamma) 🟢"
            if regime == "positive"
            else "שלילי (Negative Gamma) 🔴"
        )
        strategy = (
            "מכירת פרמיה: Iron Condor / Credit Spreads"
            if regime == "positive"
            else "קניית פרמיה: Debit Spreads / הימנע ממכירה"
        )
        cw_str = f"${cw:,.0f} ({cw_dist:+.1f}%)" if cw and cw_dist is not None else "—"
        pw_str = f"${pw:,.0f} ({pw_dist:+.1f}%)" if pw and pw_dist is not None else "—"
        gf_str = f"${gf:,.0f}" if gf else "—"
        zdte_str = f"${zdte:,.0f}" if zdte else "—"
        oi_str = f"${oi:,.0f}" if oi else "—"

        return (
            f"📊 ניתוח GEX – {symbol}\n"
            f"💰 ספוט: ${spot:,.2f}\n\n"
            "🔑 רמות מפתח:\n"
            f"🟢 Call Wall: {cw_str}\n"
            f"🔴 Put Wall: {pw_str}\n"
            f"⚡ Gamma Flip: {gf_str}\n"
            f"🎯 0DTE Magnet: {zdte_str}\n"
            f"📌 Highest OI: {oi_str}\n\n"
            f"📈 משטר: {regime_he}\n\n"
            "💡 אסטרטגיה:\n"
            f"{strategy}\n\n"
            "מקור: FlashAlpha"
        )

    @staticmethod
    def _error_message_hebrew(err: str, symbol: str) -> str:
        if err == "plan_required":
            return f"⚠️ {symbol} דורש שדרוג תוכנית ב-FlashAlpha (Basic ל-ETF/Index)."
        if err == "rate_limit":
            return "⚠️ הגעת למגבלת בקשות יומית ב-FlashAlpha. נסה מאוחר יותר."
        if err == "not_found":
            return f"⚠️ לא נמצאו נתונים ל-{symbol}."
        return f"⚠️ שגיאה בקבלת נתונים ל-{symbol}."


__all__ = ["FlashAlphaTool"]
