import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from analytics.iv_rank_calculator import IVRankCalculator, SP500_TOP_50
from tools.perplexity_tool import PerplexityTool, PerplexityToolError

logger = logging.getLogger(__name__)


SIGNIFICANCE_RULES = (
    "Consider an item significant only if at least one is true:\n"
    "  - VIX moved more than 5% in either direction\n"
    "  - SPY or QQQ moved more than 1% intraday\n"
    "  - Fed announcement, rate decision, or unexpected Fed-speak\n"
    "  - Major earnings surprise from a large-cap (>$50B market cap)\n"
    "  - Geopolitical event with clear US-equity impact\n"
    "If nothing meets the bar, return an empty alerts list."
)


def _json_instruction() -> str:
    return (
        "Return ONLY valid JSON, no prose, matching exactly this schema:\n"
        '{"alerts": [{"title": str, "summary": str, '
        '"tickers_affected": [str], "urgency": "high"|"medium"|"info", '
        '"source": str}]}'
    )


class NewsMonitor:
    """Polls Perplexity for market-moving news and returns structured alerts."""

    def __init__(
        self,
        perplexity: Optional[PerplexityTool] = None,
        iv_calculator: Optional[IVRankCalculator] = None,
    ) -> None:
        self._perplexity = perplexity
        self._iv_calculator = iv_calculator or IVRankCalculator()
        self._iv_baseline: dict[str, float] = {}
        self._last_iv_scan: Optional[datetime] = None

    def _tool(self) -> PerplexityTool:
        if self._perplexity is None:
            self._perplexity = PerplexityTool()
        return self._perplexity

    def check_market_news(self) -> list[dict[str, Any]]:
        prompt = (
            "Search: breaking market news US stocks options VIX last 2 hours.\n\n"
            f"{SIGNIFICANCE_RULES}\n\n"
            f"{_json_instruction()}"
        )
        return self._run(prompt, context="market")

    def check_ticker_news(self, tickers: list[str]) -> list[dict[str, Any]]:
        if not tickers:
            return []
        joined = ", ".join(t.upper() for t in tickers)
        prompt = (
            f"Search for breaking news in the last 2 hours about any of these tickers: {joined}.\n"
            "Only include items that would materially move the stock or its options today.\n\n"
            f"{SIGNIFICANCE_RULES}\n\n"
            f"{_json_instruction()}"
        )
        return self._run(prompt, context=f"tickers={joined}")

    def check_iv_spikes(self, threshold: float = 20.0) -> list[dict[str, Any]]:
        """Scan top tickers and return alerts for IV Rank jumps above the threshold."""
        alerts: list[dict[str, Any]] = []
        for ticker in SP500_TOP_50:
            try:
                result = self._iv_calculator.calculate_iv_rank(ticker)
            except Exception:  # noqa: BLE001
                logger.exception("IV spike check failed for %s", ticker)
                continue
            if result is None:
                continue

            new_rank = float(result.iv_rank)
            old_rank = self._iv_baseline.get(ticker)
            self._iv_baseline[ticker] = new_rank

            if old_rank is None:
                continue
            if (new_rank - old_rank) <= threshold:
                continue

            strategy = (
                result.recommended_strategies[0]
                if result.recommended_strategies
                else "Iron Condor"
            )
            summary = (
                f"📈 {ticker}: IV Rank קפץ מ-{old_rank:.1f} ל-{new_rank:.1f}\n"
                f"💰 הפרמיה התייקרה משמעותית\n"
                f"✅ שקול: {strategy}\n"
                f"⚠️ בדוק: Earnings? חדשות? אירוע?"
            )
            alerts.append(
                {
                    "title": f"🚨 קפיצת IV – {ticker}",
                    "summary": summary,
                    "tickers_affected": [ticker],
                    "urgency": "high",
                    "source": "IV Rank monitor",
                }
            )

        self._last_iv_scan = datetime.utcnow()
        logger.info("IV spike check → %d alerts", len(alerts))
        return alerts

    def _run(self, prompt: str, context: str) -> list[dict[str, Any]]:
        try:
            result = self._tool().search(prompt)
        except PerplexityToolError as exc:
            logger.warning("NewsMonitor (%s) Perplexity failed: %s", context, exc)
            return []

        alerts = _parse_alerts(result.get("answer", ""))
        fallback_source = result.get("sources", [""])[0] if result.get("sources") else ""
        for alert in alerts:
            alert.setdefault("source", fallback_source)
            alert.setdefault("urgency", "info")
            alert.setdefault("tickers_affected", [])
        logger.info("NewsMonitor (%s) → %d alerts", context, len(alerts))
        return alerts


def _parse_alerts(text: str) -> list[dict[str, Any]]:
    if not text:
        return []

    # Strip markdown code fences if Perplexity wrapped the JSON.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text

    # Find first balanced top-level object.
    brace = candidate.find("{")
    if brace == -1:
        return []
    candidate = candidate[brace:]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Try to repair by trimming trailing garbage after last closing brace.
        last = candidate.rfind("}")
        if last == -1:
            return []
        try:
            data = json.loads(candidate[: last + 1])
        except json.JSONDecodeError as exc:
            logger.debug("NewsMonitor JSON decode failed: %s", exc)
            return []

    alerts = data.get("alerts") if isinstance(data, dict) else None
    if not isinstance(alerts, list):
        return []
    return [a for a in alerts if isinstance(a, dict) and a.get("title")]
