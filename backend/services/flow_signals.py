"""Unusual-options flow signals assembled from yfinance chains.

Shared by the API routes and by FlashAlphaTool as the fallback when the
FlashAlpha Alpha-plan flow endpoint is unavailable. Scores each contract
0-100 by volume/OI ratio, premium size, distance from spot, and DTE.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from utils.time_helper import ISRAEL_TZ

logger = logging.getLogger(__name__)


def _row_to_signal(
    row: Any, right: str, spot: float, exp: str, dte: int
) -> Optional[dict[str, Any]]:
    try:
        vol = int(row.get("volume", 0) or 0)
        oi = int(row.get("openInterest", 1) or 1)
        strike = float(row["strike"])
        price = float(row.get("lastPrice", 0) or 0)
        if vol < 10 or price < 0.05:
            return None
        premium = price * vol * 100
        vol_oi = vol / max(oi, 1)
        dist = abs(strike - spot) / spot if spot else 0
        if right == "C":
            moneyness = "ITM" if strike < spot else "ATM" if dist < 0.01 else "OTM"
            intent = "bullish"
            default_delta = 0.3
        else:
            moneyness = "ITM" if strike > spot else "ATM" if dist < 0.01 else "OTM"
            intent = "bearish"
            default_delta = -0.3
        score = min(
            100,
            int(
                (min(vol_oi, 5) / 5) * 40
                + min(premium / 500_000, 1) * 35
                + (1 - min(dist, 0.1) / 0.1) * 15
                + (1 - min(dte, 30) / 30) * 10
            ),
        )
        tags: list[str] = []
        if premium > 1_000_000:
            tags.append("whale")
        if vol_oi > 1:
            tags.append("opening")
        if dte == 0:
            tags.append("0dte")
        return {
            "ts": datetime.now(ISRAEL_TZ).isoformat(),
            "expiry": exp,
            "strike": strike,
            "right": right,
            "side": "buy",
            "price": round(price, 2),
            "size": vol,
            "premium": round(premium),
            "dte": dte,
            "structure": "sweep" if vol_oi > 2 else "block",
            "aggressor": "above_ask" if vol_oi > 3 else "at_ask",
            "intent": intent,
            "score": score,
            "conviction": (
                "high" if score >= 75 else "medium" if score >= 50 else "low"
            ),
            "tags": tags,
            "enrichment": {
                "iv": float(row.get("impliedVolatility", 0.3) or 0.3),
                "delta": float(row.get("delta", default_delta) or default_delta),
                "moneyness": moneyness,
            },
        }
    except Exception:  # noqa: BLE001
        return None


def _fetch_sync(ticker: str) -> tuple[float, list[dict[str, Any]]]:
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        spot_hist = t.history(period="1d")
        if spot_hist.empty:
            return 0.0, []
        spot = float(spot_hist["Close"].iloc[-1])

        expirations = (t.options or [])[:3]
        if not expirations:
            return spot, []

        all_options: list[dict[str, Any]] = []
        for exp in expirations:
            try:
                chain = t.option_chain(exp)
            except Exception:  # noqa: BLE001
                continue

            try:
                exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                dte = (exp_dt - datetime.now()).days
            except Exception:  # noqa: BLE001
                dte = 0

            for _, row in chain.calls.iterrows():
                sig = _row_to_signal(row, "C", spot, exp, dte)
                if sig is not None:
                    all_options.append(sig)
            for _, row in chain.puts.iterrows():
                sig = _row_to_signal(row, "P", spot, exp, dte)
                if sig is not None:
                    all_options.append(sig)

        all_options.sort(key=lambda x: x["score"], reverse=True)
        return spot, all_options[:50]
    except Exception as exc:  # noqa: BLE001
        logger.error("yfinance options error for %s: %s", ticker, exc)
        return 0.0, []


async def get_unusual_options_signals(
    ticker: str,
    min_score: int = 0,
    structure: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return a FlashAlpha-shaped signals payload built from yfinance data."""
    ticker = (ticker or "SPY").upper().strip()
    # SPX options aren't available on yfinance – proxy through SPY.
    yf_ticker = {"SPX": "SPY", "NDX": "QQQ", "RUT": "IWM"}.get(ticker, ticker)

    try:
        spot, signals = await asyncio.wait_for(
            asyncio.to_thread(_fetch_sync, yf_ticker), timeout=30.0
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Options fetch timeout for %s: %s", yf_ticker, exc)
        spot, signals = 0.0, []

    if min_score > 0:
        signals = [s for s in signals if s["score"] >= min_score]
    if structure:
        signals = [s for s in signals if s["structure"] == structure]
    signals = signals[:limit]

    chain: dict[str, Any] = {}
    try:
        from tools.flashalpha_tool import FlashAlphaTool

        levels = await FlashAlphaTool().get_levels(yf_ticker)
        if levels and "levels" in levels:
            lv = levels["levels"]
            chain = {
                "call_wall": lv.get("call_wall"),
                "put_wall": lv.get("put_wall"),
                "gamma_flip": lv.get("gamma_flip"),
                "max_pain": lv.get("highest_oi_strike"),
            }
    except Exception:  # noqa: BLE001
        pass

    return {
        "symbol": ticker,
        "underlying_price": spot,
        "window_minutes": 240,
        "expiry": None,
        "chain": chain,
        "count": len(signals),
        "signals": signals,
        "source": "yfinance_unusual_options",
    }


def format_signals_hebrew(payload: dict[str, Any], ticker: str) -> str:
    """Chat-ready Hebrew digest of the top signals."""
    signals = payload.get("signals") or []
    spot = payload.get("underlying_price") or 0
    if not signals:
        return f"לא נמצאו סיגנלים חזקים ל-{ticker} כרגע."

    lines = [
        f"🐋 Flow Signals – {ticker}",
        f"💰 ספוט: ${spot:,.2f}",
        "━━━━━━━━━━━━━",
    ]
    intent_emoji = {"bullish": "🟢", "bearish": "🔴"}
    for i, sig in enumerate(signals[:8], start=1):
        right = "Call" if sig.get("right") == "C" else "Put"
        ie = intent_emoji.get(sig.get("intent", ""), "🟡")
        tags = sig.get("tags") or []
        whale = "🐋" if "whale" in tags else ""
        struct_e = "⚡" if sig.get("structure") == "sweep" else "🏦"
        lines.append(
            f"\n{i}. {struct_e}{whale} {ie} {right} ${sig.get('strike', 0):,.0f}\n"
            f"   פרמיה: ${(sig.get('premium') or 0):,.0f} | "
            f"ניקוד: {sig.get('score', 0)}/100\n"
            f"   {sig.get('expiry', '?')} ({sig.get('dte', 0)}d)"
        )
    return "\n".join(lines)


__all__ = ["get_unusual_options_signals", "format_signals_hebrew"]
