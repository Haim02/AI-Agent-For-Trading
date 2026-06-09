"""FlashAlpha Lab API client.

Clean REST API for GEX, key levels, 0DTE data, and max pain. Used as the
primary source by ``GEXEngine``; UW / Massive / yfinance remain as fallbacks
for tickers the user's plan doesn't cover.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from utils.time_helper import ISRAEL_TZ

logger = logging.getLogger(__name__)


# ───────────────────────── module-level cache ─────────────────────────
# FlashAlpha free tier is 5 calls/day, basic is 100/day. Without caching the
# chart endpoint alone burns the quota in minutes. 1h TTL is long enough to
# stay within budget and short enough that walls/flips refresh during a session.
_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_TTL = 3600  # seconds


def _cache_get(key: str) -> Optional[dict]:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    data, ts = entry
    if time.time() - ts < _CACHE_TTL:
        logger.info("Cache HIT: %s", key)
        return data
    del _CACHE[key]
    return None


def _cache_set(key: str, data: dict) -> None:
    _CACHE[key] = (data, time.time())
    logger.info("Cache SET: %s", key)


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

        cache_key = f"{endpoint}_{str(params or {})}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    f"{self.BASE_URL}{endpoint}",
                    headers=self.headers,
                    params=params or {},
                )
            if resp.status_code == 200:
                data = resp.json()
                _cache_set(cache_key, data)
                return data
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


    # ───────────────────────── narrative ─────────────────────────

    async def get_narrative(self, symbol: str) -> Optional[dict]:
        """AI-generated 7-section briefing. Requires Growth plan+."""
        return await self._get(f"/v1/exposure/narrative/{symbol}")

    async def get_narrative_hebrew(self, symbol: str) -> dict:
        """Narrative + per-section Hebrew translation + chat-ready message."""
        data = await self.get_narrative(symbol)
        if not data or "error" in data:
            err = (data or {}).get("error", "no_data") if data else "no_data"
            return {
                "error": err,
                "symbol": symbol,
                "message": self._error_message_hebrew(err, symbol),
            }

        narrative = data.get("narrative", {}) or {}
        spot = float(data.get("underlying_price") or 0)

        sections_en = {
            "regime": narrative.get("regime", ""),
            "gex_change": narrative.get("gex_change", ""),
            "key_levels": narrative.get("key_levels", ""),
            "flow": narrative.get("flow", ""),
            "vanna": narrative.get("vanna", ""),
            "charm": narrative.get("charm", ""),
            "zero_dte": narrative.get("zero_dte", ""),
            "outlook": narrative.get("outlook", ""),
        }

        from tools.translate import translate_to_hebrew

        sections_he: dict[str, str] = {}
        for key, text in sections_en.items():
            sections_he[key] = (await translate_to_hebrew(text)) if text else "—"

        raw = narrative.get("data", {}) or {}
        regime_label = str(raw.get("regime") or "")
        regime_emoji = (
            "🟢" if "positive" in regime_label.lower()
            else "🔴" if "negative" in regime_label.lower()
            else "🟡"
        )

        formatted = (
            f"📊 ניתוח מלא – {symbol}\n"
            f"💰 ספוט: ${spot:,.2f}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{regime_emoji} משטר Gamma\n"
            f"{sections_he['regime']}\n\n"
            "📈 שינוי יומי\n"
            f"{sections_he['gex_change']}\n\n"
            "🎯 רמות מפתח\n"
            f"{sections_he['key_levels']}\n\n"
            "🌊 תזרים אופציות\n"
            f"{sections_he['flow']}\n\n"
            "🌀 Vanna\n"
            f"{sections_he['vanna']}\n\n"
            "⏳ Charm\n"
            f"{sections_he['charm']}\n\n"
            "⚡ 0DTE\n"
            f"{sections_he['zero_dte']}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔮 תחזית\n"
            f"{sections_he['outlook']}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "מקור: FlashAlpha"
        )

        return {
            "symbol": symbol,
            "spot_price": spot,
            "sections_hebrew": sections_he,
            "raw_data": raw,
            "formatted_message": formatted,
            "source": "flashalpha_narrative",
        }

    # ───────────────────────── flow signals ─────────────────────────

    async def get_flow_signals(
        self,
        symbol: str,
        min_score: int = 70,
        intent: Optional[str] = None,
        structure: Optional[str] = None,
        window_minutes: int = 240,
        limit: int = 25,
        expiry: Optional[str] = None,
    ) -> Optional[dict]:
        """Scored unusual-flow feed. Requires Alpha plan."""
        params: dict[str, Any] = {
            "minScore": min_score,
            "windowMinutes": window_minutes,
            "limit": limit,
        }
        if intent:
            params["intent"] = intent
        if structure:
            params["structure"] = structure
        if expiry:
            params["expiry"] = expiry
        return await self._get(f"/v1/flow/signals/{symbol}", params=params)

    async def get_top_signals_hebrew(
        self, symbol: str, min_score: int = 70
    ) -> dict:
        """Top scored signals formatted as a Hebrew chat message."""
        data = await self.get_flow_signals(
            symbol=symbol, min_score=min_score, window_minutes=240, limit=10
        )
        if not data or "error" in data:
            err = (data or {}).get("error", "no_data") if data else "no_data"
            return {
                "error": err,
                "symbol": symbol,
                "message": self._error_message_hebrew(err, symbol),
            }

        signals = data.get("signals", []) or []
        spot = float(data.get("underlying_price") or 0)
        chain = data.get("chain", {}) or {}

        if not signals:
            return {
                "symbol": symbol,
                "count": 0,
                "signals": [],
                "chain": chain,
                "formatted_message": (
                    f"לא נמצאו סיגנלים חזקים ל-{symbol} (ניקוד ≥ {min_score})"
                ),
            }

        lines: list[str] = [
            f"🐋 סיגנלי Flow חזקים – {symbol}",
            f"💰 ספוט: ${spot:,.2f}",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "📊 רמות שוק:",
            f"🟢 Call Wall: ${(chain.get('call_wall') or 0):,.0f}",
            f"🔴 Put Wall: ${(chain.get('put_wall') or 0):,.0f}",
            f"⚡ Gamma Flip: ${(chain.get('gamma_flip') or 0):,.0f}",
            f"🎯 Max Pain: ${(chain.get('max_pain') or 0):,.0f}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            f"🎯 {len(signals)} סיגנלים (ניקוד ≥ {min_score})",
            "━━━━━━━━━━━━━━━━━━",
        ]

        intent_labels = {
            "bullish": "🟢 שורי",
            "bearish": "🔴 דובי",
            "neutral": "🟡 ניטרלי",
        }

        for i, sig in enumerate(signals[:8], start=1):
            intent = sig.get("intent", "neutral")
            intent_emoji = intent_labels.get(intent, "🟡")

            structure = sig.get("structure", "")
            struct_emoji = "⚡ SWEEP" if structure == "sweep" else "🏦 BLOCK"

            tag_emojis: list[str] = []
            tags = sig.get("tags", []) or []
            if "whale" in tags:
                tag_emojis.append("🐋")
            if "golden" in tags:
                tag_emojis.append("⭐")
            if "0dte" in tags:
                tag_emojis.append("⏰")
            if "opening" in tags:
                tag_emojis.append("🆕")
            tag_str = "".join(tag_emojis)

            right = sig.get("right", "")
            right_he = "Call" if right == "C" else "Put"
            strike = sig.get("strike") or 0
            expiry = sig.get("expiry", "")
            dte = sig.get("dte", 0)
            premium = sig.get("premium") or 0
            score = sig.get("score", 0)
            conviction = sig.get("conviction", "")
            aggressor = sig.get("aggressor", "")
            enrich = sig.get("enrichment", {}) or {}
            iv = enrich.get("iv")
            delta = enrich.get("delta")
            moneyness = enrich.get("moneyness", "")

            block = (
                f"\n{i}. {struct_emoji} {tag_str}\n"
                f"   {intent_emoji} {right_he} ${strike:,.0f}\n"
                f"   📅 פקיעה: {expiry} ({dte}d)\n"
                f"   💰 פרמיה: ${premium:,.0f}\n"
                f"   🎯 ניקוד: {score}/100 ({conviction})\n"
                f"   📍 {aggressor} | {moneyness}"
            )
            if iv is not None:
                block += f" | IV: {iv*100:.1f}%"
            if delta is not None:
                block += f" | Δ: {delta:.2f}"
            lines.append(block)

        lines.append("\n\n━━━━━━━━━━━━━━━━━━\nמקור: FlashAlpha")

        return {
            "symbol": symbol,
            "spot_price": spot,
            "count": len(signals),
            "signals": signals,
            "chain": chain,
            "formatted_message": "\n".join(lines),
            "source": "flashalpha_signals",
        }


__all__ = ["FlashAlphaTool"]
