"""Unusual Whales scraper via Playwright.

Logs in with credentials from ``UW_EMAIL`` / ``UW_PASSWORD`` and intercepts the
site's XHR/fetch responses to pull GEX, options flow, Market Tide, and news
JSON. Sessions are persisted to a temp file so we don't re-login on every call.

Use responsibly and stay within reasonable rate limits — automating a logged-in
session can violate the provider's Terms of Service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Any, Optional

from utils.time_helper import ISRAEL_TZ

logger = logging.getLogger(__name__)

UW_BASE = "https://unusualwhales.com"
UW_API_BASE = "https://api.unusualwhales.com"


def _session_path() -> str:
    return os.path.join(tempfile.gettempdir(), "uw_session.json")


class UnusualWhalesScraper:
    """Headless browser session against unusualwhales.com."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False
        self._session_file = _session_path()

    # ───────────────────────── browser lifecycle ─────────────────────────

    async def _start_browser(self) -> None:
        if self._browser:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed. Run: pip install playwright "
                "&& python -m playwright install chromium"
            ) from exc

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        storage = self._load_session()
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            storage_state=storage,
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        await self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
        )
        logger.info("✅ Browser started")

    async def _login(self) -> bool:
        email = os.getenv("UW_EMAIL", "")
        password = os.getenv("UW_PASSWORD", "")
        if not email or not password:
            logger.error("UW_EMAIL or UW_PASSWORD not set in .env")
            return False

        try:
            logger.info("Logging in as %s...", email)
            await self._page.goto(
                f"{UW_BASE}/login", wait_until="networkidle", timeout=30_000
            )
            await asyncio.sleep(2)

            await self._page.fill(
                'input[type="email"], input[name="email"], input[placeholder*="email" i]',
                email,
            )
            await asyncio.sleep(0.5)
            await self._page.fill(
                'input[type="password"], input[name="password"]', password
            )
            await asyncio.sleep(0.5)
            await self._page.click(
                'button[type="submit"], button:has-text("Sign in"), '
                'button:has-text("Log in"), button:has-text("Login")'
            )
            await self._page.wait_for_load_state("networkidle", timeout=15_000)
            await asyncio.sleep(3)

            current_url = self._page.url
            if "login" not in current_url and "auth" not in current_url:
                self._logged_in = True
                await self._async_save_session()
                logger.info("✅ Login successful!")
                return True
            logger.error("Login failed. URL: %s", current_url)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("Login error: %s", exc)
            return False

    async def _ensure_logged_in(self) -> bool:
        await self._start_browser()
        if self._logged_in:
            return True
        try:
            await self._page.goto(
                f"{UW_BASE}/dashboard", wait_until="networkidle", timeout=20_000
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard probe failed: %s", exc)
        if self._page and "login" not in self._page.url:
            self._logged_in = True
            logger.info("✅ Session restored!")
            return True
        return await self._login()

    async def _async_save_session(self) -> None:
        try:
            state = await self._context.storage_state()
            with open(self._session_file, "w", encoding="utf-8") as f:
                json.dump(state, f)
            logger.info("Session saved")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Session save failed: %s", exc)

    def _load_session(self) -> Optional[dict]:
        try:
            if os.path.exists(self._session_file):
                with open(self._session_file, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:  # noqa: BLE001
            logger.exception("Session load failed")
        return None

    # ───────────────────────── interceptor ─────────────────────────

    async def _intercept_page(
        self, url: str, wait_for_patterns: list[str], timeout: int = 20_000
    ) -> dict[str, Any]:
        """Navigate and capture JSON responses whose URL contains any of the patterns."""
        captured: dict[str, Any] = {}

        async def handle_response(response):  # type: ignore[no-untyped-def]
            try:
                resp_url = response.url
                if response.status != 200:
                    return
                for pattern in wait_for_patterns:
                    if pattern in resp_url:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct:
                            captured[pattern] = await response.json()
                            logger.info("Captured: %s", pattern)
                            return
            except Exception:  # noqa: BLE001
                pass

        self._page.on("response", handle_response)
        try:
            await self._page.goto(url, wait_until="networkidle", timeout=timeout)
            await asyncio.sleep(4)
            try:
                await self._page.evaluate("window.scrollTo(0, 300)")
                await asyncio.sleep(1)
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._page.remove_listener("response", handle_response)

        return captured

    # ───────────────────────── GEX ─────────────────────────

    async def get_gex_data(self, ticker: str = "SPY") -> dict:
        if not await self._ensure_logged_in():
            return {"error": "Login failed"}

        logger.info("Fetching GEX for %s...", ticker)
        captured = await self._intercept_page(
            url=f"{UW_BASE}/stocks/{ticker}/greek-exposure",
            wait_for_patterns=[
                "greek-exposure",
                "spot-exposures",
                "gex",
                f"/{ticker.lower()}",
            ],
        )

        if not captured:
            api = await self._direct_api_call(f"/api/stock/{ticker}/greek-exposure")
            if api:
                captured = {"greek-exposure": api}

        if not captured:
            return {"error": "No GEX data parsed"}

        return self._parse_gex_data(captured, ticker)

    @staticmethod
    def _extract_items(data: Any) -> list[dict]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("data", "results", "items"):
                v = data.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
        return []

    def _parse_gex_data(self, captured: dict, ticker: str) -> dict:
        gex_by_strike: dict[float, float] = {}
        spot = 0.0

        for _, data in captured.items():
            for item in self._extract_items(data):
                try:
                    strike = item.get("strike") or item.get("price") or 0
                    gex = (
                        item.get("gex")
                        or item.get("gamma_exposure")
                        or item.get("charm")
                        or 0
                    )
                    if strike and gex:
                        gex_by_strike[float(strike)] = float(gex)
                    if item.get("spot"):
                        spot = float(item["spot"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("GEX item parse error: %s", exc)

        if not gex_by_strike:
            return {"error": "No GEX data parsed"}

        if not spot:
            # Best-effort: take the median strike weighted by GEX magnitude.
            strikes = sorted(gex_by_strike.keys())
            spot = strikes[len(strikes) // 2]

        call_gex = {s: v for s, v in gex_by_strike.items() if s >= spot and v > 0}
        put_gex = {s: v for s, v in gex_by_strike.items() if s <= spot and v < 0}
        call_wall = max(call_gex, key=call_gex.get) if call_gex else None
        put_wall = min(put_gex, key=put_gex.get) if put_gex else None

        total_gex = sum(gex_by_strike.values())
        regime = "positive" if total_gex > 0 else "negative"

        cw_dist = ((call_wall - spot) / spot * 100) if call_wall and spot else None
        pw_dist = ((spot - put_wall) / spot * 100) if put_wall and spot else None

        return {
            "ticker": ticker,
            "spot_price": round(spot, 2),
            "call_wall": round(call_wall, 0) if call_wall else None,
            "put_wall": round(put_wall, 0) if put_wall else None,
            "call_wall_distance_pct": round(cw_dist, 2) if cw_dist is not None else None,
            "put_wall_distance_pct": round(pw_dist, 2) if pw_dist is not None else None,
            "regime": regime,
            "total_gex": round(total_gex / 1e9, 3),
            "source": "unusual_whales",
            "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
        }

    # ───────────────────────── flow ─────────────────────────

    async def get_options_flow(self, ticker: Optional[str] = None) -> list[dict]:
        if not await self._ensure_logged_in():
            return []

        logger.info("Fetching options flow...")
        url = f"{UW_BASE}/stocks/{ticker}/flow" if ticker else f"{UW_BASE}/flow-alerts"
        captured = await self._intercept_page(
            url=url,
            wait_for_patterns=["flow-alerts", "option-trades", "flow", "alerts"],
        )

        if not captured:
            endpoint = (
                f"/api/stock/{ticker}/flow-alerts"
                if ticker
                else "/api/option-trades/flow-alerts"
            )
            api = await self._direct_api_call(endpoint)
            if api:
                captured = {"flow": api}

        return self._parse_flow_data(captured)

    def _parse_flow_data(self, captured: dict) -> list[dict]:
        flows: list[dict] = []
        for _, data in captured.items():
            for item in self._extract_items(data)[:50]:
                try:
                    explicit_premium = item.get("premium")
                    if explicit_premium is not None:
                        premium = float(explicit_premium)
                    else:
                        try:
                            premium = (
                                float(item.get("price", 0))
                                * float(item.get("size", 0))
                                * 100
                            )
                        except (TypeError, ValueError):
                            premium = 0.0

                    opt_type = (item.get("option_type") or item.get("type") or "").lower()
                    side = (item.get("side") or item.get("execution") or "").lower()

                    sentiment = "neutral"
                    if opt_type == "call":
                        sentiment = "bullish" if "ask" in side else "bearish" if "bid" in side else "neutral"
                    elif opt_type == "put":
                        sentiment = "bearish" if "ask" in side else "bullish" if "bid" in side else "neutral"

                    flows.append(
                        {
                            "ticker": item.get("ticker") or item.get("symbol", ""),
                            "strike": item.get("strike"),
                            "expiry": item.get("expires") or item.get("expiry"),
                            "opt_type": opt_type,
                            "premium": round(premium),
                            "size": item.get("size", 0),
                            "trade_type": item.get("type", "") or item.get("trade_type", ""),
                            "sentiment": sentiment,
                            "side": side,
                            "timestamp": item.get("created_at") or item.get("time", ""),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Flow parse error: %s", exc)

        flows.sort(key=lambda x: x.get("premium", 0) or 0, reverse=True)
        return flows

    # ───────────────────────── market tide ─────────────────────────

    async def get_market_tide(self) -> dict:
        if not await self._ensure_logged_in():
            return {}

        logger.info("Fetching Market Tide...")
        captured = await self._intercept_page(
            url=f"{UW_BASE}/market-tide",
            wait_for_patterns=["market-tide", "market_tide", "tide"],
        )
        if not captured:
            api = await self._direct_api_call("/api/market/market-tide")
            if api:
                captured = {"tide": api}

        for _, data in captured.items():
            items = self._extract_items(data)
            if not items:
                continue
            try:
                call_premium = sum(float(i.get("call_premium", 0) or 0) for i in items)
                put_premium = sum(float(i.get("put_premium", 0) or 0) for i in items)
                total = call_premium + put_premium
                bull_pct = (call_premium / total * 100) if total else 50.0
                sentiment = (
                    "bullish" if bull_pct >= 60
                    else "bearish" if bull_pct <= 40
                    else "neutral"
                )
                return {
                    "call_premium": call_premium,
                    "put_premium": put_premium,
                    "bull_pct": round(bull_pct, 1),
                    "bear_pct": round(100 - bull_pct, 1),
                    "sentiment": sentiment,
                    "data_points": len(items),
                    "source": "unusual_whales",
                    "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("Market tide parse error: %s", exc)
        return {}

    # ───────────────────────── news ─────────────────────────

    async def get_uw_news(self) -> list[dict]:
        if not await self._ensure_logged_in():
            return []

        captured = await self._intercept_page(
            url=f"{UW_BASE}/news",
            wait_for_patterns=["headlines", "news", "articles"],
        )
        if not captured:
            api = await self._direct_api_call("/api/news/headlines")
            if api:
                captured = {"news": api}

        news: list[dict] = []
        for _, data in captured.items():
            for item in self._extract_items(data)[:20]:
                news.append(
                    {
                        "title": item.get("title") or item.get("headline", ""),
                        "source": item.get("source", ""),
                        "url": item.get("url", ""),
                        "published": item.get("published_at") or item.get("created_at", ""),
                        "tickers": item.get("tickers", []),
                    }
                )
        return news

    # ───────────────────────── net flow ─────────────────────────

    async def get_net_flow(self, ticker: Optional[str] = None) -> dict:
        if not await self._ensure_logged_in():
            return {}

        url = f"{UW_BASE}/stocks/{ticker}" if ticker else f"{UW_BASE}/net-flow"
        captured = await self._intercept_page(
            url=url,
            wait_for_patterns=["net-prem", "net_flow", "net-flow", "flow-per"],
        )
        for _, data in captured.items():
            items = self._extract_items(data)
            if not items:
                continue
            call_net = sum(
                float(i.get("call_premium", 0) or 0) - float(i.get("put_premium", 0) or 0)
                for i in items
            )
            return {
                "net_flow": round(call_net),
                "direction": "bullish" if call_net > 0 else "bearish",
                "ticker": ticker or "MARKET",
                "source": "unusual_whales",
            }
        return {}

    # ───────────────────────── direct API ─────────────────────────

    async def _direct_api_call(self, endpoint: str) -> dict:
        """Call the UW API directly, reusing the browser's session cookies."""
        try:
            cookies = await self._context.cookies()
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}"
                for c in cookies
                if "unusualwhales" in (c.get("domain") or "")
            )
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{UW_API_BASE}{endpoint}",
                    headers={
                        "Cookie": cookie_str,
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Accept": "application/json",
                        "Referer": UW_BASE,
                    },
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    logger.info("Direct API success: %s", endpoint)
                    return resp.json()
                logger.warning("Direct API %s: %s", resp.status_code, endpoint)
        except Exception as exc:  # noqa: BLE001
            logger.error("Direct API error: %s", exc)
        return {}

    # ───────────────────────── full report ─────────────────────────

    async def get_full_report(self, ticker: str = "SPY") -> dict:
        """Sequential because all four calls share the same Playwright page."""
        logger.info("Getting full UW report for %s", ticker)
        try:
            try:
                gex = await self.get_gex_data(ticker)
            except Exception:  # noqa: BLE001
                logger.exception("get_full_report: gex failed")
                gex = {}
            try:
                flow = await self.get_options_flow(ticker)
            except Exception:  # noqa: BLE001
                logger.exception("get_full_report: flow failed")
                flow = []
            try:
                tide = await self.get_market_tide()
            except Exception:  # noqa: BLE001
                logger.exception("get_full_report: market tide failed")
                tide = {}
            try:
                news = await self.get_uw_news()
            except Exception:  # noqa: BLE001
                logger.exception("get_full_report: news failed")
                news = []

            return {
                "ticker": ticker,
                "gex": gex,
                "flow": (flow or [])[:10],
                "market_tide": tide,
                "news": (news or [])[:10],
                "source": "unusual_whales",
                "timestamp": datetime.now(ISRAEL_TZ).strftime("%H:%M | %d/%m/%Y"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Full report error")
            return {"error": str(exc)}

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None


__all__ = ["UnusualWhalesScraper"]
