"""Massive API client – professional stock and options data."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class MassiveTool:
    """Professional market data from Massive API."""

    BASE_URL = "https://api.massive.com/v1"
    WS_URL = "wss://stream.massive.com/v1"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("MASSIVE_API_KEY")
        if not self.api_key:
            logger.warning("MASSIVE_API_KEY not set – Massive calls will fail")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(headers=self.headers, timeout=30.0)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    # ─────────────────────────── stocks ───────────────────────────

    async def get_stock_quote(self, ticker: str) -> dict[str, Any]:
        try:
            response = await self.client.get(f"{self.BASE_URL}/stocks/quote/{ticker}")
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("Massive quote error %s: %s", ticker, exc)
            return {}
        return {
            "ticker": ticker,
            "price": data.get("price"),
            "bid": data.get("bid"),
            "ask": data.get("ask"),
            "volume": data.get("volume"),
            "change": data.get("change"),
            "change_pct": data.get("change_pct"),
            "timestamp": data.get("timestamp"),
        }

    async def get_stock_history(
        self,
        ticker: str,
        days: int = 30,
        interval: str = "1d",
    ) -> list[dict[str, Any]]:
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            response = await self.client.get(
                f"{self.BASE_URL}/stocks/history/{ticker}",
                params={
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "interval": interval,
                },
            )
            response.raise_for_status()
            return response.json().get("bars", []) or []
        except Exception as exc:  # noqa: BLE001
            logger.error("History error %s: %s", ticker, exc)
            return []

    # ─────────────────────────── options ───────────────────────────

    async def get_options_chain(
        self, ticker: str, expiration: Optional[str] = None
    ) -> dict[str, Any]:
        try:
            params: dict[str, Any] = {}
            if expiration:
                params["expiration"] = expiration
            response = await self.client.get(
                f"{self.BASE_URL}/options/chain/{ticker}", params=params
            )
            response.raise_for_status()
            return response.json() or {}
        except Exception as exc:  # noqa: BLE001
            logger.error("Options chain error %s: %s", ticker, exc)
            return {}

    async def get_option_quote(self, option_symbol: str) -> dict[str, Any]:
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/options/quote/{option_symbol}"
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("Option quote error %s: %s", option_symbol, exc)
            return {}
        return {
            "symbol": option_symbol,
            "bid": data.get("bid"),
            "ask": data.get("ask"),
            "last": data.get("last"),
            "volume": data.get("volume"),
            "open_interest": data.get("open_interest"),
            "iv": data.get("iv"),
            "delta": data.get("delta"),
            "gamma": data.get("gamma"),
            "theta": data.get("theta"),
            "vega": data.get("vega"),
            "rho": data.get("rho"),
        }

    async def get_unusual_options_activity(
        self, min_volume: int = 1000
    ) -> list[dict[str, Any]]:
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/options/unusual",
                params={"min_volume": min_volume},
            )
            response.raise_for_status()
            return response.json().get("trades", []) or []
        except Exception as exc:  # noqa: BLE001
            logger.error("Unusual activity error: %s", exc)
            return []

    async def get_options_flow(self, ticker: str) -> dict[str, Any]:
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/options/flow/{ticker}"
            )
            response.raise_for_status()
            data = response.json() or {}
        except Exception as exc:  # noqa: BLE001
            logger.error("Flow error %s: %s", ticker, exc)
            return {}

        trades = data.get("trades", []) or []
        calls_volume = sum(
            int(t.get("size", 0) or 0) for t in trades if t.get("type") == "call"
        )
        puts_volume = sum(
            int(t.get("size", 0) or 0) for t in trades if t.get("type") == "put"
        )
        total = calls_volume + puts_volume
        call_pct = (calls_volume / total * 100) if total else 50.0
        if call_pct > 65:
            sentiment = "bullish"
        elif call_pct < 35:
            sentiment = "bearish"
        else:
            sentiment = "neutral"
        largest = max(trades, key=lambda x: x.get("premium", 0) or 0, default={})
        return {
            "ticker": ticker,
            "calls_volume": calls_volume,
            "puts_volume": puts_volume,
            "call_put_ratio": round(calls_volume / puts_volume, 2) if puts_volume else 0,
            "sentiment": sentiment,
            "total_trades": len(trades),
            "largest_trade": largest,
        }

    # ─────────────────────────── precise GEX ───────────────────────────

    async def calculate_precise_gex(self, ticker: str = "SPX") -> dict[str, Any]:
        try:
            chain = await self.get_options_chain(ticker)
            if not chain:
                return {}

            spot_price = float(chain.get("underlying_price") or 0)
            if spot_price <= 0:
                return {}

            gex_by_strike: dict[float, float] = {}
            total_call_gex = 0.0
            total_put_gex = 0.0

            for option in chain.get("options", []) or []:
                try:
                    strike = float(option["strike"])
                    gamma = float(option.get("gamma") or 0)
                    oi = float(option.get("open_interest") or 0)
                    opt_type = option.get("type")
                except (KeyError, TypeError, ValueError):
                    continue

                gex_value = gamma * oi * 100.0 * spot_price
                if opt_type == "call":
                    total_call_gex += gex_value
                else:
                    gex_value = -gex_value
                    total_put_gex += gex_value
                gex_by_strike[strike] = gex_by_strike.get(strike, 0.0) + gex_value

            if not gex_by_strike:
                return {}

            sorted_strikes = sorted(
                gex_by_strike.items(), key=lambda kv: abs(kv[1]), reverse=True
            )
            call_strikes = {k: v for k, v in gex_by_strike.items() if k > spot_price and v > 0}
            put_strikes = {k: v for k, v in gex_by_strike.items() if k < spot_price and v < 0}
            call_wall = max(call_strikes.items(), key=lambda kv: kv[1], default=(spot_price * 1.02, 0.0))[0]
            put_wall = min(put_strikes.items(), key=lambda kv: kv[1], default=(spot_price * 0.98, 0.0))[0]

            cumulative = 0.0
            gamma_flip = spot_price
            for strike in sorted(gex_by_strike.keys()):
                prev = cumulative
                cumulative += gex_by_strike[strike]
                if prev * cumulative < 0:
                    gamma_flip = strike
                    break

            total_gex = total_call_gex + total_put_gex
            return {
                "ticker": ticker,
                "spot_price": round(spot_price, 2),
                "gex_total": round(total_gex / 1e9, 2),
                "gex_calls": round(total_call_gex / 1e9, 2),
                "gex_puts": round(total_put_gex / 1e9, 2),
                "gamma_flip": round(gamma_flip, 0),
                "call_wall": round(call_wall, 0),
                "put_wall": round(put_wall, 0),
                "regime": "positive" if total_gex > 0 else "negative",
                "top_strikes": [
                    {
                        "strike": s,
                        "gex": round(v / 1e9, 2),
                        "type": "call" if v > 0 else "put",
                    }
                    for s, v in sorted_strikes[:5]
                ],
                "timestamp": datetime.now().isoformat(),
                "source": "Massive API",
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Precise GEX error: %s", exc)
            return {}

    # ─────────────────────────── precise IV rank ───────────────────────────

    async def get_precise_iv_rank(self, ticker: str) -> dict[str, Any]:
        try:
            chain = await self.get_options_chain(ticker)
            spot = float(chain.get("underlying_price") or 0)
            if spot <= 0:
                return {}

            atm_options: list[dict[str, Any]] = []
            for opt in chain.get("options", []) or []:
                try:
                    exp = datetime.fromisoformat(opt["expiration"])
                except (KeyError, ValueError):
                    continue
                days_to_exp = (exp - datetime.now()).days
                if 25 <= days_to_exp <= 50 and abs(float(opt["strike"]) - spot) < spot * 0.02:
                    atm_options.append(opt)

            if not atm_options:
                return {}

            current_iv = sum(float(o.get("iv") or 0) for o in atm_options) / len(atm_options) * 100.0

            response = await self.client.get(
                f"{self.BASE_URL}/options/iv-history/{ticker}",
                params={"days": 365},
            )
            response.raise_for_status()
            history = response.json().get("iv_data", []) or []
            if not history:
                return {}

            iv_values = [float(d.get("iv") or 0) * 100 for d in history]
            iv_high = max(iv_values)
            iv_low = min(iv_values)
            if iv_high == iv_low:
                iv_rank = 50.0
            else:
                iv_rank = (current_iv - iv_low) / (iv_high - iv_low) * 100.0
            iv_rank = max(0.0, min(100.0, round(iv_rank, 1)))

            if iv_rank >= 80:
                signal, strength = "SELL", "חזק מאוד"
            elif iv_rank >= 50:
                signal, strength = "SELL", "בינוני"
            elif iv_rank < 25:
                signal, strength = "BUY", "בינוני"
            else:
                signal, strength = "NEUTRAL", "חלש"

            return {
                "ticker": ticker,
                "current_iv": round(current_iv, 2),
                "iv_52w_high": round(iv_high, 2),
                "iv_52w_low": round(iv_low, 2),
                "iv_rank": iv_rank,
                "signal": signal,
                "signal_strength": strength,
                "source": "Massive API",
                "accuracy": "professional",
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("IV Rank error %s: %s", ticker, exc)
            return {}

    async def close(self) -> None:
        try:
            await self.client.aclose()
        except Exception:  # noqa: BLE001
            logger.exception("MassiveTool close failed")


__all__ = ["MassiveTool"]
