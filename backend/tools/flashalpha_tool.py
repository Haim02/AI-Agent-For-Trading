"""
FlashAlpha options analytics using official Python SDK.
pip install flashalpha

Replaces custom HTTP client with:
- Official error handling (TierRestrictedError etc.)
- Auto retry on rate limits
- Cleaner code

Free plan: 5 req/day - GEX, levels, greeks, IV, quotes
Basic: 100/day - + SPX/VIX/NDX/RUT index symbols
Growth: 2500/day - + 0DTE, narrative, volatility, Kelly
Alpha: unlimited - + advanced vol (SVI, variance)
"""

import os
import time
import asyncio
import logging
from typing import Optional
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)
ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")

# ─── 1-hour in-memory cache ──────────────────────
_CACHE: dict = {}
_CACHE_TTL = 3600  # seconds


def _cache_get(key: str, ttl: int = _CACHE_TTL) -> Optional[dict]:
    if key in _CACHE:
        data, ts = _CACHE[key]
        if time.time() - ts < ttl:
            logger.debug(f"Cache HIT: {key[:60]}")
            return data
        del _CACHE[key]
    return None


def _cache_set(key: str, data: dict):
    _CACHE[key] = (data, time.time())


def _make_key(*args, **kwargs) -> str:
    parts = [str(a) for a in args]
    parts += [f"{k}={v}" for k, v in sorted(kwargs.items())]
    return "_".join(parts)


class FlashAlphaTool:
    """
    Wrapper around the official FlashAlpha Python SDK.
    All methods are async (run SDK in thread pool).
    """

    def __init__(self):
        self.api_key = os.getenv("FLASHALPHA_API_KEY", "")
        self._fa = None  # Lazy init

    def _get_client(self):
        """Get or create FlashAlpha client"""
        if not self.api_key:
            raise ValueError("FLASHALPHA_API_KEY not set")
        if self._fa is None:
            from flashalpha import FlashAlpha
            self._fa = FlashAlpha(self.api_key)
        return self._fa

    async def _run(self, func, *args, **kwargs):
        """Run a sync SDK call off the event loop with a hard timeout."""
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args, **kwargs),
            timeout=20.0,
        )

    async def _call(
        self,
        method_name: str,
        *args,
        cache_ttl: int = 3600,
        **kwargs,
    ) -> Optional[dict]:
        """
        Generic cached SDK call.
        Returns None on error, error dict on plan issues.
        """
        if not self.api_key:
            return None

        cache_key = _make_key(method_name, *args, **kwargs)
        cached = _cache_get(cache_key, ttl=cache_ttl)
        if cached is not None:
            return cached

        try:
            fa = self._get_client()
            method = getattr(fa, method_name)
            result = await self._run(method, *args, **kwargs)
            if result:
                _cache_set(cache_key, result)
            return result

        except Exception as e:
            try:
                from flashalpha import (
                    TierRestrictedError,
                    RateLimitError,
                    AuthenticationError,
                    NotFoundError,
                )
                if isinstance(e, TierRestrictedError):
                    logger.warning(
                        f"FlashAlpha plan required for {method_name}"
                    )
                    return {"error": "plan_required"}
                if isinstance(e, RateLimitError):
                    logger.warning("FlashAlpha rate limit hit")
                    return {"error": "rate_limit"}
                if isinstance(e, AuthenticationError):
                    logger.error("FlashAlpha auth failed")
                    return {"error": "auth_failed"}
                if isinstance(e, NotFoundError):
                    return {"error": "not_found"}
            except ImportError:
                pass
            logger.error(f"FlashAlpha {method_name} error: {e}")
            return None

    # ─── Exposure Analytics ───────────────────────

    async def get_levels(self, symbol: str) -> Optional[dict]:
        """Key levels: gamma_flip, call_wall, put_wall"""
        return await self._call("exposure_levels", symbol)

    async def get_gex(
        self,
        symbol: str,
        expiration: Optional[str] = None,
        min_oi: int = 0,
    ) -> Optional[dict]:
        """GEX by strike"""
        kwargs: dict = {}
        if expiration:
            kwargs["expiration"] = expiration
        if min_oi:
            kwargs["min_oi"] = min_oi
        return await self._call("gex", symbol, **kwargs)

    async def get_dex(self, symbol: str) -> Optional[dict]:
        """Delta Exposure by strike"""
        return await self._call("dex", symbol)

    async def get_vex(self, symbol: str) -> Optional[dict]:
        """Vanna Exposure by strike"""
        return await self._call("vex", symbol)

    async def get_chex(self, symbol: str) -> Optional[dict]:
        """Charm Exposure by strike"""
        return await self._call("chex", symbol)

    async def get_zero_dte(self, symbol: str) -> Optional[dict]:
        """0DTE analytics (Growth+)"""
        return await self._call("zero_dte", symbol)

    async def get_max_pain(self, symbol: str) -> Optional[dict]:
        """Max Pain levels"""
        return await self._call("max_pain", symbol)

    async def get_narrative(self, symbol: str) -> Optional[dict]:
        """AI narrative (Growth+)"""
        return await self._call("narrative", symbol)

    async def get_volatility(self, symbol: str) -> Optional[dict]:
        """Volatility analytics (Growth+)"""
        return await self._call("volatility", symbol)

    # ─── Pricing Tools ───────────────────────────

    async def get_greeks(
        self,
        spot: float,
        strike: float,
        dte: int,
        sigma: float,
        opt_type: str = "call",
        rate: float = 0.05,
    ) -> Optional[dict]:
        """
        Full BSM Greeks - 15 greeks!
        1st: delta, gamma, theta, vega, rho
        2nd: vanna, charm, vomma, veta
        3rd: speed, zomma, color, ultima
        FREE - no rate limit concern
        """
        cache_key = _make_key(
            "greeks", spot, strike, dte, sigma, opt_type, rate
        )
        cached = _cache_get(cache_key)
        if cached:
            return cached

        try:
            fa = self._get_client()
            result = await self._run(
                fa.greeks,
                spot=spot,
                strike=strike,
                dte=dte,
                sigma=sigma,
                type=opt_type,
                r=rate,
            )
            if result:
                _cache_set(cache_key, result)
            return result
        except Exception as e:
            logger.error(f"Greeks error: {e}")
            return None

    async def get_iv(
        self,
        spot: float,
        strike: float,
        dte: int,
        price: float,
        opt_type: str = "call",
    ) -> Optional[dict]:
        """
        Implied Volatility solver.
        FREE - no rate limit concern
        """
        try:
            fa = self._get_client()
            return await self._run(
                fa.iv,
                spot=spot,
                strike=strike,
                dte=dte,
                price=price,
                type=opt_type,
            )
        except Exception as e:
            logger.error(f"IV error: {e}")
            return None

    async def get_kelly(
        self,
        spot: float,
        strike: float,
        dte: int,
        sigma: float,
        premium: float,
        mu: float = 0.10,
    ) -> Optional[dict]:
        """
        Kelly Criterion optimal sizing (Growth+).
        Uses numerical integration over lognormal.
        """
        return await self._call(
            "kelly",
            spot=spot,
            strike=strike,
            dte=dte,
            sigma=sigma,
            premium=premium,
            mu=mu,
        )

    # ─── Market Data ─────────────────────────────

    async def get_stock_quote(self, ticker: str) -> Optional[dict]:
        """Live stock quote"""
        return await self._call("stock_quote", ticker, cache_ttl=60)

    async def get_account(self) -> Optional[dict]:
        """Account info: plan, usage, quota"""
        try:
            fa = self._get_client()
            return await self._run(fa.account)
        except Exception as e:
            logger.error(f"Account error: {e}")
            return None

    # ─── Full Analysis ────────────────────────────

    async def get_full_analysis(self, symbol: str) -> dict:
        """
        Combined: levels + gex + optional 0dte
        Returns Hebrew-formatted result.
        """
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
                "message": self._error_msg(err, symbol),
            }

        lv = levels_data.get("levels", {}) or {}
        spot = float(levels_data.get("underlying_price") or 0)
        call_wall = lv.get("call_wall")
        put_wall = lv.get("put_wall")
        gamma_flip = lv.get("gamma_flip")
        oi_strike = lv.get("highest_oi_strike")
        zero_dte = lv.get("zero_dte_magnet")

        net_gex = 0.0
        regime = "positive"
        top_strikes: list = []

        if gex_data and "error" not in (gex_data or {}):
            net_gex = float(gex_data.get("net_gex") or 0)
            regime_raw = (
                gex_data.get("net_gex_label")
                or gex_data.get("regime", "positive")
                or ""
            )
            regime = (
                "positive"
                if "positive" in str(regime_raw).lower()
                else "negative"
            )
            sorted_strikes = sorted(
                gex_data.get("strikes", []) or [],
                key=lambda s: abs(s.get("net_gex") or 0),
                reverse=True,
            )
            for s in sorted_strikes[:8]:
                strike = s.get("strike") or 0
                gex_val = float(s.get("net_gex") or 0)
                if not strike or not spot:
                    continue
                top_strikes.append(
                    {
                        "strike": strike,
                        "gex_billion": round(gex_val / 1e9, 3),
                        "type": (
                            "call_wall" if gex_val > 0 else "put_wall"
                        ),
                        "distance_pct": round(
                            (strike - spot) / spot * 100, 2
                        ),
                    }
                )

        cw_dist = (
            (call_wall - spot) / spot * 100
            if call_wall and spot
            else None
        )
        pw_dist = (
            (spot - put_wall) / spot * 100
            if put_wall and spot
            else None
        )

        hebrew = self._format_hebrew(
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
            net_gex=net_gex,
        )

        return {
            "ticker": symbol,
            "spot_price": round(spot, 2),
            "call_wall": call_wall,
            "put_wall": put_wall,
            "gamma_flip": gamma_flip,
            "highest_oi_strike": oi_strike,
            "zero_dte_magnet": zero_dte,
            "call_wall_distance_pct": (
                round(cw_dist, 2) if cw_dist is not None else None
            ),
            "put_wall_distance_pct": (
                round(pw_dist, 2) if pw_dist is not None else None
            ),
            "regime": regime,
            "total_gex": round(net_gex / 1e9, 3),
            "top_strikes": top_strikes,
            "gex_profile": self._profile(top_strikes, spot),
            "analysis_hebrew": hebrew,
            "source": "flashalpha_sdk",
            "timestamp": datetime.now(ISRAEL_TZ).strftime(
                "%H:%M | %d/%m/%Y"
            ),
        }

    def _profile(self, top_strikes: list, spot: float) -> str:
        if not top_strikes or not spot:
            return "unknown"
        near = [
            s
            for s in top_strikes
            if abs(s.get("distance_pct", 99)) < 1.0
        ]
        if near:
            return "pin"
        sig = [
            s
            for s in top_strikes
            if abs(s.get("gex_billion", 0)) > 0.1
        ]
        if len(sig) <= 1:
            return "wall"
        if len(sig) <= 3:
            return "pillar"
        return "slide"

    def _format_hebrew(self, **k) -> str:
        spot = k["spot"]
        regime_emoji = "🟢" if k["regime"] == "positive" else "🔴"

        def _money(val) -> str:
            return f"${val:,.0f}" if val else "—"

        cw_str = (
            f"${k['call_wall']:,.0f} ({k['cw_dist']:+.1f}%)"
            if k["call_wall"] and k["cw_dist"] is not None
            else "—"
        )
        pw_str = (
            f"${k['put_wall']:,.0f} ({k['pw_dist']:+.1f}%)"
            if k["put_wall"] and k["pw_dist"] is not None
            else "—"
        )
        strategy = (
            "מכירת פרמיה: Iron Condor / Credit Spreads"
            if k["regime"] == "positive"
            else "קניית פרמיה: Debit Spreads – הימנע ממכירה!"
        )

        return (
            f"📊 ניתוח GEX – {k['symbol']}\n"
            f"💰 ספוט: ${spot:,.2f}\n\n"
            "🔑 רמות מפתח:\n"
            f"🟢 Call Wall: {cw_str}\n"
            f"🔴 Put Wall: {pw_str}\n"
            f"⚡ Gamma Flip: {_money(k['gamma_flip'])}\n"
            f"🎯 0DTE Magnet: {_money(k['zero_dte'])}\n"
            f"📌 Max OI: {_money(k['oi_strike'])}\n\n"
            f"{regime_emoji} משטר: {k['regime'].upper()}\n"
            f"Net GEX: ${k['net_gex'] / 1e9:.2f}B\n\n"
            f"💡 {strategy}\n"
            "מקור: FlashAlpha SDK"
        )

    def _error_msg(self, err: str, symbol: str) -> str:
        msgs = {
            "plan_required": (
                f"⚠️ {symbol} דורש שדרוג תוכנית."
            ),
            "rate_limit": (
                "⚠️ הגעת למגבלת בקשות יומית. נסה מאוחר יותר."
            ),
            "not_found": f"⚠️ לא נמצא: {symbol}",
            "auth_failed": (
                "⚠️ API Key שגוי. בדוק FLASHALPHA_API_KEY"
            ),
        }
        return msgs.get(err, f"⚠️ שגיאה בקבלת נתונים ל-{symbol}")

    async def get_narrative_hebrew(self, symbol: str) -> dict:
        """Narrative + per-section Hebrew translation + chat-ready message."""
        data = await self._call("narrative", symbol)
        if not data or "error" in data:
            err = (data or {}).get("error", "no_data") if data else "no_data"
            return {
                "error": err,
                "symbol": symbol,
                "message": self._error_msg(err, symbol),
            }

        narrative = data.get("narrative", {}) or {}
        spot = float(data.get("underlying_price") or 0)

        from tools.translate import translate_to_hebrew

        sections_he: dict = {}
        for key in [
            "regime",
            "gex_change",
            "key_levels",
            "flow",
            "vanna",
            "charm",
            "zero_dte",
            "outlook",
        ]:
            txt = narrative.get(key, "")
            sections_he[key] = (
                await translate_to_hebrew(txt) if txt else "—"
            )

        raw = narrative.get("data", {}) or {}
        regime = str(raw.get("regime") or "")
        regime_emoji = (
            "🟢"
            if "positive" in regime.lower()
            else "🔴"
            if "negative" in regime.lower()
            else "🟡"
        )

        formatted = (
            f"📊 ניתוח AI מלא – {symbol}\n"
            f"💰 ספוט: ${spot:,.2f}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{regime_emoji} משטר Gamma\n"
            f"{sections_he.get('regime', '—')}\n\n"
            "📈 שינוי יומי\n"
            f"{sections_he.get('gex_change', '—')}\n\n"
            "🎯 רמות מפתח\n"
            f"{sections_he.get('key_levels', '—')}\n\n"
            "🌊 תזרים אופציות\n"
            f"{sections_he.get('flow', '—')}\n\n"
            "🌀 Vanna\n"
            f"{sections_he.get('vanna', '—')}\n\n"
            "⏳ Charm\n"
            f"{sections_he.get('charm', '—')}\n\n"
            "⚡ 0DTE\n"
            f"{sections_he.get('zero_dte', '—')}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔮 תחזית\n"
            f"{sections_he.get('outlook', '—')}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "מקור: FlashAlpha SDK"
        )

        return {
            "symbol": symbol,
            "spot_price": spot,
            "sections_hebrew": sections_he,
            "raw_data": raw,
            "formatted_message": formatted,
            "source": "flashalpha_sdk_narrative",
        }

    async def get_flow_signals(
        self,
        symbol: str,
        min_score: int = 0,
        structure: Optional[str] = None,
        window_minutes: int = 240,
        limit: int = 50,
    ) -> Optional[dict]:
        """Flow signals.

        The FlashAlpha flow endpoint requires the Alpha plan; until then we
        build equivalent signals from yfinance unusual-options activity so the
        Telegram commands and scheduler jobs stay functional.
        """
        from services.flow_signals import get_unusual_options_signals

        return await get_unusual_options_signals(
            symbol, min_score=min_score, structure=structure, limit=limit
        )

    async def get_top_signals_hebrew(
        self, symbol: str, min_score: int = 70
    ) -> dict:
        from services.flow_signals import (
            format_signals_hebrew,
            get_unusual_options_signals,
        )

        payload = await get_unusual_options_signals(symbol, min_score=min_score, limit=8)
        payload["formatted_message"] = format_signals_hebrew(payload, symbol)
        return payload


__all__ = ["FlashAlphaTool"]
