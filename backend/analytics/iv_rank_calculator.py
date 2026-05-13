"""IV Rank + IV Percentile calculator with optional Polygon historical IV."""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import httpx
import yfinance as yf

logger = logging.getLogger(__name__)


SP500_TOP_50 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
    "TSLA", "AMD", "NFLX", "SPY", "QQQ", "IWM",
    "BABA", "DIS", "BA", "GS", "JPM", "BAC",
    "XOM", "CVX", "WMT", "COST", "TGT", "HD",
    "UBER", "LYFT", "SNOW", "PLTR", "COIN", "HOOD",
    "F", "GM", "RIVN", "NIO", "SOFI", "DKNG",
    "ROKU", "SPOT", "SHOP", "SQ", "PYPL", "V", "MA",
    "MU", "INTC", "QCOM", "TSM", "ASML", "CRM", "NOW",
]


@dataclass
class IVRankResult:
    ticker: str
    current_iv: float = 0.0
    iv_52w_high: float = 0.0
    iv_52w_low: float = 0.0
    iv_rank: float = 0.0
    iv_percentile: float = 0.0
    signal: str = "NEUTRAL"
    signal_strength: str = "חלש"
    recommended_strategies: list[str] = field(default_factory=list)
    explanation: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    note: Optional[str] = None


# ───────────────────────── helpers ─────────────────────────

def _classify(iv_rank: float) -> tuple[str, str, list[str], str]:
    if iv_rank >= 80:
        return (
            "SELL",
            "חזק מאוד",
            ["Iron Condor", "Short Strangle", "Bull Put Spread", "Bear Call Spread"],
            "הזדמנות זהב! IV בשיא שנתי. השוק מגזים בציפיות. הפרמיות יקרות מאוד – זמן למכור.",
        )
    if iv_rank >= 50:
        return (
            "SELL",
            "בינוני",
            ["Iron Condor", "Credit Spread"],
            "IV גבוה מהממוצע. הפרמיות יקרות – עדיף למכור. השוק מתמחר פחד או אירוע קרוב.",
        )
    if iv_rank >= 35:
        return (
            "NEUTRAL",
            "חלש",
            ["Calendar Spread", "Diagonal Spread"],
            "IV ממוצע – אין יתרון ברור לקונים או למוכרים.",
        )
    if iv_rank >= 25:
        return (
            "NEUTRAL",
            "חלש",
            ["Debit Spread", "Calendar Spread"],
            "IV מתחת לממוצע. האופציות זולות יחסית.",
        )
    return (
        "BUY",
        "בינוני",
        ["Long Straddle", "Long Strangle", "Debit Spread", "Long Puts/Calls"],
        "IV נמוך מאוד! האופציות זולות – זמן לקנות. סיכון נמוך עם פוטנציאל לרווח אם IV יקפוץ פתאום.",
    )


def _atm_average_iv(tk: yf.Ticker, spot: float) -> Optional[float]:
    expirations = list(tk.options or [])
    if not expirations:
        return None
    today = datetime.utcnow().date()
    target = None
    for exp in expirations:
        try:
            dt = datetime.strptime(exp, "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (dt - today).days
        if 7 <= days <= 30:
            target = exp
            break
    if target is None:
        target = expirations[0]

    try:
        chain = tk.option_chain(target)
    except Exception:  # noqa: BLE001
        logger.exception("option_chain failed for expiry %s", target)
        return None

    def _nearest_iv(df) -> Optional[float]:
        if df is None or df.empty:
            return None
        df = df.copy()
        df["distance"] = (df["strike"] - spot).abs()
        df = df.sort_values("distance").head(3)
        ivs = [float(v) for v in df.get("impliedVolatility", []) if v and not math.isnan(float(v))]
        return sum(ivs) / len(ivs) if ivs else None

    call_iv = _nearest_iv(chain.calls)
    put_iv = _nearest_iv(chain.puts)
    parts = [v for v in (call_iv, put_iv) if v]
    if not parts:
        return None
    return (sum(parts) / len(parts)) * 100.0


def _weekly_realized_vol_series(tk: yf.Ticker) -> list[float]:
    try:
        hist = tk.history(period="1y")
    except Exception:  # noqa: BLE001
        logger.exception("history fetch failed")
        return []
    if hist.empty:
        return []
    closes = hist["Close"].dropna()
    if closes.empty:
        return []
    returns = closes.pct_change().dropna()
    if returns.empty:
        return []
    weekly = returns.resample("W")
    rv_series: list[float] = []
    for _, group in weekly:
        if len(group) < 2:
            continue
        rv = float(group.std()) * math.sqrt(252) * 100.0
        if math.isfinite(rv):
            rv_series.append(rv)
    return rv_series


def _daily_realized_vol_series(tk: yf.Ticker, window: int = 30) -> list[float]:
    try:
        hist = tk.history(period="1y")
    except Exception:  # noqa: BLE001
        return []
    if hist.empty:
        return []
    closes = hist["Close"].dropna()
    returns = closes.pct_change().dropna()
    if len(returns) < window + 1:
        return []
    rv = returns.rolling(window=window).std().dropna() * math.sqrt(252) * 100.0
    return [float(v) for v in rv.tolist() if math.isfinite(v)]


def _polygon_iv_history(ticker: str, api_key: str) -> Optional[list[float]]:
    """Best-effort historical IV via Polygon (IV30 indicator). Returns None on failure."""
    end = datetime.utcnow().date()
    start = end - timedelta(days=365)
    url = f"https://api.polygon.io/v3/snapshot/options/{ticker.upper()}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, params={"apiKey": api_key})
            if resp.status_code != 200:
                logger.debug("Polygon snapshot %s → %s", ticker, resp.status_code)
                return None
            data = resp.json()
    except Exception:  # noqa: BLE001
        logger.exception("Polygon request failed")
        return None
    # Polygon's free tier returns a snapshot; full historical IV needs the paid endpoint.
    # We pull what we can but if the response lacks history, signal None.
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return None
    iv_values: list[float] = []
    for item in results if isinstance(results, list) else []:
        iv = item.get("implied_volatility")
        if iv:
            iv_values.append(float(iv) * 100.0)
    return iv_values or None


# ───────────────────────── calculator ─────────────────────────

class IVRankCalculator:
    def __init__(self, polygon_api_key: Optional[str] = None) -> None:
        self.polygon_api_key = polygon_api_key or os.getenv("POLYGON_API_KEY")

    def calculate_iv_rank(self, ticker: str) -> Optional[IVRankResult]:
        ticker = ticker.upper().strip()
        result = IVRankResult(ticker=ticker)

        stock = yf.Ticker(ticker)

        # 1) Current IV from option chain (nearest expiry 7-30 days out)
        current_iv: Optional[float] = None
        try:
            expirations = list(stock.options or [])
            if not expirations:
                raise ValueError("No options available")

            target = datetime.now() + timedelta(days=20)
            nearest_exp = min(
                expirations,
                key=lambda x: abs(datetime.strptime(x, "%Y-%m-%d") - target),
            )

            chain = stock.option_chain(nearest_exp)
            calls = chain.calls
            puts = chain.puts

            current_price = None
            try:
                current_price = stock.info.get("regularMarketPrice")
            except Exception:  # noqa: BLE001
                current_price = None
            if not current_price:
                fast = getattr(stock, "fast_info", None)
                if fast is not None:
                    current_price = getattr(fast, "last_price", None)
            if not current_price:
                hist_1d = stock.history(period="1d")
                if not hist_1d.empty:
                    current_price = float(hist_1d["Close"].iloc[-1])
            if not current_price:
                raise ValueError("No price available")

            atm_call = calls.iloc[(calls["strike"] - current_price).abs().argsort()[:1]]
            atm_put = puts.iloc[(puts["strike"] - current_price).abs().argsort()[:1]]
            call_iv = float(atm_call["impliedVolatility"].values[0]) * 100
            put_iv = float(atm_put["impliedVolatility"].values[0]) * 100
            current_iv = (call_iv + put_iv) / 2
            result.note = "IV from option chain"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Options IV failed for %s: %s", ticker, exc)
            # Fallback: realized volatility from last 30 days
            try:
                hist = stock.history(period="30d")
                if hist.empty:
                    return None
                returns = hist["Close"].pct_change().dropna()
                if returns.empty:
                    return None
                current_iv = float(returns.std()) * math.sqrt(252) * 100
                result.note = "IV proxy: 30d realized vol"
            except Exception:  # noqa: BLE001
                logger.exception("Realized-vol fallback failed for %s", ticker)
                return None

        if current_iv is None or not math.isfinite(current_iv) or current_iv <= 0:
            return None
        result.current_iv = round(current_iv, 2)

        # 2) 52-week IV range from rolling 30-day realized volatility
        try:
            hist_1y = stock.history(period="1y")
        except Exception:  # noqa: BLE001
            logger.exception("1y history failed for %s", ticker)
            return None
        if hist_1y.empty:
            return None

        returns_1y = hist_1y["Close"].pct_change().dropna()
        rolling_vol = returns_1y.rolling(window=20).std() * (252 ** 0.5) * 100
        rolling_vol = rolling_vol.dropna()
        if rolling_vol.empty:
            return None

        iv_52w_high = float(rolling_vol.max())
        iv_52w_low = float(rolling_vol.min())
        result.iv_52w_high = round(iv_52w_high, 2)
        result.iv_52w_low = round(iv_52w_low, 2)

        if iv_52w_high == iv_52w_low:
            iv_rank = 50.0
        else:
            iv_rank = ((current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low)) * 100
            iv_rank = max(0.0, min(100.0, round(iv_rank, 1)))
        result.iv_rank = iv_rank

        # IV percentile: pct of rolling-vol observations below current IV
        below = int((rolling_vol < current_iv).sum())
        result.iv_percentile = round((below / len(rolling_vol)) * 100.0, 1)

        signal, strength, strategies, explanation = _classify(iv_rank)
        result.signal = signal
        result.signal_strength = strength
        result.recommended_strategies = strategies
        result.explanation = explanation
        return result

    def scan_high_iv_rank(
        self,
        tickers: list[str],
        min_iv_rank: float = 50,
    ) -> list[IVRankResult]:
        results: list[IVRankResult] = []
        total = len(tickers)
        for idx, ticker in enumerate(tickers, start=1):
            try:
                logger.info("סורק %d/%d: %s", idx, total, ticker)
                result = self.calculate_iv_rank(ticker)
            except Exception:  # noqa: BLE001
                logger.exception("IV rank failed for %s", ticker)
                continue
            if result is None:
                continue
            if result.iv_rank >= min_iv_rank:
                results.append(result)
            if idx % 5 == 0:
                time.sleep(1.0)
        results.sort(key=lambda r: r.iv_rank, reverse=True)
        return results

    def get_sp500_iv_opportunities(self) -> dict:
        all_results: list[IVRankResult] = []
        total = len(SP500_TOP_50)
        # Process tickers in batches of 5 with a 1s delay between batches.
        for idx, ticker in enumerate(SP500_TOP_50, start=1):
            try:
                logger.info("סרוק %d/%d: %s", idx, total, ticker)
                result = self.calculate_iv_rank(ticker)
            except Exception:  # noqa: BLE001
                logger.exception("IV rank failed for %s", ticker)
                continue
            if result is None:
                logger.info("דילוג על %s (אין נתונים)", ticker)
                continue
            all_results.append(result)
            if idx % 5 == 0:
                time.sleep(1.0)

        sell_opportunities = sorted(
            [r for r in all_results if r.iv_rank >= 50],
            key=lambda r: r.iv_rank,
            reverse=True,
        )
        golden_opportunities = [r for r in sell_opportunities if r.iv_rank >= 80]
        buy_opportunities = sorted(
            [r for r in all_results if r.iv_rank < 25 and r.iv_rank > 0],
            key=lambda r: r.iv_rank,
        )

        market_summary = (
            f"נסרקו {len(all_results)} מניות. "
            f"{len(golden_opportunities)} הזדמנויות זהב למכירה, "
            f"{len(sell_opportunities)} הזדמנויות מכירה כלליות, "
            f"{len(buy_opportunities)} הזדמנויות קנייה."
        )

        return {
            "sell_opportunities": sell_opportunities,
            "golden_opportunities": golden_opportunities,
            "buy_opportunities": buy_opportunities,
            "scan_time": datetime.utcnow(),
            "market_summary": market_summary,
        }

    @staticmethod
    def _get_spot(tk: yf.Ticker) -> float:
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
            pass
        return 0.0


__all__ = ["IVRankCalculator", "IVRankResult", "SP500_TOP_50"]
