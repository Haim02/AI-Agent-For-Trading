"""Options-flow analysis: sweeps, blocks, smart money, sentiment."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import httpx

from db.connection import get_db
from utils.time_helper import ISRAEL_TZ

logger = logging.getLogger(__name__)


class OptionsFlowEngine:
    UW_BASE = "https://api.unusualwhales.com"

    def __init__(self) -> None:
        self.uw_key = os.getenv("UNUSUAL_WHALES_API_KEY")
        self.massive_key = os.getenv("MASSIVE_API_KEY")
        self.db = get_db()

    # ───────────────────────── public ─────────────────────────

    async def get_unusual_flow(self, min_premium: int = 100_000) -> list[dict]:
        if os.getenv("UW_EMAIL"):
            scraped = await self._get_uw_scraper_flow()
            if scraped:
                return [t for t in scraped if (t.get("premium") or 0) >= min_premium]
        if self.uw_key:
            result = await self._get_uw_flow()
            if result:
                return [t for t in result if t.get("premium", 0) >= min_premium]
        return await self._get_massive_flow(min_premium)

    async def _get_uw_scraper_flow(self) -> list[dict]:
        """Use the Playwright-based UW scraper to pull market-wide flow alerts."""
        try:
            from scrapers.uw_scraper import UnusualWhalesScraper
        except ImportError as exc:
            logger.warning("UW scraper unavailable: %s", exc)
            return []
        scraper = UnusualWhalesScraper()
        try:
            return await scraper.get_options_flow()
        except Exception:  # noqa: BLE001
            logger.exception("UW scraper flow failed")
            return []
        finally:
            await scraper.close()

    async def analyze_ticker_flow(self, ticker: str) -> dict:
        if self.uw_key:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{self.UW_BASE}/api/stock/{ticker}/flow-alerts",
                        headers={"Authorization": f"Bearer {self.uw_key}"},
                        timeout=15.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return self._analyze_flow_data(ticker, data.get("data", []))
                    logger.warning("UW ticker flow %s: HTTP %s", ticker, resp.status_code)
            except Exception as exc:  # noqa: BLE001
                logger.warning("UW ticker flow: %s", exc)
        return await self._massive_ticker_flow(ticker)

    # ───────────────────────── UW path ─────────────────────────

    async def _get_uw_flow(self) -> list[dict]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.UW_BASE}/api/option-trades/flow-alerts",
                    headers={"Authorization": f"Bearer {self.uw_key}"},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return self._parse_uw_flow(data.get("data", []))
                logger.warning("UW flow alerts: HTTP %s", resp.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("UW flow failed: %s", exc)
        return []

    def _parse_uw_flow(self, raw: list) -> list[dict]:
        parsed: list[dict] = []
        for item in raw[:50]:
            try:
                premium = float(item.get("price", 0)) * int(item.get("size", 0)) * 100
            except (TypeError, ValueError):
                premium = 0.0

            sentiment = self._determine_sentiment(
                opt_type=item.get("option_type", ""),
                side=item.get("side", ""),
                trade_type=item.get("type", ""),
            )

            try:
                is_unusual = int(item.get("volume", 0)) > int(item.get("open_interest", 1))
            except (TypeError, ValueError):
                is_unusual = False

            parsed.append(
                {
                    "ticker": item.get("ticker"),
                    "strike": item.get("strike"),
                    "expiry": item.get("expires"),
                    "opt_type": item.get("option_type"),
                    "premium": round(premium),
                    "size": item.get("size"),
                    "trade_type": item.get("type"),
                    "sentiment": sentiment,
                    "side": item.get("side"),
                    "is_unusual": is_unusual,
                    "timestamp": item.get("created_at"),
                }
            )
        return parsed

    def _determine_sentiment(self, opt_type: str, side: str, trade_type: str) -> str:
        """Call@Ask / Put@Bid = bullish; Call@Bid / Put@Ask = bearish."""
        opt_type = (opt_type or "").lower()
        side = (side or "").lower()
        if opt_type == "call":
            if side in {"ask", "above_ask"}:
                return "bullish"
            if side in {"bid", "below_bid"}:
                return "bearish"
        elif opt_type == "put":
            if side in {"ask", "above_ask"}:
                return "bearish"
            if side in {"bid", "below_bid"}:
                return "bullish"
        return "neutral"

    def _analyze_flow_data(self, ticker: str, trades: list) -> dict:
        bullish_premium = 0.0
        bearish_premium = 0.0
        bullish_count = 0
        bearish_count = 0
        sweeps: list[dict] = []
        blocks: list[dict] = []

        for trade in trades:
            try:
                premium = float(trade.get("price", 0)) * int(trade.get("size", 0)) * 100
            except (TypeError, ValueError):
                premium = 0.0
            sentiment = self._determine_sentiment(
                trade.get("option_type", ""),
                trade.get("side", ""),
                trade.get("type", ""),
            )

            if sentiment == "bullish":
                bullish_premium += premium
                bullish_count += 1
            elif sentiment == "bearish":
                bearish_premium += premium
                bearish_count += 1

            entry = {
                "premium": round(premium),
                "sentiment": sentiment,
                "strike": trade.get("strike"),
                "expiry": trade.get("expires"),
            }
            ttype = trade.get("type")
            if ttype == "sweep":
                sweeps.append(entry)
            elif ttype == "block":
                blocks.append(entry)

        total = bullish_premium + bearish_premium
        bull_pct = (bullish_premium / total * 100) if total else 50.0
        if bull_pct >= 65:
            overall = "bullish"
        elif bull_pct <= 35:
            overall = "bearish"
        else:
            overall = "neutral"

        return {
            "ticker": ticker,
            "overall_sentiment": overall,
            "bullish_premium": bullish_premium,
            "bearish_premium": bearish_premium,
            "bull_pct": round(bull_pct, 1),
            "bear_pct": round(100 - bull_pct, 1),
            "bullish_trades": bullish_count,
            "bearish_trades": bearish_count,
            "sweeps": sweeps[:5],
            "blocks": blocks[:5],
            "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
        }

    # ───────────────────────── Massive fallback ─────────────────────────

    async def _get_massive_flow(self, min_premium: int) -> list[dict]:
        """Massive unusual-options activity → standard flow format."""
        try:
            from tools.massive_tool import MassiveTool
        except Exception:  # noqa: BLE001
            logger.exception("MassiveTool import failed")
            return []
        massive = MassiveTool()
        try:
            trades = await massive.get_unusual_options_activity(min_volume=1000)
        except Exception:  # noqa: BLE001
            logger.exception("Massive get_unusual_options_activity failed")
            trades = []
        finally:
            await massive.close()
        if not trades:
            return []

        parsed: list[dict] = []
        for t in trades:
            try:
                premium = float(t.get("premium", 0) or 0)
            except (TypeError, ValueError):
                premium = 0.0
            if premium < min_premium:
                continue
            opt_type = (t.get("type") or "").lower()
            sentiment = "bullish" if opt_type == "call" else "bearish" if opt_type == "put" else "neutral"
            parsed.append(
                {
                    "ticker": t.get("ticker"),
                    "strike": t.get("strike"),
                    "expiry": t.get("expiration"),
                    "opt_type": opt_type,
                    "premium": round(premium),
                    "size": t.get("size"),
                    "trade_type": "sweep" if t.get("is_sweep") else "block",
                    "sentiment": sentiment,
                    "side": None,
                    "is_unusual": True,
                    "timestamp": t.get("timestamp"),
                }
            )
        return parsed

    async def _massive_ticker_flow(self, ticker: str) -> dict:
        """Coerce MassiveTool.get_options_flow output into _analyze_flow_data's shape."""
        try:
            from tools.massive_tool import MassiveTool
        except Exception:  # noqa: BLE001
            logger.exception("MassiveTool import failed")
            return self._empty_flow(ticker)
        massive = MassiveTool()
        try:
            data = await massive.get_options_flow(ticker)
        except Exception:  # noqa: BLE001
            logger.exception("Massive get_options_flow failed for %s", ticker)
            data = None
        finally:
            await massive.close()

        if not data:
            return self._empty_flow(ticker)

        sentiment = (data.get("sentiment") or "neutral").lower()
        if sentiment == "bullish":
            bull_pct, bear_pct = 70.0, 30.0
        elif sentiment == "bearish":
            bull_pct, bear_pct = 30.0, 70.0
        else:
            bull_pct = bear_pct = 50.0

        return {
            "ticker": ticker,
            "overall_sentiment": sentiment,
            "bullish_premium": 0,
            "bearish_premium": 0,
            "bull_pct": bull_pct,
            "bear_pct": bear_pct,
            "bullish_trades": int(data.get("calls_volume", 0) or 0),
            "bearish_trades": int(data.get("puts_volume", 0) or 0),
            "sweeps": [],
            "blocks": [],
            "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
        }

    @staticmethod
    def _empty_flow(ticker: str) -> dict:
        return {
            "ticker": ticker,
            "overall_sentiment": "neutral",
            "bullish_premium": 0,
            "bearish_premium": 0,
            "bull_pct": 50.0,
            "bear_pct": 50.0,
            "bullish_trades": 0,
            "bearish_trades": 0,
            "sweeps": [],
            "blocks": [],
            "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
        }


__all__ = ["OptionsFlowEngine"]
