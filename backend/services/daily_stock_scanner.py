"""Daily comprehensive stock scanner with prediction tracking + pattern learning."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from anthropic import Anthropic

from analytics.iv_rank_calculator import IVRankCalculator
from db.connection import get_db
from memory.long_term import LongTermMemory
from scrapers.finviz_scraper import FinVizScraper, TickerDetail, TickerSummary
from services.telegram_service import TelegramService, TelegramServiceError
from tools.finnhub_tool import FinnhubTool
from tools.perplexity_tool import PerplexityTool, PerplexityToolError

logger = logging.getLogger(__name__)


class DailyStockScanner:
    """Daily comprehensive stock analysis system. Learns from past predictions."""

    SCREENER_URL = (
        "https://finviz.com/screener.ashx"
        "?v=111&f=cap_midover,sh_avgvol_o500,sh_opt_option,"
        "sh_relvol_o1,ta_highlow52w_a70h&ft=4&o=-volume"
    )

    def __init__(self) -> None:
        self.db = get_db()
        self.finnhub = FinnhubTool()
        try:
            self.perplexity: Optional[PerplexityTool] = PerplexityTool()
        except PerplexityToolError as exc:
            logger.warning("Perplexity disabled in scanner: %s", exc)
            self.perplexity = None
        self.iv_calc = IVRankCalculator()
        api_key = os.getenv("ANTHROPIC_API_KEY")
        self.claude: Optional[Anthropic] = Anthropic(api_key=api_key) if api_key else None
        try:
            self.telegram: Optional[TelegramService] = TelegramService()
        except TelegramServiceError as exc:
            logger.warning("Telegram disabled in scanner: %s", exc)
            self.telegram = None

    # ───────────────────────── public workflow ─────────────────────────

    async def run_daily_scan(self) -> list[dict[str, Any]]:
        logger.info("🚀 מתחיל סריקה יומית של מניות")

        try:
            tickers = await asyncio.to_thread(self._scrape_screener)
        except Exception:  # noqa: BLE001
            logger.exception("FinViz screener scrape failed")
            return []

        logger.info("📊 נמצאו %d מניות לניתוח", len(tickers))

        analyses: list[dict[str, Any]] = []
        for idx, summary in enumerate(tickers, start=1):
            try:
                logger.info("🔍 [%d/%d] מנתח %s", idx, len(tickers), summary.ticker)
                analysis = await self._analyze_stock(summary)
                if analysis:
                    analyses.append(analysis)
            except Exception:  # noqa: BLE001
                logger.exception("Error analyzing %s", summary.ticker)
            await asyncio.sleep(2)

        analyses.sort(key=lambda x: x.get("quality_score", 0), reverse=True)

        if analyses:
            try:
                await self.db.stock_analyses.insert_many(analyses)
                logger.info("💾 נשמרו %d ניתוחים", len(analyses))
            except Exception:  # noqa: BLE001
                logger.exception("stock_analyses insert_many failed")

        await self._send_daily_recommendations(analyses[:5])
        return analyses

    def _scrape_screener(self) -> list[TickerSummary]:
        with FinVizScraper(headless=True) as scraper:
            return scraper._scrape_screener_url(self.SCREENER_URL, max_pages=10)

    # ───────────────────────── per-stock analysis ─────────────────────────

    async def _analyze_stock(self, summary: TickerSummary) -> Optional[dict[str, Any]]:
        ticker = summary.ticker

        try:
            detail = await asyncio.to_thread(self._scrape_detail, ticker)
        except Exception:  # noqa: BLE001
            logger.exception("scrape_ticker failed for %s", ticker)
            return None

        quote, news, recs, earnings, iv_result = await asyncio.gather(
            asyncio.to_thread(self.finnhub.get_stock_quote, ticker),
            asyncio.to_thread(self.finnhub.get_company_news, ticker, 2),
            asyncio.to_thread(self.finnhub.get_recommendation_trends, ticker),
            asyncio.to_thread(self.finnhub.check_earnings_risk, ticker),
            asyncio.to_thread(self.iv_calc.calculate_iv_rank, ticker),
            return_exceptions=True,
        )
        quote = quote if isinstance(quote, dict) else {}
        news = news if isinstance(news, list) else []
        recs = recs if isinstance(recs, dict) else {}
        earnings = earnings if isinstance(earnings, dict) else {}
        iv_result = iv_result if iv_result is not None and not isinstance(iv_result, Exception) else None

        news_context = await self._get_news_context(ticker, detail)
        trend, trend_strength = self._classify_trend(detail)
        catalysts = self._identify_catalysts(news, recs, earnings)

        rel_volume = self._parse_float(detail.rel_volume) or 0.0
        iv_rank = iv_result.iv_rank if iv_result else 50.0

        quality_score = self._calculate_quality_score(
            trend=trend,
            iv_rank=iv_rank,
            recommendations=recs,
            earnings_risk=earnings,
            volume=rel_volume,
            catalysts=catalysts,
        )

        recommendation, reason = self._get_recommendation(
            quality_score, trend, catalysts
        )
        strategy = self._suggest_strategy(trend, iv_result, earnings)

        price = self._parse_float(detail.price) or float(quote.get("price") or 0)
        change_pct = self._parse_float(detail.change_pct) or float(
            quote.get("change_pct") or 0
        )
        high_52w = self._parse_float(detail.high_52w)
        distance_from_high = None
        if high_52w and price:
            distance_from_high = round(((price - high_52w) / high_52w) * 100, 2)

        return {
            "ticker": ticker,
            "analysis_date": datetime.utcnow(),
            "found_on_screener": True,
            "screener_url": self.SCREENER_URL,
            "price": price,
            "change_pct": change_pct,
            "volume": int(self._parse_float(detail.avg_volume) or 0),
            "rel_volume": rel_volume,
            "rsi": self._parse_float(detail.rsi),
            "sma_20": self._parse_float(detail.sma_20),
            "sma_50": self._parse_float(detail.sma_50),
            "sma_200": self._parse_float(detail.sma_200),
            "high_52w": high_52w,
            "distance_from_52w_high": distance_from_high,
            "trend": trend,
            "trend_strength": trend_strength,
            "market_cap": detail.market_cap or summary.market_cap or None,
            "pe": self._parse_float(detail.pe),
            "sector": summary.sector or None,
            "iv_rank": iv_result.iv_rank if iv_result else None,
            "iv_percentile": iv_result.iv_percentile if iv_result else None,
            "has_options": True,
            "recent_news": (news or [])[:5],
            "catalysts": catalysts,
            "news_impact_analysis": news_context,
            "quality_score": quality_score,
            "recommendation": recommendation,
            "recommendation_reason": reason,
            "suggested_strategy": strategy.get("name"),
            "suggested_strikes": (
                {"description": strategy.get("strikes")} if strategy.get("strikes") else None
            ),
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(days=30),
        }

    def _scrape_detail(self, ticker: str) -> TickerDetail:
        with FinVizScraper(headless=True) as scraper:
            return scraper.scrape_ticker(ticker)

    # ───────────────────────── helpers ─────────────────────────

    async def _get_news_context(self, ticker: str, detail: TickerDetail) -> str:
        change = self._parse_float(detail.change_pct) or 0.0
        if abs(change) < 1:
            return "תנועה רגילה - אין חדשות משמעותיות"
        if not self.perplexity:
            return "לא ניתן לקבל הקשר חדשות (Perplexity לא זמין)"

        direction = "עלתה" if change > 0 else "ירדה"
        query = (
            f"Why did {ticker} stock {direction} {abs(change):.1f}% today? "
            f"Find specific news, analyst actions, or events that caused the move."
        )

        try:
            result = await asyncio.to_thread(self.perplexity.search, query)
            answer = (result.get("answer", "") if isinstance(result, dict) else "")[:500]
        except Exception:  # noqa: BLE001
            logger.exception("perplexity context failed for %s", ticker)
            return "לא ניתן לקבל הקשר חדשות"

        if not answer:
            return "לא נמצאו חדשות"
        if self.claude is None:
            return answer

        try:
            response = await asyncio.to_thread(
                self.claude.messages.create,
                model="claude-haiku-4-5",
                max_tokens=300,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "תרגם לעברית ברורה ומסודרת. רק את התרגום:\n\n" + answer
                        ),
                    }
                ],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Hebrew translation failed for %s", ticker)
            return answer
        chunks = [
            getattr(block, "text", "")
            for block in getattr(response, "content", []) or []
            if getattr(block, "type", None) == "text"
        ]
        return ("".join(chunks).strip()) or answer

    def _classify_trend(self, detail: TickerDetail) -> tuple[str, float]:
        price = self._parse_float(detail.price) or 0.0
        sma_20 = self._parse_float(detail.sma_20) or 0.0
        sma_50 = self._parse_float(detail.sma_50) or 0.0
        sma_200 = self._parse_float(detail.sma_200) or 0.0
        high_52w = self._parse_float(detail.high_52w) or 0.0

        if price == 0:
            return "sideways", 50.0

        dist_from_high = ((price - high_52w) / high_52w * 100.0) if high_52w else 0.0

        if price > sma_20 > sma_50 > sma_200 and dist_from_high > -5:
            return "strong_uptrend", 90.0
        if price > sma_50 and dist_from_high > -10:
            return "uptrend", 70.0
        if abs(dist_from_high) < 5:
            return "sideways", 50.0
        if price < sma_50:
            return "downtrend", 30.0
        return "sideways", 50.0

    def _identify_catalysts(
        self,
        news: list[dict[str, Any]],
        recommendations: dict[str, Any],
        earnings: dict[str, Any],
    ) -> list[str]:
        catalysts: set[str] = set()
        for item in (news or [])[:3]:
            headline = (item.get("headline") or "").lower()
            if any(w in headline for w in ["beat", "exceeded", "surge"]):
                catalysts.add("earnings_beat")
            if any(w in headline for w in ["upgrade", "raised", "buy rating"]):
                catalysts.add("analyst_upgrade")
            if any(w in headline for w in ["fda", "approval"]):
                catalysts.add("fda_approval")
            if any(w in headline for w in ["deal", "merger", "acquisition"]):
                catalysts.add("ma_deal")
            if any(w in headline for w in ["contract", "partnership"]):
                catalysts.add("new_partnership")
        if recommendations and "קנייה" in (recommendations.get("consensus") or ""):
            catalysts.add("bullish_analyst")
        if earnings.get("has_earnings"):
            days = earnings.get("days_until")
            if isinstance(days, (int, float)) and days <= 14:
                catalysts.add("earnings_soon")
        return sorted(catalysts)

    def _calculate_quality_score(
        self,
        trend: str,
        iv_rank: float,
        recommendations: dict[str, Any],
        earnings_risk: dict[str, Any],
        volume: float,
        catalysts: list[str],
    ) -> float:
        score = 0.0
        score += {
            "strong_uptrend": 30,
            "uptrend": 20,
            "sideways": 10,
            "downtrend": 5,
            "strong_downtrend": 0,
        }.get(trend, 10)

        if volume > 2:
            score += 15
        elif volume > 1.5:
            score += 10
        elif volume > 1:
            score += 5

        if 30 <= iv_rank <= 70:
            score += 15
        elif 20 <= iv_rank <= 80:
            score += 10
        else:
            score += 5

        if recommendations:
            buys = (recommendations.get("strong_buy", 0) or 0) + (
                recommendations.get("buy", 0) or 0
            )
            total = recommendations.get("total_analysts", 0) or 0
            if total > 0:
                score += min(15, (buys / total) * 15)

        if not earnings_risk.get("has_earnings"):
            score += 10
        else:
            days = earnings_risk.get("days_until") or 0
            if days > 14:
                score += 8
            elif days > 7:
                score += 4

        positive = {
            "earnings_beat", "analyst_upgrade", "fda_approval",
            "ma_deal", "new_partnership", "bullish_analyst",
        }
        catalyst_count = sum(1 for c in catalysts if c in positive)
        score += min(15, catalyst_count * 5)

        return round(score, 1)

    def _get_recommendation(
        self, score: float, trend: str, _catalysts: list[str]
    ) -> tuple[str, str]:
        if score >= 80 and trend in {"strong_uptrend", "uptrend"}:
            return "strong_buy", "ציון איכות גבוה + מגמה חזקה"
        if score >= 65:
            return "buy", "ציון איכות טוב + תנאים נוחים"
        if score >= 50:
            return "watch", "פוטנציאל - שווה מעקב"
        if score >= 35:
            return "hold", "לא מתאים כרגע"
        return "skip", "תנאים לא נוחים"

    def _suggest_strategy(
        self, trend: str, iv_result, earnings: dict[str, Any]
    ) -> dict[str, Optional[str]]:
        iv_rank = iv_result.iv_rank if iv_result else 50.0
        if earnings.get("has_earnings") and (earnings.get("days_until") or 99) <= 14:
            return {"name": "אין - Earnings קרוב", "strikes": None}
        if iv_rank > 50 and trend in {"strong_uptrend", "uptrend"}:
            return {"name": "Bull Put Spread", "strikes": "Delta 0.16-0.20"}
        if iv_rank < 30 and trend in {"strong_uptrend", "uptrend"}:
            return {"name": "Bull Call Debit Spread", "strikes": "ATM + 5% OTM"}
        if iv_rank > 50 and trend == "sideways":
            return {"name": "Iron Condor", "strikes": "Delta 0.10-0.16 both sides"}
        return {"name": "Calendar Spread", "strikes": "ATM"}

    @staticmethod
    def _parse_float(val: Any) -> Optional[float]:
        if val is None or val == "":
            return None
        try:
            cleaned = str(val).replace("%", "").replace(",", "").replace("$", "").strip()
            return float(cleaned)
        except (ValueError, AttributeError, TypeError):
            return None

    # ───────────────────────── Telegram digest ─────────────────────────

    async def _send_daily_recommendations(self, top_stocks: list[dict[str, Any]]) -> None:
        if not top_stocks or self.telegram is None:
            return

        parts = [
            "🏆 *סריקת בוקר - מניות מומלצות*",
            f"📅 {datetime.now().strftime('%d/%m/%Y')}",
            f"📊 נסרקו {len(top_stocks)} מניות איכותיות",
            "",
            "━━━━━━━━━━━━━━━━━━━",
        ]

        rec_emoji = {
            "strong_buy": "🟢🟢", "buy": "🟢", "watch": "🟡",
            "hold": "⚪", "skip": "🔴",
        }
        trend_emoji = {
            "strong_uptrend": "📈📈", "uptrend": "📈",
            "sideways": "↔️", "downtrend": "📉", "strong_downtrend": "📉📉",
        }

        for i, stock in enumerate(top_stocks, start=1):
            iv_rank_val = stock.get("iv_rank")
            iv_rank_text = f"{iv_rank_val:.0f}" if isinstance(iv_rank_val, (int, float)) else "N/A"
            catalysts_heb = self._translate_catalysts(stock.get("catalysts") or [])
            news_context = (stock.get("news_impact_analysis") or "")[:200]
            parts.append(
                f"\n{i}. {rec_emoji.get(stock['recommendation'], '⚪')} *{stock['ticker']}* - ציון: {stock['quality_score']}/100\n\n"
                f"💰 מחיר: ${stock['price']:.2f} ({stock['change_pct']:+.2f}%)\n"
                f"{trend_emoji.get(stock['trend'], '↔️')} מגמה: {self._trend_hebrew(stock['trend'])}\n"
                f"📊 IV Rank: {iv_rank_text}\n"
                f"🎯 קטליסטים: {catalysts_heb or 'אין'}\n\n"
                f"💡 *אסטרטגיה:* {stock.get('suggested_strategy') or '—'}\n"
                f"📝 *סיבה:* {stock.get('recommendation_reason') or '—'}\n\n"
                f"📰 *הקשר:*\n{news_context}\n\n"
                "━━━━━━━━━━━━━━━━━━━"
            )

        parts.append("\n🎓 הסוכן ילמד מהתחזיות האלה ויעקוב אחריהן ב-24/48/72 שעות")

        try:
            await self.telegram.send_alert(
                title="🏆 מניות יומיות",
                body="\n".join(parts),
                urgency="medium",
            )
        except Exception:  # noqa: BLE001
            logger.exception("send_daily_recommendations: Telegram failed")

    @staticmethod
    def _trend_hebrew(trend: str) -> str:
        return {
            "strong_uptrend": "עלייה חזקה",
            "uptrend": "עלייה",
            "sideways": "דשדוש",
            "downtrend": "ירידה",
            "strong_downtrend": "ירידה חזקה",
        }.get(trend, trend)

    @staticmethod
    def _translate_catalysts(catalysts: list[str]) -> str:
        translations = {
            "earnings_beat": "Earnings מעל הצפי",
            "analyst_upgrade": "שדרוג אנליסט",
            "fda_approval": "אישור FDA",
            "ma_deal": "מיזוג/רכישה",
            "new_partnership": "שותפות חדשה",
            "bullish_analyst": "אנליסטים חיוביים",
            "earnings_soon": "Earnings קרוב ⚠️",
        }
        return ", ".join(translations.get(c, c) for c in catalysts)

    # ───────────────────────── follow-up + learning ─────────────────────────

    async def update_predictions(self) -> int:
        now = datetime.utcnow()
        cursor = self.db.stock_analyses.find(
            {
                "analysis_date": {
                    "$gte": now - timedelta(days=7),
                    "$lte": now - timedelta(hours=20),
                },
                "actual_move_pct": None,
            }
        )
        updated = 0
        async for analysis in cursor:
            ticker = analysis.get("ticker")
            entered_at = analysis.get("analysis_date")
            if not ticker or not isinstance(entered_at, datetime):
                continue
            days_passed = (now - entered_at).days

            quote = await asyncio.to_thread(self.finnhub.get_stock_quote, ticker)
            current = (quote or {}).get("price")
            try:
                current_price = float(current) if current is not None else None
            except (TypeError, ValueError):
                current_price = None
            if not current_price:
                continue

            original_price = float(analysis.get("price") or 0)
            if original_price <= 0:
                continue
            actual_move = (current_price - original_price) / original_price * 100.0

            rec = analysis.get("recommendation")
            was_correct = (
                rec in {"strong_buy", "buy"} and actual_move > 0
            ) or (rec == "skip" and actual_move <= 0)

            update_data: dict[str, Any] = {
                "actual_move_pct": round(actual_move, 2),
                "prediction_correct": was_correct,
            }
            if days_passed >= 7:
                update_data["price_after_7d"] = current_price
            elif days_passed >= 2:
                update_data["price_after_48h"] = current_price
            elif days_passed >= 1:
                update_data["price_after_24h"] = current_price

            await self.db.stock_analyses.update_one(
                {"_id": analysis["_id"]}, {"$set": update_data}
            )
            updated += 1
        logger.info("update_predictions: %d analyses updated", updated)
        return updated

    async def learn_patterns(self) -> int:
        ltm = LongTermMemory()
        cursor = self.db.stock_analyses.find(
            {"actual_move_pct": {"$ne": None}, "prediction_correct": {"$ne": None}}
        )
        successful: dict[str, list[float]] = defaultdict(list)
        async for analysis in cursor:
            if not analysis.get("prediction_correct"):
                continue
            catalysts_str = ",".join(sorted(analysis.get("catalysts") or []))
            key = f"{analysis.get('trend', 'unknown')}|{catalysts_str}"
            try:
                successful[key].append(float(analysis.get("actual_move_pct") or 0.0))
            except (TypeError, ValueError):
                continue

        saved = 0
        for key, moves in successful.items():
            if len(moves) < 3:
                continue
            avg_move = sum(moves) / len(moves)
            trend, catalysts = key.split("|", 1)
            pattern_text = (
                "דפוס מנצח:\n"
                f"מגמה: {trend}\n"
                f"קטליסטים: {catalysts}\n"
                f"תנועה ממוצעת: {avg_move:.2f}%\n"
                f"מספר דוגמאות: {len(moves)}"
            )
            try:
                await asyncio.to_thread(
                    ltm.save_market_pattern,
                    pattern_text,
                    {
                        "trend": trend,
                        "catalysts": catalysts,
                        "avg_move": round(avg_move, 2),
                        "sample_size": len(moves),
                    },
                )
                saved += 1
            except Exception:  # noqa: BLE001
                logger.exception("save_market_pattern failed for %s", key)

        logger.info("🎓 למד %d דפוסים", saved)
        return saved

    async def cleanup_old_analyses(self) -> int:
        try:
            result = await self.db.stock_analyses.delete_many(
                {"expires_at": {"$lt": datetime.utcnow()}}
            )
        except Exception:  # noqa: BLE001
            logger.exception("cleanup_old_analyses failed")
            return 0
        logger.info("🧹 נמחקו %s ניתוחים ישנים", result.deleted_count)
        return result.deleted_count


__all__ = ["DailyStockScanner"]
