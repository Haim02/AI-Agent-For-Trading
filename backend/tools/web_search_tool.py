"""Unified web-search facade: Perplexity (primary) with OpenAI fallback."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

from openai import OpenAI
from openai import APIConnectionError, APIError, APIStatusError, RateLimitError

from tools.perplexity_tool import PerplexityTool, PerplexityToolError

logger = logging.getLogger(__name__)


class WebSearchTool:
    """High-level web search for the trading agent."""

    def __init__(
        self,
        perplexity: Optional[PerplexityTool] = None,
        openai_api_key: Optional[str] = None,
        openai_model: str = "gpt-4o-mini",
    ) -> None:
        self._perplexity: Optional[PerplexityTool] = perplexity
        if self._perplexity is None:
            try:
                self._perplexity = PerplexityTool()
            except PerplexityToolError as exc:
                logger.warning("Perplexity unavailable: %s", exc)
                self._perplexity = None

        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.openai_model = openai_model
        self._openai_client: Optional[OpenAI] = None
        if self.openai_api_key:
            try:
                self._openai_client = OpenAI(api_key=self.openai_api_key)
            except Exception:  # noqa: BLE001
                logger.exception("OpenAI client init failed")
                self._openai_client = None

    # ───────────────────────── low-level providers ─────────────────────────

    def search_perplexity(self, query: str) -> dict[str, Any]:
        if not self._perplexity:
            raise PerplexityToolError("PerplexityTool not initialised")
        result = self._perplexity.search(query)
        return {
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "timestamp": result.get("timestamp") or datetime.utcnow().isoformat(),
        }

    def search_openai(self, query: str) -> dict[str, Any]:
        if not self._openai_client:
            raise RuntimeError("OpenAI client not available")
        try:
            response = self._openai_client.chat.completions.create(
                model=self.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a financial research assistant. "
                            "Search for current market data and news. "
                            "Always provide sources."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
            )
        except (APIConnectionError, APIStatusError, APIError, RateLimitError) as exc:
            logger.warning("OpenAI search failed: %s", exc)
            raise
        answer = (response.choices[0].message.content or "").strip()
        return {
            "answer": answer,
            "timestamp": datetime.utcnow().isoformat(),
        }

    # ───────────────────────── high-level helpers ─────────────────────────

    def search_best(self, query: str) -> str:
        """Try Perplexity first (real web), fall back to OpenAI, then a Hebrew failure note."""
        try:
            payload = self.search_perplexity(query)
            answer = (payload.get("answer") or "").strip()
            sources = payload.get("sources") or []
            if answer:
                text = answer
                if sources:
                    text += "\n\nמקורות:\n" + "\n".join(f"• {s}" for s in sources[:5])
                return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("Perplexity path failed: %s", exc)

        try:
            payload = self.search_openai(query)
            answer = (payload.get("answer") or "").strip()
            if answer:
                return answer
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI path failed: %s", exc)

        return "לא הצלחתי למצוא מידע"

    def find_high_iv_stocks(self) -> str:
        query = (
            "Find stocks with highest implied volatility right now. Look for:\n"
            "1. Stocks with IV Rank above 70\n"
            "2. Liquid options (volume > 1000)\n"
            "3. No earnings in next 7 days\n"
            "Include: ticker, IV%, IV Rank, reason for high IV.\n"
            "Format as a list."
        )
        return self.search_best(query)

    def get_market_overview(self) -> str:
        query = (
            "Current US stock market overview:\n"
            "- SPY and QQQ current price and trend\n"
            "- VIX current level and what it means\n"
            "- Top gaining and losing sectors today\n"
            "- Key economic events this week\n"
            "Answer in Hebrew."
        )
        return self.search_best(query)

    def research_ticker(self, ticker: str) -> str:
        ticker = ticker.upper().strip()
        query = (
            f"Research {ticker} stock right now:\n"
            "- Current price and today's movement\n"
            "- Why is it moving (news, earnings, sector)?\n"
            "- Implied volatility level\n"
            "- Upcoming catalysts or risks\n"
            "- Analyst sentiment\n"
            "Answer in Hebrew."
        )
        return self.search_best(query)


__all__ = ["WebSearchTool"]
