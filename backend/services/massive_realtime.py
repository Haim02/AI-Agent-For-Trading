"""Massive WebSocket monitor – unusual options + sudden stock moves."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

import websockets

from services.telegram_service import TelegramService, TelegramServiceError

logger = logging.getLogger(__name__)


class MassiveRealtimeMonitor:
    """WebSocket connection to Massive for real-time alerts."""

    WS_URL = "wss://stream.massive.com/v1"
    WATCHLIST = [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META",
        "AMZN", "TSLA", "AMD", "SPY", "QQQ", "IWM",
    ]

    def __init__(self) -> None:
        self.api_key = os.getenv("MASSIVE_API_KEY")
        try:
            self.telegram: Optional[TelegramService] = TelegramService()
        except TelegramServiceError as exc:
            logger.warning("Telegram disabled in realtime monitor: %s", exc)
            self.telegram = None
        self.running = False

    async def connect_and_monitor(self) -> None:
        if not self.api_key:
            logger.warning("MASSIVE_API_KEY not set – realtime monitor disabled")
            return
        self.running = True
        while self.running:
            try:
                async with websockets.connect(
                    self.WS_URL,
                    extra_headers={"Authorization": f"Bearer {self.api_key}"},
                ) as ws:
                    await self._subscribe(ws)
                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            event = json.loads(message)
                        except (TypeError, ValueError):
                            logger.warning("Bad WS payload: %s", str(message)[:200])
                            continue
                        await self._handle_event(event)
            except asyncio.CancelledError:
                logger.info("Massive realtime monitor cancelled")
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("WS error: %s – reconnecting in 30s", exc)
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    break

    async def _subscribe(self, ws) -> None:
        subscriptions = [
            {
                "action": "subscribe",
                "channel": "options.unusual",
                "filters": {"min_premium": 100000},
            },
            {
                "action": "subscribe",
                "channel": "stocks.alerts",
                "tickers": self.WATCHLIST,
                "filters": {"min_change_pct": 2},
            },
        ]
        for sub in subscriptions:
            await ws.send(json.dumps(sub))

    async def _handle_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "unusual_options":
            await self._handle_unusual(event)
        elif event_type == "stock_alert":
            await self._handle_stock_alert(event)

    async def _handle_unusual(self, event: dict) -> None:
        if self.telegram is None:
            return
        ticker = event.get("ticker", "—")
        premium = float(event.get("premium", 0) or 0)
        opt_type = event.get("type", "")
        strike = event.get("strike", "—")
        expiration = event.get("expiration", "—")
        size = int(event.get("size", 0) or 0)
        direction = "Call 🟢" if opt_type == "call" else "Put 🔴"
        bias = "🐂 מישהו גדול שורי" if opt_type == "call" else "🐻 מישהו גדול דובי"
        message = (
            "🚨 פעילות חריגה באופציות!\n\n"
            f"💵 ${ticker} - {direction}\n"
            f"📊 Strike: ${strike}\n"
            f"📅 פקיעה: {expiration}\n"
            f"🔢 גודל: {size:,} חוזים\n"
            f"💰 פרמיה: ${premium:,.0f}\n\n"
            f"{bias}"
        )
        try:
            await self.telegram.send_alert(
                title="פעילות אופציות חריגה",
                body=message,
                urgency="high",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Telegram unusual alert failed")

    async def _handle_stock_alert(self, event: dict) -> None:
        if self.telegram is None:
            return
        ticker = event.get("ticker", "—")
        try:
            change_pct = float(event.get("change_pct") or 0)
        except (TypeError, ValueError):
            change_pct = 0.0
        price = event.get("price", "—")
        volume = int(event.get("volume", 0) or 0)
        direction = "📈 עלייה" if change_pct > 0 else "📉 ירידה"
        message = (
            f"⚡ תנועה חדה ב-{ticker}!\n\n"
            f"{direction} של {change_pct:.2f}%\n"
            f"💰 מחיר: ${price}\n"
            f"📊 נפח: {volume:,}"
        )
        try:
            await self.telegram.send_alert(
                title=f"תנועה חדה ב-{ticker}",
                body=message,
                urgency="medium",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Telegram stock alert failed")


__all__ = ["MassiveRealtimeMonitor"]
