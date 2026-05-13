import logging
import os
import time
from datetime import datetime
from typing import Any, Optional

from openai import OpenAI
from openai import APIConnectionError, APIError, APIStatusError, RateLimitError

logger = logging.getLogger(__name__)

PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
DEFAULT_MODEL = "sonar"
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5


class PerplexityToolError(RuntimeError):
    """Raised when the Perplexity API cannot be reached after retries."""


class PerplexityTool:
    """Thin wrapper around Perplexity's OpenAI-compatible Chat Completions API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("PERPLEXITY_API_KEY")
        if not self.api_key:
            raise PerplexityToolError("PERPLEXITY_API_KEY is not set")

        self.model = model
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=PERPLEXITY_BASE_URL,
            timeout=timeout,
        )

    # ───────────────────────── internal ─────────────────────────

    def _chat(self, prompt: str, system: Optional[str] = None) -> tuple[str, list[str]]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                )
                answer = (response.choices[0].message.content or "").strip()
                sources = self._extract_sources(response)
                return answer, sources
            except RateLimitError as exc:
                last_exc = exc
                logger.warning("Perplexity rate-limited (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, exc)
            except (APIConnectionError, APIStatusError, APIError) as exc:
                last_exc = exc
                logger.warning("Perplexity API error (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, exc)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.exception("Unexpected Perplexity failure (attempt %d/%d)", attempt, MAX_ATTEMPTS)

            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        raise PerplexityToolError(
            f"Perplexity request failed after {MAX_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    @staticmethod
    def _extract_sources(response: Any) -> list[str]:
        """Perplexity returns citations either on the top-level response or per-choice."""
        for path in ("citations", "search_results"):
            value = getattr(response, path, None)
            if value:
                return [_as_url(item) for item in value if _as_url(item)]

        try:
            raw = response.model_dump()
        except Exception:  # noqa: BLE001
            raw = {}

        for key in ("citations", "search_results"):
            value = raw.get(key)
            if value:
                return [_as_url(item) for item in value if _as_url(item)]

        return []

    def _result(self, query: str, answer: str, sources: list[str]) -> dict[str, Any]:
        return {
            "query": query,
            "answer": answer,
            "sources": sources,
            "timestamp": datetime.utcnow().isoformat(),
        }

    # ───────────────────────── public API ─────────────────────────

    def search(self, query: str) -> dict[str, Any]:
        logger.info("Perplexity search: %s", query)
        answer, sources = self._chat(query)
        return self._result(query, answer, sources)

    def research_ticker(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper().strip()
        prompt = (
            f"Latest news and analysis for {ticker} stock. "
            "Include: recent price movement reasons, upcoming catalysts, "
            "analyst sentiment, any risks or opportunities."
        )
        system = (
            "You are a financial research assistant. Be concise, factual, and cite recent sources. "
            "Structure the response with clear sections."
        )
        logger.info("Perplexity research_ticker: %s", ticker)
        answer, sources = self._chat(prompt, system=system)
        return self._result(prompt, answer, sources)

    def market_overview(self) -> dict[str, Any]:
        prompt = (
            "Current US stock market overview today. "
            "SPY and QQQ trend, VIX level, key macro events this week "
            "affecting options trading."
        )
        system = (
            "You are a market research assistant. Be concise, factual, and cite recent sources. "
            "Focus on information relevant to short-dated options traders."
        )
        logger.info("Perplexity market_overview")
        answer, sources = self._chat(prompt, system=system)
        return self._result(prompt, answer, sources)

    def analyze_event(self, event: str) -> dict[str, Any]:
        prompt = (
            f"Research and analyze the following market event or question: {event}\n\n"
            "Provide context, expected impact on US equity options markets, "
            "and any actionable considerations."
        )
        system = (
            "You are an options-market analyst. Be concise, factual, and cite recent sources."
        )
        logger.info("Perplexity analyze_event: %s", event)
        answer, sources = self._chat(prompt, system=system)
        return self._result(prompt, answer, sources)


def _as_url(item: Any) -> Optional[str]:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("url") or item.get("link") or item.get("href")
    return getattr(item, "url", None)
