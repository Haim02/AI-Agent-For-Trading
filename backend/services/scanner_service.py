import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from analytics.gex_calculator import GEXCalculator
from analytics.iv_rank_calculator import IVRankCalculator
from db.connection import get_db
from scrapers.finviz_scraper import FinVizScraper, TickerDetail
from services.telegram_service import TelegramService, TelegramServiceError
from tools.perplexity_tool import PerplexityTool, PerplexityToolError

logger = logging.getLogger(__name__)


def _to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {k: _to_dict(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dict(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_iv_pct(text: str) -> Optional[float]:
    if not text:
        return None
    parts = [p.strip().rstrip("%") for p in text.replace(",", " ").split() if p]
    for p in parts:
        try:
            return float(p)
        except ValueError:
            continue
    return None


def _earnings_within_days(earnings_text: str, days: int = 14) -> bool:
    if not earnings_text:
        return False
    text = earnings_text.strip()
    for fmt in ("%b %d/%Y", "%b %d %Y", "%b %d", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text.split(" - ")[0].strip(), fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.utcnow().year)
            return 0 <= (dt - datetime.utcnow()).days <= days
        except ValueError:
            continue
    return False


def _strong_trend(detail: TickerDetail) -> bool:
    price = detail.price
    if price is None:
        return False
    sma_50 = _parse_iv_pct(detail.sma_50)
    sma_200 = _parse_iv_pct(detail.sma_200)
    if sma_50 is None or sma_200 is None:
        return False
    return sma_50 > -2 and sma_200 > -2 and (detail.change_pct or 0) > 0


def _positive_sentiment(news_answer: str) -> bool:
    if not news_answer:
        return False
    text = news_answer.lower()
    pos_keywords = ["beat", "upgrade", "growth", "bullish", "positive", "strong", "rally"]
    neg_keywords = ["miss", "downgrade", "bearish", "lawsuit", "decline", "weak", "warning"]
    pos = sum(1 for k in pos_keywords if k in text)
    neg = sum(1 for k in neg_keywords if k in text)
    return pos > neg


class ScannerService:
    """Morning scan: FinViz screener → ranked candidates with GEX + news."""

    def __init__(
        self,
        finviz: Optional[FinVizScraper] = None,
        gex: Optional[GEXCalculator] = None,
        perplexity: Optional[PerplexityTool] = None,
        telegram: Optional[TelegramService] = None,
        iv_rank: Optional[IVRankCalculator] = None,
    ) -> None:
        self._injected_finviz = finviz
        self.gex = gex or GEXCalculator()
        self.iv_rank = iv_rank or IVRankCalculator()
        self._perplexity = perplexity
        self._telegram = telegram

    def _perplexity_tool(self) -> Optional[PerplexityTool]:
        if self._perplexity is not None:
            return self._perplexity
        try:
            self._perplexity = PerplexityTool()
        except PerplexityToolError as exc:
            logger.warning("Perplexity unavailable: %s", exc)
            return None
        return self._perplexity

    def _telegram_service(self) -> Optional[TelegramService]:
        if self._telegram is not None:
            return self._telegram
        try:
            self._telegram = TelegramService()
        except TelegramServiceError as exc:
            logger.warning("Telegram unavailable: %s", exc)
            return None
        return self._telegram

    async def run_morning_scan(self) -> dict:
        candidates: list[dict] = []

        finviz_cm = self._injected_finviz or FinVizScraper(headless=True)
        owned = self._injected_finviz is None
        try:
            if owned:
                finviz_cm.start()
            summaries = finviz_cm.scrape_screener(max_pages=1)[:20]

            for summary in summaries:
                ticker = summary.ticker
                try:
                    detail = finviz_cm.scrape_ticker(ticker)
                except Exception:  # noqa: BLE001
                    logger.exception("FinViz scrape_ticker failed for %s", ticker)
                    continue

                try:
                    gex_data = self.gex.calculate_gex(ticker)
                except Exception:  # noqa: BLE001
                    logger.exception("GEX calculation failed for %s", ticker)
                    gex_data = None

                try:
                    iv_result = self.iv_rank.calculate_iv_rank(ticker)
                except Exception:  # noqa: BLE001
                    logger.exception("IV rank failed for %s", ticker)
                    iv_result = None

                news_payload: dict = {}
                tool = self._perplexity_tool()
                if tool is not None:
                    try:
                        news_payload = tool.research_ticker(ticker)
                    except PerplexityToolError as exc:
                        logger.warning("Perplexity research failed for %s: %s", ticker, exc)

                score, score_breakdown = self._score(detail, gex_data, news_payload, iv_result)
                strategy = self._suggest_strategy(gex_data, detail, iv_result)

                candidates.append(
                    {
                        "ticker": ticker,
                        "score": score,
                        "score_breakdown": score_breakdown,
                        "detail": _to_dict(detail),
                        "gex": _to_dict(gex_data) if gex_data else None,
                        "iv_rank": _to_dict(iv_result) if iv_result else None,
                        "news": news_payload,
                        "strategy": strategy,
                    }
                )
        finally:
            if owned:
                finviz_cm.stop()

        candidates.sort(key=lambda c: c["score"], reverse=True)
        top = candidates[:5]

        sell_candidates = [c for c in candidates if (c.get("iv_rank") or {}).get("iv_rank", 0) >= 50]
        buy_candidates = [
            c for c in candidates
            if 0 < (c.get("iv_rank") or {}).get("iv_rank", 0) < 25
        ]
        neutral_candidates = [
            c for c in candidates if c not in sell_candidates and c not in buy_candidates
        ]

        scan_doc = {
            "timestamp": datetime.utcnow(),
            "date": datetime.utcnow().date().isoformat(),
            "candidates_count": len(candidates),
            "top": top,
            "sell_candidates": sell_candidates,
            "buy_candidates": buy_candidates,
            "neutral_candidates": neutral_candidates,
        }
        try:
            db = get_db()
            await db.scans.insert_one(dict(scan_doc))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist scan to MongoDB")

        message = self.format_scan_result(scan_doc)
        tg = self._telegram_service()
        if tg is not None:
            await tg.send_message(message)

        scan_doc.pop("_id", None)
        return _to_dict(scan_doc)

    def _score(
        self,
        detail: TickerDetail,
        gex_data,
        news_payload: dict,
        iv_result=None,
    ) -> tuple[int, dict]:
        breakdown = {
            "iv_rank": 0,
            "gex_positive": 0,
            "no_earnings_soon": 0,
            "strong_trend": 0,
            "positive_news": 0,
        }

        iv_rank_value = getattr(iv_result, "iv_rank", None)
        if iv_rank_value is not None and iv_rank_value >= 80:
            breakdown["iv_rank"] = 30
        elif iv_rank_value is not None and iv_rank_value >= 50:
            breakdown["iv_rank"] = 20
        else:
            iv = _parse_iv_pct(detail.iv_pct)
            if iv is not None and iv > 50:
                breakdown["iv_rank"] = 20

        if gex_data is not None and gex_data.regime == "positive":
            breakdown["gex_positive"] = 20

        if not _earnings_within_days(detail.earnings_date, days=14):
            breakdown["no_earnings_soon"] = 20

        if _strong_trend(detail):
            breakdown["strong_trend"] = 20

        if _positive_sentiment(news_payload.get("answer", "")):
            breakdown["positive_news"] = 20

        return sum(breakdown.values()), breakdown

    @staticmethod
    def _suggest_strategy(gex_data, detail: TickerDetail, iv_result=None) -> str:
        if iv_result is not None and iv_result.recommended_strategies:
            return iv_result.recommended_strategies[0]
        if gex_data is None:
            return "Iron Condor"
        if gex_data.regime == "positive":
            return "Cash Secured Put / Bull Put Spread"
        return "Long Straddle / Long Calls"

    def format_scan_result(self, scan_data: dict) -> str:
        date = scan_data.get("date") or datetime.utcnow().date().isoformat()
        lines = [
            f"🔍 סריקת בוקר – {date}",
            "",
            "🏆 המניות המובילות להיום:",
            "",
        ]
        for idx, item in enumerate(scan_data.get("top", []), start=1):
            ticker = item.get("ticker", "?")
            score = item.get("score", 0)
            detail = item.get("detail") or {}
            price = detail.get("price", "—")
            iv = detail.get("iv_pct", "—")
            earnings = detail.get("earnings_date", "—")
            gex = item.get("gex") or {}
            regime = gex.get("regime", "—")
            iv_block = item.get("iv_rank") or {}
            iv_rank_val = iv_block.get("iv_rank", "—")
            iv_strength = iv_block.get("signal_strength", "—")
            iv_explain = iv_block.get("explanation", "")
            iv_strats = iv_block.get("recommended_strategies") or []
            headlines = detail.get("news") or []
            headline = headlines[0]["title"] if headlines else "—"
            strategy = item.get("strategy", "—")

            lines.extend(
                [
                    f"{idx}. {ticker} – ציון: {score}/100",
                    f"   💰 מחיר: ${price}",
                    f"   📊 IV: {iv} | GEX: {regime}",
                    f"   📊 IV Rank: {iv_rank_val} – {iv_strength}",
                ]
            )
            if iv_explain:
                lines.append(f"   💡 {iv_explain}")
            if iv_strats:
                lines.append(f"   ✅ אסטרטגיות: {', '.join(iv_strats)}")
            lines.extend(
                [
                    f"   📅 Earnings: {earnings}",
                    f"   📰 {headline}",
                    f"   ✅ אסטרטגיה מומלצת: {strategy}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()


__all__ = ["ScannerService"]
