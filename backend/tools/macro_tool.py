"""Macro-economic snapshot: VIX, rates, FX, commodities, economic calendar."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import yfinance as yf

from tools.fear_greed_tool import FearGreedTool
from tools.finnhub_tool import FinnhubTool

logger = logging.getLogger(__name__)


def _spot(symbol: str) -> Optional[float]:
    try:
        tk = yf.Ticker(symbol)
        fast = getattr(tk, "fast_info", None)
        if fast is not None:
            price = getattr(fast, "last_price", None)
            if price:
                return float(price)
        hist = tk.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:  # noqa: BLE001
        logger.exception("Spot fetch failed for %s", symbol)
    return None


class MacroTool:
    def get_market_indicators(self) -> dict[str, Any]:
        vix_price = _spot("^VIX")
        indicators: dict[str, Any] = {}
        indicators["vix"] = {
            "current": vix_price,
            "signal": (
                "גבוה" if (vix_price or 0) > 20 else "נמוך"
            ),
        }
        indicators["treasury_10y"] = _spot("^TNX")
        indicators["dxy"] = _spot("DX-Y.NYB")
        indicators["gold"] = _spot("GC=F")
        indicators["oil"] = _spot("CL=F")
        indicators["spx"] = _spot("^GSPC")
        return indicators

    def get_economic_calendar(self) -> list[dict[str, Any]]:
        try:
            client = FinnhubTool().client
            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            payload = client.calendar_economic(_from=from_date, to=to_date)
        except Exception:  # noqa: BLE001
            logger.exception("economic_calendar fetch failed")
            return []

        events = (payload or {}).get("economicCalendar") or []
        results: list[dict[str, Any]] = []
        for ev in events:
            impact_raw = (ev.get("impact") or "").lower()
            if impact_raw not in {"high", "medium"}:
                continue
            results.append(
                {
                    "event": ev.get("event") or ev.get("eventName") or "",
                    "date": f"{ev.get('time', '')}",
                    "impact": "גבוה" if impact_raw == "high" else "בינוני",
                    "actual": ev.get("actual"),
                    "estimate": ev.get("estimate"),
                    "previous": ev.get("prev"),
                    "country": ev.get("country"),
                }
            )
        results.sort(key=lambda x: x.get("date") or "")
        return results[:15]

    def get_market_summary(self) -> str:
        indicators = self.get_market_indicators()
        fg = FearGreedTool().get_fear_greed_index()
        events = self.get_economic_calendar()

        def _fmt(value: Any, prefix: str = "", suffix: str = "") -> str:
            if value is None:
                return "—"
            try:
                return f"{prefix}{float(value):,.2f}{suffix}"
            except (TypeError, ValueError):
                return f"{prefix}{value}{suffix}"

        vix = indicators["vix"]
        events_line = (
            f"{len(events)} אירועים (גבוה/בינוני)" if events else "אין אירועים בולטים"
        )
        score_text = (
            f"{fg.get('score', '—')} – {fg.get('hebrew_rating', '—')}"
            if fg
            else "—"
        )
        implication = fg.get("strategy_implication", "—") if fg else "—"

        return (
            "📊 מצב מאקרו עכשיו:\n\n"
            f"😱 Fear & Greed: {score_text}\n"
            f"📈 VIX: {_fmt(vix.get('current'))} – {vix.get('signal', '—')}\n"
            f"💵 דולר (DXY): {_fmt(indicators.get('dxy'))}\n"
            f"🥇 זהב: {_fmt(indicators.get('gold'), prefix='$')}\n"
            f"🛢️ נפט: {_fmt(indicators.get('oil'), prefix='$')}\n"
            f"📅 אירועים השבוע: {events_line}\n\n"
            "💡 משמעות לאופציות:\n"
            f"{implication}"
        )


__all__ = ["MacroTool"]
