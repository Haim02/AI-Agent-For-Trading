"""High-level autonomous agent that drives the LangGraph state machine."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

from agent.graph import create_agent_graph
from agent.state import initial_state
from db.connection import get_db
from memory.reflection_engine import ReflectionEngine, ReflectionEngineError
from scrapers.menthorq_scraper import MenthorQScraper
from services.telegram_service import TelegramService, TelegramServiceError

logger = logging.getLogger(__name__)


class AutonomousAgent:
    """Single entry point used by Telegram, the API, and the scheduler."""

    def __init__(self) -> None:
        self._graph = None
        self._lock = asyncio.Lock()
        self.last_scan_at: Optional[datetime] = None
        self.last_reflection_at: Optional[datetime] = None

    def _ensure_graph(self):
        if self._graph is None:
            self._graph = create_agent_graph()
        return self._graph

    async def warm_up(self) -> None:
        """Compile the graph eagerly so the first call is fast."""
        async with self._lock:
            self._ensure_graph()
        logger.info("🤖 סוכן אוטונומי מוכן לפעולה")

    async def run(self, user_message: str, session_id: Optional[str] = None) -> str:
        session = session_id or f"session:{uuid.uuid4().hex[:12]}"
        graph = self._ensure_graph()
        state = initial_state(user_message, session_id=session)
        final = await graph.ainvoke(state)
        return final.get("response") or "(לא התקבלה תשובה)"

    async def run_autonomous_task(self, task: str) -> str:
        session = f"auto:{uuid.uuid4().hex[:8]}"
        return await self.run(task, session_id=session)

    async def morning_routine(self) -> str:
        logger.info("🌅 מתחיל שגרת בוקר אוטונומית...")
        response = await self.run_autonomous_task(
            "סרוק את השוק ומצא הזדמנויות מסחר להיום."
        )
        self.last_scan_at = datetime.utcnow()
        await self._notify_telegram("🌅 סריקת בוקר", response)
        return response

    async def eod_routine(self) -> str:
        logger.info("🌆 מתחיל סיכום יום אוטונומי...")
        response = await self.run_autonomous_task(
            "נתח את יום המסחר של היום. מה קרה, מה הצליח, מה למדנו."
        )

        try:
            engine = ReflectionEngine()
            reflection = await engine.reflect_on_day()
            summary = reflection.get("summary")
            if summary:
                response = f"{response}\n\n📘 רפלקציה:\n{summary}"
            self.last_reflection_at = datetime.utcnow()
        except ReflectionEngineError as exc:
            logger.warning("Reflection skipped: %s", exc)
        except Exception:  # noqa: BLE001
            logger.exception("eod_routine: reflection failed")

        await self._notify_telegram("🌆 סיכום יום", response)
        return response

    async def risk_check(self) -> str:
        logger.info("🛡 בדיקת סיכון לפוזיציות פתוחות...")
        db = get_db()
        cursor = db.positions.find({"status": "open"})
        positions = await cursor.to_list(length=200)
        if not positions:
            return "אין פוזיציות פתוחות."

        alerts: list[str] = []
        gex_snapshot: Optional[dict[str, Any]] = None
        try:
            with MenthorQScraper(headless=True) as scraper:
                gex_snapshot = asdict(scraper.scrape_gex_data())
        except Exception:  # noqa: BLE001
            logger.exception("risk_check: MenthorQ scrape failed")

        call_wall = float((gex_snapshot or {}).get("call_wall") or 0.0)
        put_wall = float((gex_snapshot or {}).get("put_wall") or 0.0)
        spot = float((gex_snapshot or {}).get("spot_price") or 0.0)

        for pos in positions:
            ticker = pos.get("ticker", "?")
            strikes = [pos.get("short_call_strike"), pos.get("short_put_strike")]
            credit = float(pos.get("premium_received") or 0.0)
            current_value = float(pos.get("current_value") or 0.0)
            unrealized = current_value - credit if current_value else float(pos.get("unrealized_pnl") or 0.0)

            for strike in strikes:
                if strike is None:
                    continue
                try:
                    strike_val = float(strike)
                except (TypeError, ValueError):
                    continue
                if call_wall and abs(strike_val - call_wall) / call_wall < 0.003:
                    alerts.append(f"⚠️ {ticker}: סטרייק {strike_val} קרוב ל-Call Wall {call_wall}")
                if put_wall and abs(strike_val - put_wall) / put_wall < 0.003:
                    alerts.append(f"⚠️ {ticker}: סטרייק {strike_val} קרוב ל-Put Wall {put_wall}")

            if credit > 0 and unrealized < -2 * credit:
                alerts.append(
                    f"🚨 {ticker}: הפסד {unrealized:.2f}$ – יותר מ-2x מהקרדיט {credit:.2f}$"
                )

        if not alerts:
            return f"✅ אין סיכונים מיידיים ({len(positions)} פוזיציות נבדקו). ספוט: {spot}"

        body = "\n".join(alerts)
        await self._notify_telegram("🛡 בדיקת סיכון", body, urgency="high")
        return body

    async def _notify_telegram(self, title: str, body: str, urgency: str = "info") -> None:
        try:
            tg = TelegramService()
        except TelegramServiceError as exc:
            logger.warning("Telegram disabled: %s", exc)
            return
        try:
            await tg.send_alert(title=title, body=body, urgency=urgency)
        except Exception:  # noqa: BLE001
            logger.exception("Telegram send failed")


_agent_singleton: Optional[AutonomousAgent] = None


def get_autonomous_agent() -> AutonomousAgent:
    global _agent_singleton
    if _agent_singleton is None:
        _agent_singleton = AutonomousAgent()
    return _agent_singleton


__all__ = ["AutonomousAgent", "get_autonomous_agent"]
