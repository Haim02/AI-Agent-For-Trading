import asyncio
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agent.autonomous_agent import get_autonomous_agent
from db.connection import get_db
from memory.long_term import LongTermMemory
from memory.reflection_engine import ReflectionEngine, ReflectionEngineError
from memory.short_term import ShortTermMemory
from services.daily_stock_scanner import DailyStockScanner
from services.news_monitor import NewsMonitor
from services.smart_news_monitor import SmartNewsMonitor
from services.telegram_service import TelegramService, TelegramServiceError

logger = logging.getLogger(__name__)

EST = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

_scheduler: Optional[AsyncIOScheduler] = None
_telegram: Optional[TelegramService] = None
_news_monitor: Optional[NewsMonitor] = None
_smart_monitor: Optional[SmartNewsMonitor] = None
_stock_scanner: Optional[DailyStockScanner] = None
_market_analyzer = None
_flow_engine = None
_wall_detector = None
_morning_briefing = None
_weekly_report = None


def get_smart_news_monitor() -> SmartNewsMonitor:
    global _smart_monitor
    if _smart_monitor is None:
        _smart_monitor = SmartNewsMonitor()
    return _smart_monitor


def get_daily_stock_scanner() -> DailyStockScanner:
    global _stock_scanner
    if _stock_scanner is None:
        _stock_scanner = DailyStockScanner()
    return _stock_scanner


def _get_market_analyzer():
    global _market_analyzer
    if _market_analyzer is None:
        from analytics.market_structure import MarketStructureAnalyzer
        _market_analyzer = MarketStructureAnalyzer()
    return _market_analyzer


def _get_flow_engine():
    global _flow_engine
    if _flow_engine is None:
        from analytics.flow_engine import OptionsFlowEngine
        _flow_engine = OptionsFlowEngine()
    return _flow_engine


def _get_wall_detector():
    global _wall_detector
    if _wall_detector is None:
        from services.wall_break_detector import WallBreakDetector
        _wall_detector = WallBreakDetector()
    return _wall_detector


def _get_morning_briefing():
    global _morning_briefing
    if _morning_briefing is None:
        from services.morning_briefing import MorningBriefing
        _morning_briefing = MorningBriefing()
    return _morning_briefing


def _get_weekly_report():
    global _weekly_report
    if _weekly_report is None:
        from services.weekly_report import WeeklyReportService
        _weekly_report = WeeklyReportService()
    return _weekly_report


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


async def send_market_open_alert() -> None:
    """16:30 Israel – US market open ping + today's SPX GEX levels for 0DTE."""
    try:
        from tools.macro_tool import MacroTool
        from utils.time_helper import now_israel

        macro = await asyncio.to_thread(MacroTool().get_market_summary)
        now = now_israel()

        tg = _telegram_service()
        if tg is None:
            return
        body = (
            f"🔔 *השוק נפתח - {now.strftime('%H:%M')} (ישראל)*\n\n"
            f"{macro}\n\n"
            "📊 מוכן לסריקה? שלח:\n"
            "\"תמליץ על מניות להיום\""
        )
        await tg.send_alert(
            title="🔔 השוק האמריקאי נפתח!",
            body=body,
            urgency="medium",
        )

        # Immediately follow with today's SPX gamma levels – the core 0DTE map.
        try:
            from analytics.gex_engine import GEXEngine

            gex = await GEXEngine().get_full_gex_analysis("SPX")
            if "error" not in gex and gex.get("analysis_hebrew"):
                gex_body = gex["analysis_hebrew"]
                if gex.get("warning") == "estimated_levels":
                    gex_body += "\n\n⚠️ נתונים משוערים בלבד"
                await tg.send_alert(
                    title="⚡ רמות GEX להיום – SPX 0DTE",
                    body=gex_body,
                    urgency="medium",
                )
        except Exception:  # noqa: BLE001
            logger.exception("market_open GEX report failed")
    except Exception:  # noqa: BLE001
        logger.exception("send_market_open_alert failed")


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
    """Persist a GEX snapshot every 30 min (FlashAlpha → UW → Massive → yfinance)."""
    if not _is_market_hours():
        logger.debug("Skipping GEX snapshot – outside market hours")
        return
    try:
        from analytics.gex_engine import GEXEngine

        gex = await GEXEngine().get_full_gex_analysis("SPX")
        if "error" in gex:
            logger.warning("GEX snapshot skipped – %s", gex["error"])
            return
        db = get_db()
        snapshot = dict(gex)
        snapshot["timestamp"] = datetime.utcnow()
        await db.gex_history.insert_one(snapshot)
        logger.info(
            "GEX snapshot saved – regime=%s total=%s",
            gex.get("regime"), gex.get("total_gex"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("job_scrape_gex_data failed")


async def job_dex_monitor() -> None:
    """Intraday Delta support/resistance watch for SPX (user-requested)."""
    if not _is_market_hours():
        return
    try:
        from services.dex_monitor import DexMonitor

        await DexMonitor().check_and_alert("SPX")
    except Exception:  # noqa: BLE001
        logger.exception("job_dex_monitor failed")


async def job_smart_news_company() -> None:
    try:
        await get_smart_news_monitor().check_company_news()
    except Exception:  # noqa: BLE001
        logger.exception("job_smart_news_company failed")


async def job_smart_news_macro() -> None:
    try:
        await get_smart_news_monitor().check_macro_news()
    except Exception:  # noqa: BLE001
        logger.exception("job_smart_news_macro failed")


async def job_smart_news_earnings() -> None:
    try:
        await get_smart_news_monitor().check_earnings_today()
    except Exception:  # noqa: BLE001
        logger.exception("job_smart_news_earnings failed")


async def job_smart_news_impact() -> None:
    try:
        await get_smart_news_monitor().update_news_impact()
    except Exception:  # noqa: BLE001
        logger.exception("job_smart_news_impact failed")


async def job_smart_news_learn() -> None:
    try:
        await get_smart_news_monitor().learn_from_news()
    except Exception:  # noqa: BLE001
        logger.exception("job_smart_news_learn failed")


async def job_smart_news_cleanup() -> None:
    try:
        await get_smart_news_monitor().cleanup_old_news()
    except Exception:  # noqa: BLE001
        logger.exception("job_smart_news_cleanup failed")


async def job_daily_stock_scan() -> None:
    try:
        await get_daily_stock_scanner().run_daily_scan()
    except Exception:  # noqa: BLE001
        logger.exception("job_daily_stock_scan failed")


async def job_update_predictions() -> None:
    try:
        await get_daily_stock_scanner().update_predictions()
    except Exception:  # noqa: BLE001
        logger.exception("job_update_predictions failed")


async def job_learn_patterns() -> None:
    try:
        await get_daily_stock_scanner().learn_patterns()
    except Exception:  # noqa: BLE001
        logger.exception("job_learn_patterns failed")


async def job_cleanup_analyses() -> None:
    try:
        await get_daily_stock_scanner().cleanup_old_analyses()
    except Exception:  # noqa: BLE001
        logger.exception("job_cleanup_analyses failed")


async def job_monitor_unusual_options() -> None:
    if not _is_market_hours():
        return
    from tools.massive_tool import MassiveTool

    massive = MassiveTool()
    if not massive.enabled:
        await massive.close()
        return
    try:
        trades = await massive.get_unusual_options_activity(min_volume=2500)
    except Exception:  # noqa: BLE001
        logger.exception("job_monitor_unusual_options failed")
        return
    finally:
        await massive.close()

    if not trades:
        return
    tg = _telegram_service()
    if tg is None:
        return
    lines = ["🚨 פעילות אופציות חריגה:"]
    for t in trades[:5]:
        opt_type = t.get("type", "")
        emoji = "🟢" if opt_type == "call" else "🔴"
        premium = float(t.get("premium", 0) or 0)
        lines.append(
            f"{emoji} ${t.get('ticker', '?')} {opt_type.upper()} "
            f"${t.get('strike', '?')} – ${premium:,.0f}"
        )
    try:
        await tg.send_alert(
            title="פעילות אופציות חריגה",
            body="\n".join(lines),
            urgency="high",
        )
    except Exception:  # noqa: BLE001
        logger.exception("unusual_options telegram send failed")


async def job_morning_briefing() -> None:
    """14:00 Israel – scan watchlist + StrategySelector → actionable digest."""
    try:
        await _get_morning_briefing().generate_daily_briefing()
    except Exception:  # noqa: BLE001
        logger.exception("morning_briefing failed")


async def job_weekly_report() -> None:
    """Sunday 08:00 Israel – ship weekly P&L digest to Telegram."""
    try:
        await _get_weekly_report().generate_weekly_report()
    except Exception:  # noqa: BLE001
        logger.exception("weekly_report failed")


async def job_morning_narrative() -> None:
    """14:30 Israel – send FlashAlpha 7-section narrative for SPY + QQQ."""
    try:
        from tools.flashalpha_tool import FlashAlphaTool
    except ImportError:
        logger.warning("morning_narrative: FlashAlphaTool unavailable")
        return

    fa = FlashAlphaTool()
    tg = _telegram_service()
    for ticker in ("SPY", "QQQ"):
        try:
            result = await fa.get_narrative_hebrew(ticker)
        except Exception:  # noqa: BLE001
            logger.exception("morning_narrative: %s failed", ticker)
            continue
        if "error" in result:
            logger.warning(
                "morning_narrative: %s skipped (%s)", ticker, result.get("error")
            )
            continue
        if tg is None:
            continue
        try:
            await tg.send_alert(
                title=f"🧠 ניתוח AI – {ticker}",
                body=result.get("formatted_message") or "—",
                urgency="medium",
            )
        except Exception:  # noqa: BLE001
            logger.exception("morning_narrative: telegram failed for %s", ticker)


async def job_whale_signals() -> None:
    """Every 30 min during US session – ping only score 85+ whale/golden trades."""
    try:
        from tools.flashalpha_tool import FlashAlphaTool
    except ImportError:
        return

    fa = FlashAlphaTool()
    tg = _telegram_service()
    if tg is None:
        return

    intent_he = {"bullish": "🟢 שורי", "bearish": "🔴 דובי", "neutral": "🟡"}
    for ticker in ("SPY", "QQQ", "NVDA", "TSLA"):
        try:
            data = await fa.get_flow_signals(
                symbol=ticker, min_score=85, window_minutes=30, limit=3
            )
        except Exception:  # noqa: BLE001
            logger.exception("whale_signals: %s fetch failed", ticker)
            continue
        if not data or "error" in data:
            continue
        signals = data.get("signals") or []
        for sig in signals[:2]:
            tags = sig.get("tags") or []
            if "whale" not in tags and "golden" not in tags:
                continue
            right = "Call" if sig.get("right") == "C" else "Put"
            intent_str = intent_he.get(sig.get("intent"), "🟡")
            body = (
                f"🐋 לוויתן! {ticker}\n\n"
                f"{intent_str} {right} ${sig.get('strike', 0):,.0f}\n"
                f"פקיעה: {sig.get('expiry', '?')} ({sig.get('dte', 0)}d)\n"
                f"פרמיה: ${sig.get('premium', 0):,.0f}\n"
                f"ניקוד: {sig.get('score', 0)}/100"
            )
            try:
                await tg.send_alert(title=f"🐋 {ticker}", body=body, urgency="high")
            except Exception:  # noqa: BLE001
                logger.exception("whale_signals: telegram failed for %s", ticker)


async def job_momentum_scan() -> None:
    """15:00 Israel – FinViz momentum scan + top-5 Telegram digest."""
    try:
        scanner = _get_daily_stock_scanner()
        result = await scanner.scan_momentum_stocks(limit=15)
    except Exception:  # noqa: BLE001
        logger.exception("momentum_scan: scan failed")
        return

    tg = _telegram_service()
    if tg is None:
        return

    top5 = (result.get("stocks") or [])[:5]
    if not top5:
        return
    lines = ["🔍 סריקת מניות יומית", ""]
    for s in top5:
        change = s.get("change_pct") or 0
        lines.append(f"📈 {s.get('ticker', '?')} +{change:.1f}%")
        news = s.get("news") or []
        if news:
            lines.append(f"   {str(news[0])[:80]}")
        lines.append("")
    try:
        await tg.send_alert(
            title="🔍 סריקת מניות", body="\n".join(lines), urgency="low"
        )
    except Exception:  # noqa: BLE001
        logger.exception("momentum_scan: telegram failed")


async def job_daily_gex_report() -> None:
    """14:00 Israel – send GEX+Flow report for SPY/QQQ/SPX."""
    analyzer = _get_market_analyzer()
    tg = _telegram_service()
    for ticker in ["SPY", "QQQ", "SPX"]:
        try:
            report = await analyzer.get_daily_report(ticker)
        except Exception:  # noqa: BLE001
            logger.exception("daily_gex_report: failed for %s", ticker)
            continue
        if tg is None:
            continue
        try:
            await tg.send_alert(
                title=f"📊 ניתוח GEX יומי – {ticker}",
                body=report.get("report_hebrew") or "(אין דוח)",
                urgency="medium",
            )
        except Exception:  # noqa: BLE001
            logger.exception("daily_gex_report: telegram failed for %s", ticker)


async def job_wall_break_check() -> None:
    """Every 5 min during market hours – watch SPY for Wall/Flip crosses."""
    try:
        await _get_wall_detector().check_for_breaks("SPY")
    except Exception:  # noqa: BLE001
        logger.exception("wall_break_check failed")


async def job_unusual_flow_check() -> None:
    """Every 15 min – $500k+ sweeps/blocks, top 3 alerted."""
    try:
        flows = await _get_flow_engine().get_unusual_flow(min_premium=500_000)
    except Exception:  # noqa: BLE001
        logger.exception("unusual_flow: fetch failed")
        return
    if not flows:
        return
    top = sorted(flows, key=lambda x: x.get("premium", 0) or 0, reverse=True)[:3]
    tg = _telegram_service()
    if tg is None:
        return
    for trade in top:
        direction = "🟢 שורי" if trade.get("sentiment") == "bullish" else "🔴 דובי"
        trade_type = (
            "⚡ SWEEP – דחיפות גבוהה!"
            if trade.get("trade_type") == "sweep"
            else "🏦 BLOCK – Smart Money"
        )
        premium = trade.get("premium") or 0
        size = trade.get("size") or 0
        body = (
            f"🚨 {trade_type}\n\n"
            f"💹 {trade.get('ticker', '?')} {direction}\n"
            f"🎯 Strike: ${trade.get('strike', '?')}\n"
            f"📅 פקיעה: {trade.get('expiry', '?')}\n"
            f"💰 פרמיה: ${premium:,}\n"
            f"📊 גודל: {size} חוזים"
        )
        try:
            await tg.send_alert(title="🐋 Unusual Flow!", body=body, urgency="high")
        except Exception:  # noqa: BLE001
            logger.exception("unusual_flow: telegram failed")


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
    # 08:00 Israel (sun-thu = Israel work week)
    _scheduler.add_job(
        job_run_morning_scan,
        CronTrigger(day_of_week="sun-thu", hour=5, minute=0, timezone=UTC),
        id="morning_briefing_israel",
        replace_existing=True,
        max_instances=1,
    )
    # 23:30 Israel
    _scheduler.add_job(
        job_run_eod_routine,
        CronTrigger(day_of_week="mon-fri", hour=20, minute=30, timezone=UTC),
        id="eod_summary",
        replace_existing=True,
        max_instances=1,
    )
    # 16:30 Israel – market open alert
    _scheduler.add_job(
        send_market_open_alert,
        CronTrigger(day_of_week="mon-fri", hour=13, minute=30, timezone=UTC),
        id="market_open_alert",
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
    # DEX (Delta) support/resistance monitor – every 30 min during the US session.
    _scheduler.add_job(
        job_dex_monitor,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="10,40", timezone=EST),
        id="dex_monitor",
        replace_existing=True,
        max_instances=1,
    )

    # ───────── Smart news monitor (Israel hours) ─────────
    # News during US market hours: 16:30–23:00 Israel = 13:30–20:00 UTC
    _scheduler.add_job(
        job_smart_news_company,
        CronTrigger(day_of_week="mon-fri", hour="13-20", minute="0,30", timezone=UTC),
        id="news_company",
        replace_existing=True,
        max_instances=1,
    )
    # Macro check 3×/day Israel time: 08:00, 13:00, 20:00 IL = 05:00, 10:00, 17:00 UTC
    _scheduler.add_job(
        job_smart_news_macro,
        CronTrigger(day_of_week="sun-fri", hour="5,10,17", minute=0, timezone=UTC),
        id="macro_news",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_smart_news_earnings,
        CronTrigger(hour=7, minute=0, timezone=EST),
        id="news_earnings",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_smart_news_impact,
        CronTrigger(hour=17, minute=0, timezone=EST),
        id="news_impact",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_smart_news_learn,
        CronTrigger(hour=2, minute=0, timezone=EST),
        id="news_learn",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_smart_news_cleanup,
        CronTrigger(hour=2, minute=30, timezone=EST),
        id="news_cleanup",
        replace_existing=True,
        max_instances=1,
    )

    # ───────── Daily stock scanner ─────────
    # Pre-market scan 14:00 Israel = 11:00 UTC (30 min before US pre-market)
    _scheduler.add_job(
        job_daily_stock_scan,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=0, timezone=UTC),
        id="pre_market_scan",
        replace_existing=True,
        max_instances=1,
    )

    # ───────── GEX + Flow market-structure jobs ─────────
    # Morning briefing 14:00 Israel = 11:00 UTC – actionable strategy digest.
    _scheduler.add_job(
        job_morning_briefing,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=0, timezone=UTC),
        id="morning_briefing",
        replace_existing=True,
        max_instances=1,
    )
    # Daily GEX report 14:05 Israel = 11:05 UTC – per-ticker GEX/Flow dump,
    # offset 5 min so it doesn't share the slot with morning_briefing.
    _scheduler.add_job(
        job_daily_gex_report,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=5, timezone=UTC),
        id="daily_gex_report",
        replace_existing=True,
        max_instances=1,
    )
    # Wall-break watcher every 5 min during US session = 13–20 UTC (16:30–23:00 IL).
    _scheduler.add_job(
        job_wall_break_check,
        CronTrigger(day_of_week="mon-fri", hour="13-20", minute="*/5", timezone=UTC),
        id="wall_break_check",
        replace_existing=True,
        max_instances=1,
    )
    # Unusual flow $500k+ every 15 min during US session.
    _scheduler.add_job(
        job_unusual_flow_check,
        CronTrigger(day_of_week="mon-fri", hour="13-20", minute="*/15", timezone=UTC),
        id="unusual_flow",
        replace_existing=True,
        max_instances=1,
    )

    # Weekly P&L digest – Sunday 08:00 Israel = 05:00 UTC.
    _scheduler.add_job(
        job_weekly_report,
        CronTrigger(day_of_week="sun", hour=5, minute=0, timezone=UTC),
        id="weekly_report",
        replace_existing=True,
        max_instances=1,
    )

    # Momentum scan – 15:00 Israel = 12:00 UTC, weekdays.
    _scheduler.add_job(
        job_momentum_scan,
        CronTrigger(day_of_week="mon-fri", hour=12, minute=0, timezone=UTC),
        id="momentum_scan",
        replace_existing=True,
        max_instances=1,
    )

    # Morning narrative – 14:30 Israel = 11:30 UTC.
    _scheduler.add_job(
        job_morning_narrative,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=30, timezone=UTC),
        id="morning_narrative",
        replace_existing=True,
        max_instances=1,
    )

    # Whale-signal sweeps every 30 min during US session (14:00–21:30 UTC ≈ 17–24 IL).
    _scheduler.add_job(
        job_whale_signals,
        CronTrigger(day_of_week="mon-fri", hour="14-21", minute="*/30", timezone=UTC),
        id="whale_signals",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_update_predictions,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=EST),
        id="update_predictions",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_learn_patterns,
        CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=EST),
        id="learn_patterns",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_cleanup_analyses,
        CronTrigger(day=1, hour=3, minute=0, timezone=EST),
        id="cleanup_analyses",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        job_monitor_unusual_options,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/15", timezone=EST),
        id="unusual_options",
        replace_existing=True,
        max_instances=1,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started. Israel-time jobs: morning_briefing_israel (08:00), "
        "pre_market_scan (14:00), market_open_alert+SPX GEX (16:30), "
        "news_company (16:30–23:00), eod_summary (23:30), macro_news (08:00/13:00/20:00). "
        "Session jobs: dex_monitor (30m), wall_break_check (5m), gex_snapshot (30m), "
        "risk_check, iv_spikes, unusual_flow, whale_signals."
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None
