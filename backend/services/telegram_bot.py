import asyncio
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent.autonomous_agent import AutonomousAgent, get_autonomous_agent
from analytics.gex_calculator import GEXCalculator
from db.connection import get_db
from memory.long_term import LongTermMemory
from scrapers.menthorq_scraper import MenthorQScraper
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

EST = ZoneInfo("America/New_York")

WELCOME = "שלום חיים! הסוכן האוטונומי שלך מוכן 🤖"

HELP_TEXT = (
    "📚 *פקודות זמינות:*\n\n"
    "/start — אתחול שיחה\n"
    "/scan — סריקת שוק אוטונומית\n"
    "/positions — פוזיציות פתוחות\n"
    "/gex — GEX נוכחי מ-MenthorQ\n"
    "/summary — סיכום יומי\n"
    "/learn טקסט — שמור העדפה חדשה\n"
    "/help — תפריט עזרה\n\n"
    "אפשר גם פשוט לכתוב שאלה חופשית בעברית והסוכן יענה."
)

_application: Optional[Application] = None
_polling_task: Optional[asyncio.Task] = None


def _agent() -> AutonomousAgent:
    return get_autonomous_agent()


def clean_response(text: str) -> str:
    """Strip JSON envelopes/code fences before sending to Telegram users."""
    if not text:
        return "מצטער, אירעה שגיאה. נסה שוב."
    text = re.sub(r"```json.*?```", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r'\{[^{}]*"tool"[^{}]*\}', "", text)
    text = re.sub(r'\{[^{}]*"action"[^{}]*\}', "", text)
    text = re.sub(r'\{[^{}]*"tool_input"[^{}]*\}', "", text)
    text = text.strip()
    if not text:
        text = "מצטער, אירעה שגיאה. נסה שוב."
    return text


async def _send(update: Update, text: str, parse: bool = True) -> None:
    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN if parse else None,
        disable_web_page_preview=True,
    )


# ───────────────────────── handlers ─────────────────────────

async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, WELCOME)


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, HELP_TEXT)


async def cmd_scan(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, "🔍 מתחיל סריקת שוק...", parse=False)
    try:
        response = await _agent().run_autonomous_task("סרוק שוק")
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_scan failed")
        await _send(update, f"⚠️ סריקה נכשלה: {exc}", parse=False)
        return
    await _send(update, clean_response(response or ""), parse=False)


async def cmd_positions(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db()
    cursor = db.positions.find({"status": "open"}).sort("entry_date", -1).limit(20)
    docs = await cursor.to_list(length=20)
    if not docs:
        await _send(update, "אין כרגע פוזיציות פתוחות.")
        return
    lines = [f"💼 *פוזיציות פתוחות:* {len(docs)}", ""]
    for d in docs:
        ticker = d.get("ticker", "—")
        strategy = d.get("strategy", "—")
        expiry = d.get("expiration_date")
        expiry_s = expiry.date().isoformat() if hasattr(expiry, "date") else (expiry or "—")
        premium = d.get("premium_received") or d.get("premium_paid") or 0
        lines.append(f"• `{ticker}` — {strategy} | תפוגה: {expiry_s} | פרמיה: ${premium}")
    await _send(update, "\n".join(lines))


async def cmd_gex(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, "⏳ מושך נתוני GEX...", parse=False)

    def _scrape() -> dict:
        with MenthorQScraper(headless=True) as scraper:
            return asdict(scraper.scrape_gex_data())

    try:
        menthorq = await asyncio.to_thread(_scrape)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_gex: MenthorQ failed")
        menthorq = {"note": str(exc)}

    try:
        calc = await asyncio.to_thread(GEXCalculator().get_key_levels, "SPY")
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_gex: calculator failed")
        calc = {"error": str(exc)}

    lines = [
        "⚡ *GEX נוכחי*",
        f"Regime: {menthorq.get('regime', '—')}",
        f"Spot: {menthorq.get('spot_price', '—')}",
        f"Call Wall: {menthorq.get('call_wall', '—')}",
        f"Put Wall: {menthorq.get('put_wall', '—')}",
        f"Gamma Flip: {menthorq.get('gamma_flip_level', '—')}",
        "",
        "*חישוב עצמי (SPY):*",
        f"Regime: {calc.get('regime', '—')} | Zero-DTE Safe: {calc.get('zero_dte_safe', '—')}",
        f"Call Resistance: {calc.get('call_resistance_levels', [])}",
        f"Put Support: {calc.get('put_support_levels', [])}",
    ]
    if menthorq.get("note"):
        lines.append(f"\n📝 {menthorq['note']}")
    await _send(update, "\n".join(lines))


async def cmd_summary(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db()
    today = datetime.now(EST).date().isoformat()
    journal = await db.journal.find_one({"date": today})

    open_positions = await db.positions.count_documents({"status": "open"})
    closed_positions = await db.positions.count_documents({"status": "closed"})

    daily_pnl = 0.0
    notes = ""
    if journal:
        daily_pnl = float(journal.get("daily_pnl") or 0.0)
        notes = journal.get("agent_summary") or journal.get("notes") or ""

    pnl_emoji = "🟢" if daily_pnl >= 0 else "🔴"
    lines = [
        f"📊 *סיכום יומי — {today}*",
        "",
        f"{pnl_emoji} P&L יומי: `${daily_pnl:,.2f}`",
        f"💼 פוזיציות פתוחות: `{open_positions}`",
        f"✅ פוזיציות סגורות: `{closed_positions}`",
    ]
    if notes:
        lines.extend(["", "📝 הערות:", notes])
    if not journal:
        lines.append("\n(עוד אין רשומת יומן להיום)")
    await _send(update, "\n".join(lines))


async def cmd_learn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    raw = " ".join(ctx.args or []).strip()
    if not raw:
        await _send(update, "שימוש: `/learn <תוכן>`")
        return
    try:
        doc_id = await asyncio.to_thread(LongTermMemory().save_user_fact, raw, "preference")
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_learn failed")
        await _send(update, f"⚠️ שמירה נכשלה: {exc}", parse=False)
        return
    await _send(update, f"✅ נשמר ({doc_id})", parse=False)


async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = (message.text or "").strip()
    chat_id = update.effective_chat.id
    session_id = str(chat_id)

    if not text:
        logger.info("Received empty message from chat %s – ignoring", chat_id)
        return

    logger.info("Received message from user (chat=%s): %s", chat_id, text)

    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is missing – cannot run agent")
        try:
            await message.reply_text("שגיאה: מפתח API חסר")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send API-key error reply")
        return

    try:
        logger.info("Sending to agent (session=%s)...", session_id)
        response = await _agent().run(text, session_id=session_id)
        logger.info("Agent response: %s", response)

        clean_text = clean_response(response or "")
        try:
            await message.reply_text(clean_text)
        except Exception:  # noqa: BLE001
            # Markdown / entity parse errors or oversize message – retry plain.
            logger.exception("reply_text failed, retrying without formatting")
            await message.reply_text(clean_text[:4000])
    except Exception:  # noqa: BLE001
        logger.exception("on_message: agent failed for chat %s", chat_id)
        try:
            await message.reply_text("מצטער, הייתה שגיאה. נסה שוב.")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send fallback error reply")


# ───────────────────────── lifecycle ─────────────────────────

def build_application() -> Application:
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is not set")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("gex", cmd_gex))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("learn", cmd_learn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


async def start_bot() -> None:
    global _application, _polling_task
    if _application is not None:
        return
    try:
        _application = build_application()
    except RuntimeError as exc:
        logger.warning("Telegram bot disabled: %s", exc)
        _application = None
        return

    await _application.initialize()
    await _application.start()
    await _application.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started")


async def stop_bot() -> None:
    global _application, _polling_task
    if _application is None:
        return
    try:
        if _application.updater and _application.updater.running:
            await _application.updater.stop()
        await _application.stop()
        await _application.shutdown()
        logger.info("Telegram bot stopped")
    finally:
        _application = None
        _polling_task = None
