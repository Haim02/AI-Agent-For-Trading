"""Reddit sentiment scanner — finds trending tickers in r/wallstreetbets and r/options."""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

import requests

logger = logging.getLogger(__name__)

REDDIT_URLS = [
    "https://www.reddit.com/r/wallstreetbets/hot.json?limit=25",
    "https://www.reddit.com/r/options/hot.json?limit=25",
]
HEADERS = {"User-Agent": "OptionsAgent/1.0"}

# Word-shaped patterns that look like tickers but aren't.
STOPWORDS = {
    "USA", "USD", "CEO", "CFO", "GDP", "ETF", "IPO", "ATH", "ATL", "DD",
    "YOLO", "FOMO", "PUMP", "DUMP", "WSB", "SPY", "OP", "TLDR", "EOD",
    "OPEN", "CALL", "PUT", "ITM", "OTM", "ATM", "BUY", "SELL", "HOLD",
    "EPS", "PE", "GAIN", "LOSS", "NEW", "BIG", "OLD", "BAD", "GOOD",
    "EDIT", "EU", "EV", "IV", "IRA", "ROI", "WTF", "LOL", "TIL",
    "MSFT", "ETH", "BTC", "AI", "USA", "GTC", "OCC",
}

BULLISH_WORDS = {
    "moon", "rocket", "🚀", "calls", "buy", "long", "bull", "bullish",
    "all in", "up", "rip", "to the moon", "yolo",
}
BEARISH_WORDS = {
    "puts", "short", "bear", "bearish", "crash", "dump", "down",
    "sell", "tank", "drop",
}

KNOWN_TICKERS = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AMD",
    "NFLX", "SPY", "QQQ", "IWM", "BABA", "DIS", "BA", "GS", "JPM", "BAC",
    "XOM", "CVX", "WMT", "COST", "TGT", "HD", "UBER", "LYFT", "SNOW",
    "PLTR", "COIN", "HOOD", "F", "GM", "RIVN", "NIO", "SOFI", "DKNG",
    "ROKU", "SPOT", "SHOP", "SQ", "PYPL", "V", "MA", "MU", "INTC", "QCOM",
    "TSM", "ASML", "CRM", "NOW", "ORCL", "ADBE", "CSCO", "AVGO", "MRVL",
    "SMCI", "ARM", "TQQQ", "SQQQ", "VIX", "VTI", "VOO", "GME", "AMC",
    "BB", "NOK", "CLSK", "MARA", "RIOT", "MSTR", "C", "WFC", "AXP", "MMM",
}

TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")


def _classify_post(text: str) -> str:
    lower = text.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in lower)
    bear = sum(1 for w in BEARISH_WORDS if w in lower)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


class RedditSentimentTool:
    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def _fetch_posts(self) -> list[dict[str, Any]]:
        posts: list[dict[str, Any]] = []
        for url in REDDIT_URLS:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=self.timeout)
                resp.raise_for_status()
                children = (resp.json().get("data") or {}).get("children") or []
                for child in children:
                    payload = child.get("data") or {}
                    posts.append(
                        {
                            "title": payload.get("title") or "",
                            "selftext": payload.get("selftext") or "",
                            "score": int(payload.get("score") or 0),
                            "subreddit": payload.get("subreddit") or "",
                        }
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Reddit fetch failed for %s", url)
        return posts

    def get_trending_tickers(self) -> list[dict[str, Any]]:
        posts = self._fetch_posts()
        counts: Counter[str] = Counter()
        sentiments: dict[str, list[str]] = {}

        for post in posts:
            text = f"{post['title']}\n{post['selftext']}"
            sentiment = _classify_post(text)
            seen: set[str] = set()
            for token in TICKER_RE.findall(text):
                if token in STOPWORDS or token in seen:
                    continue
                if token not in KNOWN_TICKERS:
                    continue
                counts[token] += 1
                sentiments.setdefault(token, []).append(sentiment)
                seen.add(token)

        results: list[dict[str, Any]] = []
        for ticker, mentions in counts.most_common(10):
            votes = sentiments.get(ticker, [])
            bull = votes.count("bullish")
            bear = votes.count("bearish")
            if bull > bear:
                sentiment = "bullish"
            elif bear > bull:
                sentiment = "bearish"
            else:
                sentiment = "neutral"
            results.append(
                {
                    "ticker": ticker,
                    "mentions": mentions,
                    "sentiment": sentiment,
                }
            )
        return results

    def analyze_sentiment(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper().strip()
        posts = self._fetch_posts()
        bull = 0
        bear = 0
        mentions = 0
        for post in posts:
            text = f"{post['title']}\n{post['selftext']}"
            if ticker not in TICKER_RE.findall(text):
                continue
            mentions += 1
            classification = _classify_post(text)
            if classification == "bullish":
                bull += 1
            elif classification == "bearish":
                bear += 1
        if mentions == 0:
            return {
                "ticker": ticker,
                "sentiment": "unknown",
                "confidence": 0.0,
                "mentions": 0,
            }
        if bull > bear:
            sentiment = "bullish"
            confidence = bull / mentions
        elif bear > bull:
            sentiment = "bearish"
            confidence = bear / mentions
        else:
            sentiment = "neutral"
            confidence = 0.5
        return {
            "ticker": ticker,
            "sentiment": sentiment,
            "confidence": round(confidence, 2),
            "mentions": mentions,
        }


__all__ = ["RedditSentimentTool"]
