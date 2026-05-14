"""Smart news monitor: deduplicated, classified, impact-tracked, self-cleaning."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from anthropic import Anthropic

from db.connection import get_db
from memory.long_term import LongTermMemory
from services.telegram_service import TelegramService, TelegramServiceError
from tools.finnhub_tool import FinnhubTool
from tools.perplexity_tool import PerplexityTool, PerplexityToolError

logger = logging.getLogger(__name__)


IMPORTANCE_LABELS = {
    "high": "🔴 חשוב מאוד",
    "medium": "🟡 חשוב",
    "low": "🟢 מידע",
}

CATEGORY_LABELS = {
    "earnings": "📊 דוחות כספיים",
    "analyst": "👥 המלצת אנליסט",
    "merger": "🤝 מיזוג/רכישה",
    "geopolitical": "🌍 גיאופוליטי",
    "fed": "🏛️ פד/ריבית",
    "economic": "📈 נתון כלכלי",
    "company_news": "🏢 חדשות חברה",
    "macro": "💼 מאקרו",
}

SENTIMENT_LABELS = {
    "bullish": "חיובי לשוק 🟢",
    "bearish": "שלילי לשוק 🔴",
    "neutral": "ניטרלי 🟡",
}

MARKET_KEYWORDS = [
    # Companies & stocks
    "stock", "shares", "earnings", "revenue", "profit", "loss",
    "guidance", "outlook", "dividend", "buyback", "ipo", "merger",
    "acquisition", "deal", "partnership",
    # Fed & macro
    "fed", "federal reserve", "interest rate", "inflation", "cpi",
    "gdp", "unemployment", "powell", "recession", "economy",
    "treasury", "yield", "bond",
    # Markets
    "market", "nasdaq", "s&p", "dow jones", "wall street", "trading",
    "rally", "crash", "bull", "bear", "volatility", "vix", "options",
    "futures",
    # Geopolitical that moves markets
    "sanctions", "tariff", "trade war", "oil", "opec", "energy",
    "supply chain",
    # Analyst actions
    "analyst", "upgrade", "downgrade", "price target", "rating",
    "buy", "sell", "overweight", "underweight",
    # Corporate actions
    "ceo", "layoff", "bankruptcy", "lawsuit", "fda", "approval",
    "regulation",
]

IRRELEVANT_KEYWORDS = [
    "sports", "celebrity", "entertainment", "movie", "music",
    "fashion", "food", "weather", "crime", "accident",
]

_MARKDOWN_HEADING_RE = re.compile(r"#+\s*")

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

        api_key = os.getenv("ANTHROPIC_API_KEY")
        self._anthropic: Optional[Anthropic] = Anthropic(api_key=api_key) if api_key else None
        if self._anthropic is None:
            logger.warning("Anthropic disabled (no ANTHROPIC_API_KEY) – translations skipped")

    # ───────────────────────── translation + filtering ─────────────────────────

    async def _translate_to_hebrew(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if self._anthropic is None:
            return text
        try:
            response = await asyncio.to_thread(
                self._anthropic.messages.create,
                model="claude-haiku-4-5",
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "תרגם את הטקסט הבא לעברית בצורה טבעית וברורה.\n"
                            "אם זה כבר עברית – השאר כמו שזה.\n"
                            "רק את התרגום, בלי הסברים:\n\n"
                            f"{text}"
                        ),
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Translation failed: %s", exc)
            return text

        chunks: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                chunks.append(getattr(block, "text", ""))
        translated = "".join(chunks).strip()
        return translated or text

    @staticmethod
    def _clean_markdown(text: str) -> str:
        if not text:
            return ""
        return _MARKDOWN_HEADING_RE.sub("", text).strip()

    def _is_market_relevant(self, headline: str, summary: str) -> bool:
        """Return True only if the news can move stocks, indices, or options."""
        text = ((headline or "") + " " + (summary or "")).lower()
        has_market_keyword = any(kw in text for kw in MARKET_KEYWORDS)
        is_irrelevant = any(kw in text for kw in IRRELEVANT_KEYWORDS)
        return has_market_keyword and not is_irrelevant

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

    async def _format_for_telegram(self, news: dict) -> str:
        headline_raw = self._clean_markdown(news.get("headline") or "")
        summary_raw = self._clean_markdown((news.get("summary") or "")[:200])

        headline_heb = await self._translate_to_hebrew(headline_raw)
        summary_heb = await self._translate_to_hebrew(summary_raw) if summary_raw else ""

        tickers_str = " ".join(f"${t}" for t in (news.get("tickers") or []))
        importance = IMPORTANCE_LABELS.get(
            news.get("importance", "low"), IMPORTANCE_LABELS["low"]
        )
        category = CATEGORY_LABELS.get(
            news.get("category", "macro"), CATEGORY_LABELS["macro"]
        )
        sentiment = SENTIMENT_LABELS.get(
            news.get("sentiment") or "neutral", SENTIMENT_LABELS["neutral"]
        )

        return (
            f"{importance}\n"
            f"{category}\n\n"
            f"📰 {headline_heb}\n\n"
            f"{summary_heb}\n\n"
            "━━━━━━━━━━━━━━\n"
            f"💹 מניות: {tickers_str or 'שוק כללי'}\n"
            f"📊 השפעה: {sentiment}"
        )

    async def _save_and_send(self, news_data: dict) -> None:
        if not self._is_market_relevant(
            news_data.get("headline", ""), news_data.get("summary", "") or ""
        ):
            logger.info(
                "Skipping non-market news: %s",
                (news_data.get("headline") or "")[:50],
            )
            return

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
            body = await self._format_for_telegram(news_data)
            title_raw = self._clean_markdown(news_data.get("headline") or "")[:80]
            title_heb = await self._translate_to_hebrew(title_raw) if title_raw else "חדשות שוק"
            await self.telegram.send_alert(
                title=title_heb[:80],
                body=body,
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
