"""Finnhub API wrapper – quotes, news, earnings, analyst recommendations."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import finnhub

logger = logging.getLogger(__name__)


class FinnhubTool:
    def __init__(self, api_key: Optional[str] = None) -> None:
        api_key = api_key or os.getenv("FINNHUB_API_KEY")
        self.client = finnhub.Client(api_key=api_key)

    # ───────────────────────── primitives ─────────────────────────

    def get_stock_quote(self, ticker: str) -> dict:
        """מחיר נוכחי של מניה"""
        try:
            quote = self.client.quote(ticker)
            return {
                "ticker": ticker,
                "price": quote.get("c"),
                "change": quote.get("d"),
                "change_pct": quote.get("dp"),
                "high": quote.get("h"),
                "low": quote.get("l"),
                "open": quote.get("o"),
                "prev_close": quote.get("pc"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Quote error %s: %s", ticker, exc)
            return {}

    def get_company_news(self, ticker: str, days: int = 3) -> list:
        """חדשות אחרונות למניה"""
        try:
            to_date = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            news = self.client.company_news(ticker, _from=from_date, to=to_date)
            return [
                {
                    "headline": n.get("headline", ""),
                    "summary": n.get("summary", ""),
                    "source": n.get("source", ""),
                    "url": n.get("url", ""),
                    "datetime": datetime.fromtimestamp(n["datetime"]).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    if n.get("datetime")
                    else "",
                }
                for n in (news or [])[:5]
            ]
        except Exception as exc:  # noqa: BLE001
            logger.error("News error %s: %s", ticker, exc)
            return []

    def get_earnings_calendar(self, days_ahead: int = 14) -> list:
        """Earnings Calendar – מניות עם דוחות קרובים"""
        try:
            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = (datetime.now() + timedelta(days=days_ahead)).strftime(
                "%Y-%m-%d"
            )
            earnings = self.client.earnings_calendar(
                _from=from_date, to=to_date, symbol="", international=False
            )
            results = []
            for e in (earnings or {}).get("earningsCalendar", [])[:20]:
                results.append(
                    {
                        "ticker": e.get("symbol"),
                        "date": e.get("date"),
                        "eps_estimate": e.get("epsEstimate"),
                        "revenue_estimate": e.get("revenueEstimate"),
                    }
                )
            return results
        except Exception as exc:  # noqa: BLE001
            logger.error("Earnings calendar error: %s", exc)
            return []

    def get_market_news(self, category: str = "general") -> list:
        """חדשות שוק כלליות"""
        try:
            news = self.client.general_news(category, min_id=0)
            return [
                {
                    "headline": n.get("headline", ""),
                    "summary": (n.get("summary") or "")[:200],
                    "source": n.get("source", ""),
                    "datetime": datetime.fromtimestamp(n["datetime"]).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    if n.get("datetime")
                    else "",
                }
                for n in (news or [])[:8]
            ]
        except Exception as exc:  # noqa: BLE001
            logger.error("Market news error: %s", exc)
            return []

    def get_recommendation_trends(self, ticker: str) -> dict:
        """המלצות אנליסטים"""
        try:
            trends = self.client.recommendation_trends(ticker)
            if not trends:
                return {}
            latest = trends[0]
            total = sum(
                [
                    latest.get("strongBuy", 0),
                    latest.get("buy", 0),
                    latest.get("hold", 0),
                    latest.get("sell", 0),
                    latest.get("strongSell", 0),
                ]
            )
            return {
                "ticker": ticker,
                "period": latest.get("period"),
                "strong_buy": latest.get("strongBuy", 0),
                "buy": latest.get("buy", 0),
                "hold": latest.get("hold", 0),
                "sell": latest.get("sell", 0),
                "strong_sell": latest.get("strongSell", 0),
                "total_analysts": total,
                "consensus": self._get_consensus(latest),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Recommendation error %s: %s", ticker, exc)
            return {}

    @staticmethod
    def _get_consensus(trend: dict) -> str:
        buy = trend.get("strongBuy", 0) + trend.get("buy", 0)
        sell = trend.get("strongSell", 0) + trend.get("sell", 0)
        hold = trend.get("hold", 0)
        if buy > sell and buy > hold:
            return "קנייה 🟢"
        if sell > buy and sell > hold:
            return "מכירה 🔴"
        return "המתנה 🟡"

    def check_earnings_risk(self, ticker: str) -> dict:
        """בדוק אם יש earnings בקרוב – סיכון לפוזיציה"""
        try:
            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = (datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d")
            earnings = self.client.earnings_calendar(
                _from=from_date,
                to=to_date,
                symbol=ticker,
                international=False,
            )
            calendar = (earnings or {}).get("earningsCalendar", [])
            if not calendar:
                return {
                    "has_earnings": False,
                    "days_until": None,
                    "risk_level": "נמוך ✅",
                    "message": "אין דוחות ב-21 הימים הקרובים",
                }
            next_earnings = calendar[0]
            earnings_date = datetime.strptime(next_earnings["date"], "%Y-%m-%d")
            days_until = (earnings_date - datetime.now()).days

            if days_until <= 7:
                risk = "גבוה מאוד 🔴"
                msg = f"⚠️ Earnings בעוד {days_until} ימים!"
            elif days_until <= 14:
                risk = "גבוה 🟠"
                msg = f"⚠️ Earnings בעוד {days_until} ימים"
            else:
                risk = "בינוני 🟡"
                msg = f"Earnings בעוד {days_until} ימים"

            return {
                "has_earnings": True,
                "date": next_earnings["date"],
                "days_until": days_until,
                "risk_level": risk,
                "message": msg,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Earnings risk error %s: %s", ticker, exc)
            return {"has_earnings": False, "risk_level": "לא ידוע"}

    def get_full_ticker_analysis(self, ticker: str) -> str:
        """ניתוח מלא של מניה – משלב כל המקורות"""
        quote = self.get_stock_quote(ticker)
        news = self.get_company_news(ticker)
        recommendations = self.get_recommendation_trends(ticker)
        earnings_risk = self.check_earnings_risk(ticker)

        news_text = (
            "\n".join([f"• {n['headline']} ({n['source']})" for n in news[:3]])
            if news
            else "אין חדשות אחרונות"
        )

        change_pct = quote.get("change_pct") or 0.0

        buy_count = recommendations.get("strong_buy", 0) + recommendations.get(
            "buy", 0
        )
        sell_count = recommendations.get("strong_sell", 0) + recommendations.get(
            "sell", 0
        )

        return (
            f"📊 ניתוח {ticker} – Finnhub\n\n"
            f"💰 מחיר: ${quote.get('price', 'N/A')}\n"
            f"📈 שינוי: {change_pct:.2f}%\n"
            f"📊 טווח יומי: ${quote.get('low', 'N/A')} – ${quote.get('high', 'N/A')}\n\n"
            "👥 המלצות אנליסטים:\n"
            f"🟢 קנייה: {buy_count}\n"
            f"🟡 המתנה: {recommendations.get('hold', 0)}\n"
            f"🔴 מכירה: {sell_count}\n"
            f"קונצנזוס: {recommendations.get('consensus', 'N/A')}\n\n"
            f"📅 Earnings Risk: {earnings_risk.get('risk_level', 'N/A')}\n"
            f"{earnings_risk.get('message', '')}\n\n"
            "📰 חדשות אחרונות:\n"
            f"{news_text}"
        )


__all__ = ["FinnhubTool"]
