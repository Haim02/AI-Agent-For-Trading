import asyncio
import logging
import math
import os
from datetime import datetime
from typing import Optional

import yfinance as yf

from scrapers.menthorq_scraper import GEXData
from tools.massive_tool import MassiveTool

logger = logging.getLogger(__name__)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _black_scholes_gamma(
    spot: float,
    strike: float,
    time_to_expiry: float,
    iv: float,
    risk_free: float = 0.04,
) -> float:
    """Standard Black-Scholes gamma. Returns 0 for degenerate inputs."""
    if spot <= 0 or strike <= 0 or time_to_expiry <= 0 or iv <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (risk_free + 0.5 * iv * iv) * time_to_expiry) / (
        iv * math.sqrt(time_to_expiry)
    )
    return _norm_pdf(d1) / (spot * iv * math.sqrt(time_to_expiry))


def _resolve_symbols(ticker: str) -> tuple[str, str, str]:
    """Return (display_ticker, spot_yf_symbol, options_yf_symbol)."""
    upper = ticker.upper().strip()
    if upper in {"SPX", "^SPX", "SPXW", "^GSPC"}:
        return "SPX", "^GSPC", "^SPX"
    return upper, upper, upper


class GEXCalculator:
    """Compute per-strike and aggregate GEX from a yfinance options chain."""

    def __init__(self, risk_free_rate: float = 0.04) -> None:
        self.risk_free_rate = risk_free_rate

    def calculate_gex(self, ticker: str = "SPX") -> GEXData:
        display_ticker, yf_symbol, options_symbol = _resolve_symbols(ticker)

        # 1) Spot price
        stock = yf.Ticker(yf_symbol)
        spot_price = self._get_spot_price(stock)

        # 2) Options chain (fallback: SPY × 10 for SPX)
        options_ticker = yf.Ticker(options_symbol)
        expirations: list[str] = []
        try:
            expirations = list(options_ticker.options or [])
        except Exception:  # noqa: BLE001
            logger.exception("options() failed for %s", options_symbol)

        if not expirations and display_ticker == "SPX":
            logger.warning("No SPX options on %s – falling back to SPY × 10", options_symbol)
            spy = yf.Ticker("SPY")
            try:
                expirations = list(spy.options or [])
            except Exception:  # noqa: BLE001
                logger.exception("SPY options fallback failed")
                expirations = []
            spy_spot = self._get_spot_price(spy)
            if spy_spot > 0:
                spot_price = spy_spot * 10.0
            options_ticker = spy

        if spot_price <= 0:
            logger.warning("No spot price for %s", display_ticker)
            return GEXData(
                ticker=display_ticker,
                regime="unknown",
                dealer_behavior="לא ניתן לחשב",
            )

        if not expirations:
            return GEXData(
                ticker=display_ticker,
                spot_price=spot_price,
                regime="unknown",
                dealer_behavior="לא ניתן לחשב",
            )

        # 3) Nearest weekly expirations within 45 days (top 3)
        now = datetime.now()
        valid_exps: list[str] = []
        for exp in expirations:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d")
            except ValueError:
                continue
            days = (exp_date - now).days
            if 0 <= days <= 45:
                valid_exps.append(exp)
        use_exps = valid_exps[:3] if valid_exps else expirations[:3]

        # 4) Aggregate GEX per strike (calls positive, puts negative)
        gex_by_strike: dict[float, float] = {}
        call_gex_by_strike: dict[float, float] = {}
        put_gex_by_strike: dict[float, float] = {}
        using_spy_proxy = options_ticker.ticker == "SPY" and display_ticker == "SPX"
        scale = 10.0 if using_spy_proxy else 1.0

        for exp in use_exps:
            try:
                chain = options_ticker.option_chain(exp)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to load options for %s exp=%s", display_ticker, exp)
                continue

            ttm = self._time_to_expiry(exp)

            for _, row in chain.calls.iterrows():
                strike = float(row.get("strike", 0) or 0) * scale
                oi = float(row.get("openInterest", 0) or 0)
                gamma = float(row.get("gamma") or 0)
                if gamma <= 0:
                    iv = float(row.get("impliedVolatility") or 0)
                    gamma = _black_scholes_gamma(spot_price, strike, ttm, iv, self.risk_free_rate)
                call_gex = gamma * oi * 100.0 * spot_price
                gex_by_strike[strike] = gex_by_strike.get(strike, 0.0) + call_gex
                call_gex_by_strike[strike] = call_gex_by_strike.get(strike, 0.0) + call_gex

            for _, row in chain.puts.iterrows():
                strike = float(row.get("strike", 0) or 0) * scale
                oi = float(row.get("openInterest", 0) or 0)
                gamma = float(row.get("gamma") or 0)
                if gamma <= 0:
                    iv = float(row.get("impliedVolatility") or 0)
                    gamma = _black_scholes_gamma(spot_price, strike, ttm, iv, self.risk_free_rate)
                put_gex = gamma * oi * 100.0 * spot_price * -1.0
                gex_by_strike[strike] = gex_by_strike.get(strike, 0.0) + put_gex
                put_gex_by_strike[strike] = put_gex_by_strike.get(strike, 0.0) + put_gex

        if not gex_by_strike:
            # Mock data so downstream consumers still get coherent numbers
            step = 50.0 if display_ticker == "SPX" else max(1.0, round(spot_price * 0.005, 0))
            return GEXData(
                ticker=display_ticker,
                timestamp=datetime.now(),
                spot_price=round(spot_price, 2),
                gex_total=0.0,
                dex_total=0.0,
                gamma_flip_level=round(spot_price * 0.98, 0),
                call_wall=round(spot_price * 1.03 / step) * step,
                put_wall=round(spot_price * 0.97 / step) * step,
                top_gex_strikes=[],
                regime="unknown",
                dealer_behavior="לא ניתן לחשב",
            )

        total_gex = sum(gex_by_strike.values())
        gamma_flip = self._find_gamma_flip(gex_by_strike, spot_price)

        call_above = {s: g for s, g in call_gex_by_strike.items() if s > spot_price and g > 0}
        call_wall = max(call_above, key=call_above.get) if call_above else spot_price * 1.02

        put_below = {s: g for s, g in put_gex_by_strike.items() if s < spot_price and g < 0}
        put_wall = min(put_below, key=put_below.get) if put_below else spot_price * 0.98

        top_strikes_sorted = sorted(
            gex_by_strike.items(), key=lambda kv: abs(kv[1]), reverse=True
        )[:5]
        top_strikes = [
            {
                "strike": strike,
                "gex_value": gex,
                "type": "call" if gex >= 0 else "put",
            }
            for strike, gex in top_strikes_sorted
        ]

        regime = "positive" if total_gex > 0 else "negative"
        dealer_behavior = (
            "קונים בירידות, מוכרים בעליות – שוק רגוע"
            if regime == "positive"
            else "מוכרים בירידות, קונים בעליות – שוק תנודתי"
        )

        return GEXData(
            ticker=display_ticker,
            timestamp=datetime.now(),
            spot_price=round(spot_price, 2),
            gex_total=round(total_gex / 1_000_000, 2),  # millions of $
            dex_total=0.0,
            gamma_flip_level=round(gamma_flip, 0),
            call_wall=round(call_wall, 0),
            put_wall=round(put_wall, 0),
            top_gex_strikes=top_strikes,
            regime=regime,
            dealer_behavior=dealer_behavior,
        )

    def get_key_levels(self, ticker: str = "SPX") -> dict:
        gex = self.calculate_gex(ticker)

        call_strikes = sorted(
            [s for s in gex.top_gex_strikes if s["gex_value"] > 0],
            key=lambda s: s["gex_value"],
            reverse=True,
        )
        put_strikes = sorted(
            [s for s in gex.top_gex_strikes if s["gex_value"] < 0],
            key=lambda s: s["gex_value"],
        )

        call_resistance = [s["strike"] for s in call_strikes[:3]]
        put_support = [s["strike"] for s in put_strikes[:3]]

        between_walls = (
            gex.put_wall > 0
            and gex.call_wall > 0
            and gex.put_wall <= gex.spot_price <= gex.call_wall
        )

        return {
            "ticker": gex.ticker,
            "spot_price": gex.spot_price,
            "gamma_flip": gex.gamma_flip_level,
            "call_wall": gex.call_wall,
            "put_wall": gex.put_wall,
            "call_resistance_levels": call_resistance,
            "put_support_levels": put_support,
            "regime": gex.regime,
            "zero_dte_safe": gex.regime == "positive" and between_walls,
        }

    @staticmethod
    def _get_spot_price(tk) -> float:
        try:
            fast = getattr(tk, "fast_info", None)
            if fast and getattr(fast, "last_price", None):
                return float(fast.last_price)
        except Exception:  # noqa: BLE001
            pass
        try:
            hist = tk.history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:  # noqa: BLE001
            logger.exception("Spot price fetch failed")
        return 0.0

    @staticmethod
    def _time_to_expiry(exp_date: str) -> float:
        try:
            target = datetime.strptime(exp_date, "%Y-%m-%d")
        except ValueError:
            return 0.02
        days = max((target - datetime.utcnow()).days, 1)
        return days / 365.0

    async def calculate_gex_async(self, ticker: str = "SPX") -> dict:
        """Massive-first GEX. Falls back to yfinance-based ``calculate_gex``."""
        if os.getenv("MASSIVE_API_KEY"):
            massive = MassiveTool()
            try:
                data = await massive.calculate_precise_gex(ticker)
                if data:
                    logger.info("Using Massive GEX for %s", ticker)
                    return data
            except Exception as exc:  # noqa: BLE001
                logger.warning("Massive GEX failed for %s: %s – using yfinance", ticker, exc)
            finally:
                await massive.close()

        gex = await asyncio.to_thread(self.calculate_gex, ticker)
        return {
            "ticker": gex.ticker,
            "spot_price": gex.spot_price,
            "gex_total": gex.gex_total,
            "gamma_flip": gex.gamma_flip_level,
            "call_wall": gex.call_wall,
            "put_wall": gex.put_wall,
            "regime": gex.regime,
            "top_strikes": gex.top_gex_strikes,
            "source": "yfinance",
        }

    @staticmethod
    def _find_gamma_flip(per_strike: dict[float, float], spot_price: float) -> float:
        if not per_strike:
            return spot_price
        sorted_strikes = sorted(per_strike.keys())
        cumulative = 0.0
        gamma_flip = spot_price
        for strike in sorted_strikes:
            prev = cumulative
            cumulative += per_strike[strike]
            if prev < 0 and cumulative >= 0:
                return strike
            if prev > 0 and cumulative <= 0:
                return strike
        return gamma_flip


__all__ = ["GEXCalculator"]
