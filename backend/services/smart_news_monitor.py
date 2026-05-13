"""Smart news monitor: deduplicated, classified, impact-tracked, self-cleaning."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from db.connection import get_db
from memory.long_term import LongTermMemory
from services.telegram_service import TelegramService, TelegramServiceError
from tools.finnhub_tool import FinnhubTool
from tools.perplexity_tool import PerplexityTool, PerplexityToolError

logger = logging.getLogger(__name__)


CATEGORY_EMOJI = {
    "earnings": "📊",
    "analyst": "👥",
    "merger": "🤝",
    "geopolitical": "🌍",
    "fed": "🏛️",
    "economic": "📈",
    "company_news": "🏢",
    "macro": "💼",
}

SENTIMENT_EMOJI = {
    "bullish": "🟢 חיובי",
    "bearish": "🔴 שלילי",
    "neutral": "🟡 ניטרלי",
}

IMPORTANCE_EMOJI = {
    "high": "🚨 דחוף",
    "medium": "⚠️ חשוב",
    "low": "ℹ️ מידע",
}

HIGH_KEYWORDS = [
    "fed", "interest rate", "recession", "war", "sanctions",
    "earnings beat", "earnings miss", "acquired", "merger",
    "lawsuit", "investigation", "crash", "surge", "guidance",
    "downgrade", "upgrade", "ceo resigns", "bankruptcy",
]
MEDIUM_KEYWORDS = [
    "analyst", "price target", "report", "deal", "partnership",
    "launch", "approval", "fda",
]
BULLISH_WORDS = [
    "beat", "surge", "rally", "upgrade", "buy", "strong",
    "growth", "positive", "approval", "breakthrough",
]
BEARISH_WORDS = [
    "miss", "drop", "crash", "downgrade", "sell", "weak",
    "decline", "loss", "lawsuit", "investigation", "warning",
]


class SmartNewsMonitor:
    """Intelligent news monitoring with deduplication and impact tracking."""

    WATCHLIST = [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META",
        "AMZN", "TSLA", "AMD", "NFLX", "JPM",
        "SPY", "QQQ", "IWM",
    ]

    EXTRA_KNOWN_TICKERS = {
        "DIS", "BA", "GS", "BAC", "WMT", "COST", "HD", "V", "MA", "INTC",
    }

    def __init__(self) -> None:
        self.db = get_db()
        self.finnhub = FinnhubTool()
        try:
            self.perplexity: Optional[PerplexityTool] = PerplexityTool()
        except PerplexityToolError as exc:
            logger.warning("Perplexity disabled: %s", exc)
            self.perplexity = None
        try:
            self.telegram: Optional[TelegramService] = TelegramService()
        except TelegramServiceError as exc:
            logger.warning("Telegram disabled: %s", exc)
            self.telegram = None

    # ───────────────────────── helpers ─────────────────────────

    def _generate_hash(self, headline: str, source: str) -> str:
        normalized = (headline.lower()[:100] + source)
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()

    async def _already_sent(self, news_hash: str) -> bool:
        existing = await self.db.news_items.find_one({"news_hash": news_hash})
        return existing is not None and existing.get("sent_to_user_at") is not None

    async def _quote(self, ticker: str) -> Optional[float]:
        quote = await asyncio.to_thread(self.finnhub.get_stock_quote, ticker)
        price = (quote or {}).get("price")
        try:
            return float(price) if price is not None else None
        except (TypeError, ValueError):
            return None

    # ───────────────────────── classifier helpers ─────────────────────────

    def _classify_importance(self, headline: str, summary: str) -> str:
        text = (headline + " " + (summary or "")).lower()
        for kw in HIGH_KEYWORDS:
            if kw in text:
                return "high"
        for kw in MEDIUM_KEYWORDS:
            if kw in text:
                return "medium"
        return "low"

    def _classify_category(self, text: str) -> str:
        text = (text or "").lower()
        if "earnings" in text or "eps" in text:
            return "earnings"
        if "analyst" in text or "rating" in text or "target" in text:
            return "analyst"
        if "merger" in text or "acquir" in text:
            return "merger"
        if "war" in text or "sanctions" in text:
            return "geopolitical"
        if "fed" in text or "powell" in text:
            return "fed"
        if "gdp" in text or "inflation" in text or "cpi" in text:
            return "economic"
        return "company_news"

    def _classify_sentiment(self, text: str) -> str:
        text = (text or "").lower()
        bull = sum(1 for w in BULLISH_WORDS if w in text)
        bear = sum(1 for w in BEARISH_WORDS if w in text)
        if bull > bear:
            return "bullish"
        if bear > bull:
            return "bearish"
        return "neutral"

    def _extract_tickers(self, text: str) -> list[str]:
        pattern = r"\$?([A-Z]{2,5})\b"
        matches = re.findall(pattern, text or "")
        known = set(self.WATCHLIST) | self.EXTRA_KNOWN_TICKERS
        return [m for m in matches if m in known]

    def _parse_perplexity_response(self, answer: str, _sources: list) -> list[dict]:
        items: list[dict] = []
        lines = [l.strip() for l in (answer or "").split("\n") if l.strip() and len(l.strip()) > 30]
        for line in lines[:5]:
            cleaned = line.lstrip("•-*123456789. ")
            items.append(
                {
                    "headline": cleaned[:200],
                    "summary": cleaned,
                    "category": self._classify_category(cleaned),
                    "importance": self._classify_importance(cleaned, ""),
                    "sentiment": self._classify_sentiment(cleaned),
                    "tickers": self._extract_tickers(cleaned),
                }
            )
        return items

    # ───────────────────────── save + telegram ─────────────────────────

    def _format_for_telegram(self, news: dict) -> str:
        emoji = CATEGORY_EMOJI.get(news.get("category", ""), "📰")
        tickers_str = " ".join(f"${t}" for t in news.get("tickers", []) or [])
        return (
            f"{IMPORTANCE_EMOJI.get(news['importance'], 'ℹ️')}\n\n"
            f"{emoji} {news['headline']}\n\n"
            f"{(news.get('summary') or '')[:300]}\n\n"
            f"💹 מניות מושפעות: {tickers_str or '—'}\n"
            f"📊 סנטימנט: {SENTIMENT_EMOJI.get(news.get('sentiment') or 'neutral', '🟡 ניטרלי')}\n"
            f"🔗 מקור: {news.get('source', '—')}\n\n"
            f"#{news.get('category', 'news')}"
        )

    async def _save_and_send(self, news_data: dict) -> None:
        news_data["expires_at"] = datetime.utcnow() + timedelta(days=7)
        news_data["sent_to_user_at"] = datetime.utcnow()
        news_data.setdefault("created_at", datetime.utcnow())

        prices: dict[str, float] = {}
        for ticker in news_data.get("tickers", []) or []:
            price = await self._quote(ticker)
            if price is not None:
                prices[ticker] = price
        news_data["price_at_news"] = prices
        news_data.setdefault("price_after_1d", {})
        news_data.setdefault("price_after_3d", {})
        news_data.setdefault("price_after_7d", {})

        try:
            await self.db.news_items.insert_one(news_data)
        except Exception:  # noqa: BLE001
            logger.exception("news_items insert failed")
            return

        if self.telegram is None:
            return
        try:
            await self.telegram.send_alert(
                title=news_data["headline"][:80],
                body=self._format_for_telegram(news_data),
                urgency=news_data.get("importance", "info"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram send failed: %s", exc)

    # ───────────────────────── checks ─────────────────────────

    async def check_company_news(self) -> int:
        sent = 0
        for ticker in self.WATCHLIST:
            try:
                news_list = await asyncio.to_thread(self.finnhub.get_company_news, ticker, 1)
            except Exception:  # noqa: BLE001
                logger.exception("check_company_news fetch failed for %s", ticker)
                continue

            for news in (news_list or [])[:3]:
                headline = news.get("headline") or ""
                if not headline:
                    continue
                news_hash = self._generate_hash(headline, "finnhub")
                if await self._already_sent(news_hash):
                    continue

                summary = news.get("summary") or ""
                importance = self._classify_importance(headline, summary)
                if importance == "low":
                    continue

                news_data = {
                    "news_hash": news_hash,
                    "headline": headline,
                    "summary": summary,
                    "source": "finnhub",
                    "url": news.get("url"),
                    "category": self._classify_category(headline),
                    "importance": importance,
                    "sentiment": self._classify_sentiment(headline + " " + summary),
                    "tickers": [ticker],
                    "published_at": datetime.utcnow(),
                }
                await self._save_and_send(news_data)
                sent += 1
        logger.info("check_company_news: %d new items sent", sent)
        return sent

    async def check_macro_news(self) -> int:
        if not self.perplexity:
            logger.warning("Perplexity unavailable – skipping macro news")
            return 0
        try:
            result = await asyncio.to_thread(
                self.perplexity.search,
                "Important breaking news in the last 2 hours: "
                "Fed announcements, geopolitical events, wars, sanctions, "
                "major economic data, mergers and acquisitions that affect "
                "US stock market. Include source URLs.",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Perplexity macro search failed")
            return 0

        answer = result.get("answer", "") if isinstance(result, dict) else ""
        sources = result.get("sources", []) if isinstance(result, dict) else []
        items = self._parse_perplexity_response(answer, sources)

        sent = 0
        for item in items:
            news_hash = self._generate_hash(item["headline"], "perplexity")
            if await self._already_sent(news_hash):
                continue
            item.update(
                {
                    "news_hash": news_hash,
                    "source": "perplexity",
                    "published_at": datetime.utcnow(),
                    "url": sources[0] if sources else None,
                }
            )
            await self._save_and_send(item)
            sent += 1
        logger.info("check_macro_news: %d new items sent", sent)
        return sent

    async def check_earnings_today(self) -> int:
        try:
            earnings = await asyncio.to_thread(self.finnhub.get_earnings_calendar, 2)
        except Exception:  # noqa: BLE001
            logger.exception("earnings_calendar fetch failed")
            return 0

        sent = 0
        for e in earnings or []:
            ticker = e.get("ticker")
            date = e.get("date")
            if not ticker or not date:
                continue
            news_hash = self._generate_hash(f"earnings-{ticker}-{date}", "earnings")
            if await self._already_sent(news_hash):
                continue
            news_data = {
                "news_hash": news_hash,
                "headline": f"📊 Earnings {ticker} - {date}",
                "summary": f"EPS Estimate: {e.get('eps_estimate')}",
                "source": "earnings_calendar",
                "url": None,
                "category": "earnings",
                "importance": "high",
                "sentiment": "neutral",
                "tickers": [ticker],
                "published_at": datetime.utcnow(),
            }
            await self._save_and_send(news_data)
            sent += 1
        logger.info("check_earnings_today: %d new items sent", sent)
        return sent

    # ───────────────────────── impact + learning ─────────────────────────

    @staticmethod
    def _calculate_impact(
        before: dict, after: dict, _predicted: Optional[str] = None
    ) -> float:
        if not before or not after:
            return 0.0
        changes: list[float] = []
        for ticker, old_price in before.items():
            try:
                old = float(old_price)
                new = float(after.get(ticker) or 0.0)
            except (TypeError, ValueError):
                continue
            if old > 0 and new > 0:
                changes.append(((new - old) / old) * 100.0)
        if not changes:
            return 0.0
        avg = sum(changes) / len(changes)
        return round(avg * 10.0, 2)

    async def update_news_impact(self) -> int:
        cutoff_7d = datetime.utcnow() - timedelta(days=7)
        cursor = self.db.news_items.find(
            {"sent_to_user_at": {"$gte": cutoff_7d}, "price_at_news": {"$ne": {}}}
        )
        updated = 0
        async for news in cursor:
            sent_at = news.get("sent_to_user_at")
            if not isinstance(sent_at, datetime):
                continue
            days_passed = (datetime.utcnow() - sent_at).days
            if days_passed < 1:
                continue

            update_field: Optional[str] = None
            if days_passed >= 7 and not news.get("price_after_7d"):
                update_field = "price_after_7d"
            elif days_passed >= 3 and not news.get("price_after_3d"):
                update_field = "price_after_3d"
            elif days_passed >= 1 and not news.get("price_after_1d"):
                update_field = "price_after_1d"
            if not update_field:
                continue

            current_prices: dict[str, float] = {}
            for ticker in news.get("tickers") or []:
                price = await self._quote(ticker)
                if price is not None:
                    current_prices[ticker] = price
            if not current_prices:
                continue

            impact = self._calculate_impact(
                news.get("price_at_news") or {},
                current_prices,
                news.get("sentiment"),
            )
            await self.db.news_items.update_one(
                {"_id": news["_id"]},
                {"$set": {update_field: current_prices, "impact_score": impact}},
            )
            updated += 1
        logger.info("update_news_impact: %d items updated", updated)
        return updated

    async def learn_from_news(self) -> int:
        ltm = LongTermMemory()
        cursor = self.db.news_items.find(
            {"impact_score": {"$ne": None}, "price_after_7d": {"$ne": {}}}
        )
        saved = 0
        async for news in cursor:
            tickers = ",".join(news.get("tickers") or [])
            pattern = (
                f"כש{news.get('category', '?')} {news.get('sentiment', '?')} "
                f"מתפרסם על {tickers or '—'}, "
                f"המחיר נע {news.get('impact_score')}% בממוצע תוך 7 ימים.\n"
                f"כותרת: {(news.get('headline') or '')[:100]}"
            )
            try:
                await asyncio.to_thread(
                    ltm.save_market_pattern,
                    pattern,
                    {
                        "category": news.get("category"),
                        "sentiment": news.get("sentiment"),
                        "tickers": news.get("tickers") or [],
                        "impact": news.get("impact_score"),
                    },
                )
                saved += 1
            except Exception:  # noqa: BLE001
                logger.exception("save_market_pattern failed for %s", news.get("_id"))
        logger.info("learn_from_news: %d patterns saved", saved)
        return saved

    async def cleanup_old_news(self) -> int:
        try:
            result = await self.db.news_items.delete_many(
                {"expires_at": {"$lt": datetime.utcnow()}}
            )
        except Exception:  # noqa: BLE001
            logger.exception("cleanup_old_news failed")
            return 0
        logger.info("🧹 Deleted %s old news items", result.deleted_count)
        return result.deleted_count


__all__ = ["SmartNewsMonitor"]
