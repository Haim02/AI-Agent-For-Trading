import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from dataclasses import asdict

from agent.autonomous_agent import get_autonomous_agent
from db.connection import get_db
from memory.long_term import LongTermMemory
from memory.reflection_engine import ReflectionEngine, ReflectionEngineError
from memory.short_term import ShortTermMemory
from scrapers.menthorq_scraper import MenthorQScraper
from services.news_monitor import NewsMonitor
from services.telegram_service import TelegramService, TelegramServiceError

logger = logging.getLogger(__name__)

EST = ZoneInfo("America/New_York")

_scheduler: Optional[AsyncIOScheduler] = None
_telegram: Optional[TelegramService] = None
_news_monitor: Optional[NewsMonitor] = None


# ───────────────────────── helpers ─────────────────────────

def _telegram_service() -> Optional[TelegramService]:
    global _telegram
    if _telegram is not None:
        return _telegram
    try:
        _telegram = TelegramService()
        return _telegram
    except TelegramServiceError as exc:
        logger.warning("Telegram disabled (alerts will be skipped): %s", exc)
        return None


def _monitor() -> NewsMonitor:
    global _news_monitor
    if _news_monitor is None:
        _news_monitor = NewsMonitor()
    return _news_monitor


def _is_market_hours(now: Optional[datetime] = None) -> bool:
    """9:30 → 16:00 EST, Mon-Fri."""
    now = now or datetime.now(EST)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


async def _send_alerts(alerts: list[dict]) -> None:
    tg = _telegram_service()
    if tg is None or not alerts:
        return
    for alert in alerts:
        title = alert.get("title", "Market alert")
        urgency = alert.get("urgency", "info")
        tickers = ", ".join(alert.get("tickers_affected", [])) or "—"
        source = alert.get("source", "")
        body_parts = [
            alert.get("summary", ""),
            f"\n*Tickers:* {tickers}",
        ]
        if source:
            body_parts.append(f"*Source:* {source}")
        await tg.send_alert(title=title, body="\n".join(body_parts), urgency=urgency)


# ───────────────────────── jobs ─────────────────────────

async def job_check_market_news() -> None:
    if not _is_market_hours():
        logger.debug("Skipping market news job — outside market hours")
        return
    try:
        alerts = _monitor().check_market_news()
        await _send_alerts(alerts)
    except Exception:  # noqa: BLE001
        logger.exception("job_check_market_news failed")


async def job_check_iv_spikes() -> None:
    if not _is_market_hours():
        logger.debug("Skipping IV spike scan – outside market hours")
        return
    try:
        alerts = _monitor().check_iv_spikes()
        await _send_alerts(alerts)
    except Exception:  # noqa: BLE001
        logger.exception("job_check_iv_spikes failed")


async def job_check_ticker_news() -> None:
    if not _is_market_hours():
        logger.debug("Skipping ticker news job — outside market hours")
        return
    try:
        db = get_db()
        cursor = db.positions.find({"status": "open"}, {"ticker": 1})
        docs = await cursor.to_list(length=200)
        tickers = sorted({d["ticker"].upper() for d in docs if d.get("ticker")})
        if not tickers:
            logger.debug("No open positions — skipping ticker news job")
            return
        alerts = _monitor().check_ticker_news(tickers)
        await _send_alerts(alerts)
    except Exception:  # noqa: BLE001
        logger.exception("job_check_ticker_news failed")


async def job_reflect_on_day() -> None:
    try:
        engine = ReflectionEngine()
    except ReflectionEngineError as exc:
        logger.warning("Reflection skipped — %s", exc)
        return
    try:
        result = await engine.reflect_on_day()
        logger.info("Daily reflection saved: %s", result.get("saved"))
        tg = _telegram_service()
        if tg is not None and result.get("summary"):
            await tg.send_alert(
                title="סיכום למידה יומי",
                body=result["summary"],
                urgency="info",
            )
    except Exception:  # noqa: BLE001
        logger.exception("job_reflect_on_day failed")


async def job_cleanup_short_term() -> None:
    try:
        result = await ShortTermMemory().cleanup_expired()
        logger.info("ShortTermMemory cleanup result: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("job_cleanup_short_term failed")


def job_purge_raw_data() -> None:
    try:
        result = LongTermMemory().delete_raw_data(older_than_days=7)
        logger.info("LongTermMemory raw-data purge: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("job_purge_raw_data failed")


async def job_run_morning_scan() -> None:
    try:
        await get_autonomous_agent().morning_routine()
    except Exception:  # noqa: BLE001
        logger.exception("job_run_morning_scan failed")


async def job_run_eod_routine() -> None:
    try:
        await get_autonomous_agent().eod_routine()
    except Exception:  # noqa: BLE001
        logger.exception("job_run_eod_routine failed")


async def job_risk_check() -> None:
    if not _is_market_hours():
        logger.debug("Skipping risk check – outside market hours")
        return
    try:
        await get_autonomous_agent().risk_check()
    except Exception:  # noqa: BLE001
        logger.exception("job_risk_check failed")


async def job_scrape_gex_data() -> None:
    if not _is_market_hours():
        logger.debug("Skipping GEX scrape – outside market hours")
        return
    try:
        with MenthorQScraper(headless=True) as scraper:
            gex = scraper.scrape_gex_data()
        db = get_db()
        snapshot = asdict(gex)
        snapshot["timestamp"] = gex.timestamp
        await db.gex_history.insert_one(snapshot)
        logger.info("GEX snapshot saved – regime=%s total=%s", gex.regime, gex.gex_total)
    except Exception:  # noqa: BLE001
        logger.exception("job_scrape_gex_data failed")


async def job_daily_summary() -> None:
    try:
        db = get_db()
        today = datetime.now(EST).date().isoformat()

        open_positions = await db.positions.count_documents({"status": "open"})
        closed_positions = await db.positions.count_documents({"status": "closed"})

        pipeline = [
            {"$match": {"realized_pnl": {"$ne": None}}},
            {"$group": {"_id": None, "total": {"$sum": "$realized_pnl"}}},
        ]
        agg = await db.positions.aggregate(pipeline).to_list(length=1)
        total_realized = float(agg[0]["total"]) if agg else 0.0

        journal = await db.journal.find_one({"date": today})
        daily_pnl = float(journal["daily_pnl"]) if journal and journal.get("daily_pnl") is not None else 0.0
        notes = journal.get("agent_summary") if journal else None

        summary = {
            "date": today,
            "daily_pnl": daily_pnl,
            "open_positions": open_positions,
            "closed_positions": closed_positions,
            "total_realized_pnl": total_realized,
            "notes": notes,
        }

        tg = _telegram_service()
        if tg is not None:
            await tg.send_market_summary(summary)
    except Exception:  # noqa: BLE001
        logger.exception("job_daily_summary failed")


# ───────────────────────── lifecycle ─────────────────────────

def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone=EST)

    _scheduler.add_job(
        job_check_market_news,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/30", timezone=EST),
        id="market_news",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_check_ticker_news,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute=0, timezone=EST),
        id="ticker_news",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_daily_summary,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=EST),
        id="daily_summary",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_reflect_on_day,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=EST),
        id="reflect_on_day",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_cleanup_short_term,
        CronTrigger(hour=0, minute=0, timezone=EST),
        id="cleanup_short_term",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_purge_raw_data,
        CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=EST),
        id="purge_raw_data",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_run_morning_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=EST),
        id="morning_scan",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_run_eod_routine,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=EST),
        id="eod_routine",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_risk_check,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute=0, timezone=EST),
        id="risk_check",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_scrape_gex_data,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/30", timezone=EST),
        id="gex_snapshot",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_check_iv_spikes,
        CronTrigger(day_of_week="mon-fri", hour="9-15/2", minute=15, timezone=EST),
        id="iv_spikes",
        replace_existing=True,
        max_instances=1,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started (EST). Jobs: market_news, ticker_news, daily_summary, "
        "reflect_on_day, cleanup_short_term, purge_raw_data, morning_scan, "
        "eod_routine, risk_check, gex_snapshot, iv_spikes"
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None
