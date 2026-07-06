"""LangGraph-compatible tool wrappers around all existing services.

Each tool is exposed both as a callable (for direct dispatch from `act_node`)
and registered in ``TOOL_REGISTRY`` so the LangGraph agent can look them up by
name. Tools return strings (or dicts that are stringified) so they can be
embedded back into the prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from analytics.gex_calculator import GEXCalculator
from analytics.iv_rank_calculator import IVRankCalculator
from db.connection import get_db
from memory.long_term import LongTermMemory
from scrapers.finviz_scraper import FinVizScraper
from services.scanner_service import ScannerService
from services.telegram_service import TelegramService, TelegramServiceError
from tools.fear_greed_tool import FearGreedTool
from tools.finnhub_tool import FinnhubTool
from tools.macro_tool import MacroTool
from tools.massive_tool import MassiveTool
from tools.perplexity_tool import PerplexityTool, PerplexityToolError
from tools.reddit_sentiment_tool import RedditSentimentTool
from tools.web_search_tool import WebSearchTool
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
EST = ZoneInfo("America/New_York")


# ───────────────────────── helpers ─────────────────────────

def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(_serialize(value), ensure_ascii=False, default=str, indent=2)


def _is_market_hours(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(EST)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


def _earnings_within_days(text: str, days: int = 14) -> bool:
    if not text:
        return False
    raw = text.strip().split(" - ")[0]
    for fmt in ("%b %d/%Y", "%b %d %Y", "%b %d", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.utcnow().year)
            delta_days = (dt - datetime.utcnow()).days
            return 0 <= delta_days <= days
        except ValueError:
            continue
    return False


# ───────────────────────── tool implementations ─────────────────────────

async def scan_market(_: Optional[dict] = None) -> str:
    """סורק את השוק ומוצא מניות מתאימות למסחר."""
    service = ScannerService()
    result = await service.run_morning_scan()
    return service.format_scan_result(result)


async def analyze_ticker(ticker: str) -> str:
    """מנתח מניה ספציפית לעומק כולל GEX וחדשות."""
    ticker = ticker.upper().strip()

    def _run() -> dict:
        with FinVizScraper(headless=True) as scraper:
            detail = scraper.scrape_ticker(ticker)
        try:
            gex = GEXCalculator().calculate_gex(ticker)
        except Exception:  # noqa: BLE001
            logger.exception("GEX failed for %s", ticker)
            gex = None
        return {
            "ticker": ticker,
            "detail": _serialize(detail),
            "gex": _serialize(gex) if gex else None,
        }

    payload = await asyncio.to_thread(_run)
    detail = payload["detail"] or {}
    gex = payload["gex"] or {}
    lines = [
        f"📊 ניתוח מניה: {ticker}",
        f"💰 מחיר: {detail.get('price', '—')}",
        f"📈 שינוי: {detail.get('change_pct', '—')}%",
        f"📐 P/E: {detail.get('pe', '—')} | EPS: {detail.get('eps', '—')}",
        f"🌪 IV: {detail.get('iv_pct', '—')} | RSI: {detail.get('rsi', '—')}",
        f"📅 Earnings: {detail.get('earnings_date', '—')}",
        f"🎯 Target: {detail.get('target_price', '—')} | Recom: {detail.get('recommendation', '—')}",
    ]
    if gex:
        lines.extend(
            [
                "",
                f"⚡ GEX Regime: {gex.get('regime', '—')}",
                f"🛡 Call Wall: {gex.get('call_wall', '—')} | Put Wall: {gex.get('put_wall', '—')}",
                f"🔄 Gamma Flip: {gex.get('gamma_flip_level', '—')}",
                f"📝 {gex.get('dealer_behavior', '')}",
            ]
        )
    news = detail.get("news") or []
    if news:
        lines.append("\n📰 חדשות אחרונות:")
        for item in news[:3]:
            lines.append(f"• [{item.get('time', '')}] {item.get('title', '')}")
    return "\n".join(lines)


async def get_gex_levels(ticker: Optional[str] = None) -> str:
    """מחזיר רמות GEX קריטיות לבחירת סטרייקים. ברירת מחדל: SPX."""
    requested = (ticker or "SPX").upper().strip() or "SPX"
    levels = await asyncio.to_thread(GEXCalculator().get_key_levels, requested)

    spot_price = float(levels.get("spot_price") or 0)
    gamma_flip = float(levels.get("gamma_flip") or 0)
    call_wall = float(levels.get("call_wall") or 0)
    put_wall = float(levels.get("put_wall") or 0)
    regime = levels.get("regime", "unknown")
    display = levels.get("ticker", requested)

    above_flip = gamma_flip > 0 and spot_price > gamma_flip
    regime_line = (
        "✅ מעל Gamma Flip – Long Gamma – שוק רגוע"
        if above_flip
        else "⚠️ מתחת Gamma Flip – Short Gamma – שוק תנודתי"
    )
    strategy_line = (
        "Iron Condor עם Strikes מעל Call Wall ומתחת Put Wall"
        if regime == "positive"
        else "Credit Spread בכיוון המגמה בלבד"
    )

    return (
        f"📊 {display} GEX Analysis:\n"
        f"💰 Spot: {spot_price:,.0f}\n"
        f"🔄 Gamma Flip: {gamma_flip:,.0f}\n"
        f"🟢 Call Wall: {call_wall:,.0f}\n"
        f"🔴 Put Wall: {put_wall:,.0f}\n"
        f"📈 משטר: {regime}\n"
        f"\n"
        f"{regime_line}\n"
        f"\n"
        f"🎯 אסטרטגיה מומלצת:\n"
        f"{strategy_line}"
    )


async def get_market_news(query: str) -> str:
    """שולף חדשות שוק עדכניות ומנתח השפעה."""
    try:
        tool = PerplexityTool()
    except PerplexityToolError as exc:
        return f"שירות החדשות לא זמין: {exc}"
    result = await asyncio.to_thread(tool.search, query)
    answer = result.get("answer", "")
    sources = result.get("sources", [])
    text = f"📰 חדשות: {query}\n\n{answer}"
    if sources:
        text += "\n\nמקורות:\n" + "\n".join(f"• {s}" for s in sources[:5])
    return text


async def get_open_positions(_: Optional[dict] = None) -> str:
    """מחזיר את כל הפוזיציות הפתוחות של חיים."""
    db = get_db()
    cursor = db.positions.find({"status": "open"}).sort("entry_date", -1)
    docs = await cursor.to_list(length=200)
    if not docs:
        return "אין פוזיציות פתוחות כרגע."
    lines = [f"💼 פוזיציות פתוחות: {len(docs)}", ""]
    for d in docs:
        ticker = d.get("ticker", "—")
        strategy = d.get("strategy", "—")
        expiry = d.get("expiration_date")
        expiry_s = expiry.date().isoformat() if hasattr(expiry, "date") else (expiry or "—")
        premium = d.get("premium_received") or d.get("premium_paid") or 0
        lines.append(f"• {ticker} – {strategy} | תפוגה: {expiry_s} | פרמיה: ${premium}")
    return "\n".join(lines)


async def save_position(position_data: dict) -> str:
    """שומר פוזיציה חדשה למסד הנתונים."""
    if not isinstance(position_data, dict) or not position_data.get("ticker"):
        return "❌ חסר ticker בנתוני הפוזיציה."
    db = get_db()
    now = datetime.utcnow()
    doc = dict(position_data)
    doc["ticker"] = str(doc["ticker"]).upper()
    doc.setdefault("status", "open")
    doc.setdefault("entry_date", now)
    doc["created_at"] = now
    doc["updated_at"] = now
    result = await db.positions.insert_one(doc)
    return f"✅ פוזיציה נשמרה (id={result.inserted_id})."


async def search_knowledge(query: str) -> str:
    """מחפש בספרייה המקצועית (חומרי NotebookLM: קורסים, מדריכים ותמלולים על GEX/DEX/Flow)."""
    def _run() -> list[dict]:
        from memory.trading_library import TradingLibrary

        return TradingLibrary().query(query, n_results=5)

    try:
        excerpts = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        logger.exception("search_knowledge failed")
        return f"החיפוש בספרייה נכשל: {exc}"

    if not excerpts:
        return "לא נמצא חומר רלוונטי בספרייה המקצועית לשאילתה הזו."

    lines = [f"📚 מהספרייה המקצועית – תוצאות עבור: {query}"]
    for item in excerpts:
        lines.append(
            f"\n[{item.get('notebook') or '—'} / {item.get('doc_title') or '—'}]\n"
            f"{(item.get('content') or '')[:900]}"
        )
    return "\n".join(lines)


async def save_memory(content: str, category: str = "lesson") -> str:
    """שומר לקח, העדפה או דפוס שוק לזיכרון ארוך הטווח (כשחיים מלמד משהו חדש)."""
    def _run() -> str:
        ltm = LongTermMemory()
        cat = (category or "lesson").lower().strip()
        if cat in ("preference", "behavior", "goal", "risk_tolerance"):
            ltm.save_user_fact(content, category=cat)
            return "user_profile"
        if cat in ("lesson", "trade_learning"):
            ltm.save_trade_learning(content, context={})
            return "trade_learnings"
        if cat in ("pattern", "market_pattern"):
            ltm.save_market_pattern(content, conditions={})
            return "market_patterns"
        if cat in ("strategy", "strategy_knowledge"):
            ltm.save_strategy_knowledge(content, context={})
            return "strategy_knowledge"
        ltm.save_knowledge(content, {"category": cat})
        return "knowledge_base"

    try:
        collection = await asyncio.to_thread(_run)
        return f"✅ נשמר בזיכרון ({collection}): {content[:120]}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("save_memory failed")
        return f"שמירת הזיכרון נכשלה: {exc}"


async def recall_memory(query: str) -> str:
    """שולף זיכרונות רלוונטיים על עסקאות ודפוסים קודמים."""
    def _run() -> dict:
        ltm = LongTermMemory()
        return ltm.recall_all(query, n_results=3)

    try:
        results = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        logger.exception("recall_memory failed")
        return f"לא ניתן לשלוף זיכרונות: {exc}"

    lines = [f"🧠 זיכרונות עבור: {query}"]
    found_any = False
    for collection, items in results.items():
        if not items:
            continue
        found_any = True
        lines.append(f"\n[{collection}]")
        for item in items[:3]:
            content = item.get("content") or item.get("document") or str(item)
            lines.append(f"• {content}")
    if not found_any:
        lines.append("(לא נמצאו זיכרונות רלוונטיים)")
    return "\n".join(lines)


async def send_telegram(message: str, urgency: str = "info") -> str:
    """שולח הודעה לחיים בטלגרם."""
    try:
        tg = TelegramService()
    except TelegramServiceError as exc:
        return f"טלגרם לא זמין: {exc}"
    ok = await tg.send_alert(title="🤖 הסוכן", body=message, urgency=urgency)
    return "✅ הודעה נשלחה" if ok else "❌ שליחה נכשלה"


async def calculate_strategy(
    ticker: str,
    strategy_type: str,
    gex_data: Optional[dict] = None,
) -> str:
    """מחשב פרמטרים מדויקים לאסטרטגיה מבוקשת."""
    ticker = ticker.upper().strip()
    strategy_type = (strategy_type or "iron_condor").lower()

    if gex_data is None:
        levels = await asyncio.to_thread(GEXCalculator().get_key_levels, ticker)
    else:
        levels = dict(gex_data)

    spot = float(levels.get("spot_price") or 0.0)
    call_wall = float(levels.get("call_wall") or 0.0)
    put_wall = float(levels.get("put_wall") or 0.0)
    gamma_flip = float(levels.get("gamma_flip") or 0.0)
    regime = levels.get("regime", "positive")

    is_zero_dte = "0dte" in strategy_type or "zero" in strategy_type
    delta_low, delta_high = (0.05, 0.10) if is_zero_dte else (0.10, 0.16)
    dte = 0 if is_zero_dte else 35

    if spot <= 0:
        return "❌ אין מחיר ספוט – לא ניתן לחשב סטרייקים."

    short_call = round(max(call_wall, spot * (1 + delta_high)), 2)
    short_put = round(min(put_wall, spot * (1 - delta_high)) if put_wall > 0 else spot * (1 - delta_high), 2)
    wing = round(max(spot * 0.01, 5.0), 2)
    long_call = round(short_call + wing, 2)
    long_put = round(short_put - wing, 2)

    if gamma_flip > 0 and (short_call < gamma_flip < long_call or short_put > gamma_flip > long_put):
        return (
            "⚠️ אחד הסטרייקים חוצה את Gamma Flip Level – אסטרטגיה לא מומלצת.\n"
            f"Gamma Flip: {gamma_flip} | Short Call: {short_call} | Short Put: {short_put}"
        )

    estimated_credit = round(wing * 0.20, 2)
    max_profit = estimated_credit * 100
    max_loss = round((wing - estimated_credit) * 100, 2)
    breakeven_up = round(short_call + estimated_credit, 2)
    breakeven_dn = round(short_put - estimated_credit, 2)

    lines = [
        f"📐 חישוב אסטרטגיה: {strategy_type} – {ticker}",
        f"💲 ספוט: {spot} | Regime: {regime} | Gamma Flip: {gamma_flip}",
        f"🛡 Call Wall: {call_wall} | Put Wall: {put_wall}",
        "",
        f"📈 Short Call: {short_call} | Long Call: {long_call}",
        f"📉 Short Put: {short_put} | Long Put: {long_put}",
        f"🎯 Delta target: {delta_low}-{delta_high}",
        f"📅 DTE: {dte} ({'0DTE' if is_zero_dte else '30-45 DTE'})",
        "",
        f"💰 Estimated Credit: ${estimated_credit}",
        f"📊 Max Profit: ${max_profit}",
        f"📉 Max Loss: ${max_loss}",
        f"⚖ Breakeven: {breakeven_dn} – {breakeven_up}",
    ]
    return "\n".join(lines)


async def check_trade_conditions(ticker: str, strategy_type: str = "iron_condor") -> str:
    """בודק אם תנאי השוק מתאימים לכניסה לעסקה."""
    ticker = ticker.upper().strip()

    def _fetch() -> dict:
        with FinVizScraper(headless=True) as scraper:
            detail = scraper.scrape_ticker(ticker)
        gex = None
        try:
            gex = GEXCalculator().calculate_gex(ticker)
        except Exception:  # noqa: BLE001
            logger.exception("GEX failed for %s", ticker)
        vix_detail = None
        try:
            with FinVizScraper(headless=True) as scraper:
                vix_detail = scraper.scrape_ticker("VIX")
        except Exception:  # noqa: BLE001
            logger.exception("VIX fetch failed")
        return {
            "detail": _serialize(detail),
            "gex": _serialize(gex) if gex else None,
            "vix": _serialize(vix_detail) if vix_detail else None,
        }

    payload = await asyncio.to_thread(_fetch)
    detail = payload["detail"] or {}
    gex = payload["gex"] or {}
    vix_detail = payload["vix"] or {}

    vix_value = vix_detail.get("price")
    iv_value: Optional[float] = None
    iv_raw = detail.get("iv_pct") or ""
    for part in iv_raw.replace(",", " ").split():
        try:
            iv_value = float(part.replace("%", ""))
            break
        except ValueError:
            continue

    earnings_soon = _earnings_within_days(detail.get("earnings_date", ""), days=14)
    market_open = _is_market_hours()
    gex_regime = gex.get("regime", "unknown")

    try:
        memories = await asyncio.to_thread(
            LongTermMemory().recall, f"{ticker} {strategy_type}", "trade_learnings", 3
        )
    except Exception:  # noqa: BLE001
        memories = []

    conditions = {
        "market_open": market_open,
        "gex_regime": gex_regime,
        "earnings_within_14d": earnings_soon,
        "vix": vix_value,
        "iv_pct": iv_value,
    }

    issues: list[str] = []
    if not market_open:
        issues.append("השוק סגור")
    if earnings_soon:
        issues.append("יש Earnings בתוך 14 ימים")
    if gex_regime == "negative" and "iron" in strategy_type.lower():
        issues.append("GEX שלילי – לא מתאים ל-Iron Condor")
    if isinstance(vix_value, (int, float)) and vix_value > 30:
        issues.append(f"VIX גבוה ({vix_value})")

    if issues:
        risk_level = "גבוה"
        reason = "; ".join(issues)
        approved = False
    elif gex_regime == "positive" and (iv_value or 0) > 30:
        risk_level = "נמוך"
        reason = "GEX חיובי, IV סביר, אין Earnings קרוב"
        approved = True
    else:
        risk_level = "בינוני"
        reason = "תנאים בסיסיים תקינים, מומלץ אישור ידני"
        approved = True

    result = {
        "approved": approved,
        "reason": reason,
        "risk_level": risk_level,
        "conditions": conditions,
        "memories_count": len(memories),
    }
    return _to_text(result)


async def scan_iv_opportunities(
    min_iv_rank: float = 50,
    min_iv_percentile: Optional[float] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_volume: Optional[int] = None,
    limit: int = 15,
    **kwargs: Any,
) -> str:
    """סורק מניות עם IV גבוה להזדמנויות מסחר. משלב חיפוש אינטרנט וחישוב IV Rank מדויק."""
    # Claude may send "min_iv_percentile" – treat it as IV-rank threshold.
    if min_iv_percentile is not None:
        min_iv_rank = min_iv_percentile

    try:
        min_value = float(min_iv_rank)
    except (TypeError, ValueError):
        min_value = 50.0

    try:
        limit_value = max(1, int(limit))
    except (TypeError, ValueError):
        limit_value = 15

    if kwargs:
        logger.info("scan_iv_opportunities: ignoring extra params %s", list(kwargs))

    # 1) Pull current high-IV candidates from the web
    web_text = "(לא זמין כרגע)"
    try:
        web_text = await asyncio.to_thread(WebSearchTool().find_high_iv_stocks)
    except Exception:  # noqa: BLE001
        logger.exception("WebSearchTool.find_high_iv_stocks failed")

    # 2) Calculate IV Rank for the SP500_TOP_50 watchlist and keep the strongest
    calc = IVRankCalculator()
    payload = await asyncio.to_thread(calc.get_sp500_iv_opportunities)
    sells = [r for r in payload.get("sell_opportunities", []) if r.iv_rank >= min_value]
    golden = payload.get("golden_opportunities", [])
    buys = payload.get("buy_opportunities", [])

    # 3) Format combined message
    top_calc = (golden + sells)[:limit_value] if (golden or sells) else []
    lines = ["🔍 מניות עם IV גבוה עכשיו:", ""]
    lines.append("📊 מתוצאות חיפוש אינטרנט:")
    lines.append(web_text)
    lines.append("")
    lines.append("📈 חישוב IV Rank מדויק:")
    if not top_calc:
        lines.append("(לא נמצאו מניות מעל סף ה-IV Rank שנקבע)")
    else:
        for i, r in enumerate(top_calc, start=1):
            lines.append(
                f"{i}. {r.ticker} – IV Rank: {r.iv_rank}"
            )
            lines.append(
                f"   📊 IV: {r.current_iv}% | אות: {r.signal} ({r.signal_strength})"
            )
            if r.recommended_strategies:
                lines.append(
                    f"   ✅ {', '.join(r.recommended_strategies)}"
                )
    if buys:
        lines.append("")
        lines.append("🛒 הזדמנויות קנייה (IV Rank < 25):")
        for r in buys[:5]:
            lines.append(f"• {r.ticker} – IV Rank: {r.iv_rank}")
    return "\n".join(lines).rstrip()


async def web_search(query: str) -> str:
    """מחפש מידע עדכני באינטרנט על שוק המניות."""
    return await asyncio.to_thread(WebSearchTool().search_best, query)


async def market_overview(_: Optional[dict] = None) -> str:
    """מקבל סקירת שוק עדכנית מהאינטרנט."""
    return await asyncio.to_thread(WebSearchTool().get_market_overview)


async def research_stock(ticker: str) -> str:
    """חוקר מניה ספציפית באינטרנט."""
    return await asyncio.to_thread(WebSearchTool().research_ticker, ticker)


# ───────────────────────── Finnhub tools ─────────────────────────

async def check_earnings(ticker: str) -> str:
    """בודק אם יש earnings קרוב למניה ומעריך את רמת הסיכון לפוזיציה."""
    ticker = ticker.upper().strip()
    data = await asyncio.to_thread(FinnhubTool().check_earnings_risk, ticker)
    if not data:
        return f"❌ לא ניתן לבדוק Earnings ל-{ticker} כרגע."
    if not data.get("has_earnings"):
        return (
            f"📅 {ticker} – אין דוחות בקרוב\n"
            f"רמת סיכון: {data.get('risk_level', 'נמוך ✅')}\n"
            f"{data.get('message', '')}"
        )
    return (
        f"📅 {ticker} – דוחות קרובים\n"
        f"תאריך: {data.get('date', '—')}\n"
        f"ימים עד הדוח: {data.get('days_until', '—')}\n"
        f"רמת סיכון: {data.get('risk_level', '—')}\n"
        f"{data.get('message', '')}"
    )


async def get_analyst_recommendations(ticker: str) -> str:
    """מחזיר המלצות אנליסטים למניה."""
    ticker = ticker.upper().strip()
    data = await asyncio.to_thread(FinnhubTool().get_recommendation_trends, ticker)
    if not data:
        return f"❌ אין המלצות אנליסטים זמינות ל-{ticker}."
    buy = data.get("strong_buy", 0) + data.get("buy", 0)
    sell = data.get("strong_sell", 0) + data.get("sell", 0)
    return (
        f"👥 המלצות אנליסטים – {ticker}\n"
        f"תקופה: {data.get('period', '—')}\n"
        f"🟢 קנייה (Strong Buy + Buy): {buy}\n"
        f"🟡 המתנה: {data.get('hold', 0)}\n"
        f"🔴 מכירה (Sell + Strong Sell): {sell}\n"
        f"סה\"כ אנליסטים: {data.get('total_analysts', 0)}\n"
        f"קונצנזוס: {data.get('consensus', '—')}"
    )


async def get_stock_news(ticker: str) -> str:
    """שולף חדשות אחרונות על מניה ספציפית."""
    ticker = ticker.upper().strip()
    items = await asyncio.to_thread(FinnhubTool().get_company_news, ticker, 3)
    if not items:
        return f"📰 לא נמצאו חדשות אחרונות עבור {ticker}."
    lines = [f"📰 חדשות אחרונות – {ticker}"]
    for n in items:
        when = n.get("datetime") or ""
        head = n.get("headline") or ""
        source = n.get("source") or ""
        lines.append(f"• [{when}] {head} ({source})")
        summary = (n.get("summary") or "").strip()
        if summary:
            lines.append(f"   {summary[:200]}")
    return "\n".join(lines)


async def get_earnings_calendar(_: Optional[dict] = None) -> str:
    """מציג את לוח הדוחות הקרובים לשבועיים הקרובים."""
    items = await asyncio.to_thread(FinnhubTool().get_earnings_calendar, 14)
    if not items:
        return "📅 לא נמצאו דוחות מתוכננים ב-14 הימים הקרובים."
    lines = ["📅 לוח Earnings – שבועיים קרובים:"]
    for e in items:
        eps = e.get("eps_estimate")
        eps_text = f"EPS צפי: {eps}" if eps is not None else "EPS צפי: —"
        lines.append(f"• {e.get('date', '—')} | {e.get('ticker', '—')} | {eps_text}")
    return "\n".join(lines)


async def full_ticker_analysis(ticker: str) -> str:
    """ניתוח מלא של מניה כולל מחיר, המלצות, חדשות וסיכון Earnings."""
    ticker = ticker.upper().strip()
    return await asyncio.to_thread(FinnhubTool().get_full_ticker_analysis, ticker)


# ───────────────────────── macro / sentiment tools ─────────────────────────

async def get_fear_greed(_: Optional[dict] = None) -> str:
    """מחזיר מדד הפחד והחמדנות של השוק."""
    data = await asyncio.to_thread(FearGreedTool().get_fear_greed_index)
    if not data:
        return "😶 לא ניתן לשלוף את מדד הפחד והחמדנות כרגע."
    return (
        "📊 Fear & Greed Index\n"
        f"ציון: {data.get('score', '—')}/100\n"
        f"מצב: {data.get('hebrew_rating', '—')} ({data.get('rating', '—')})\n"
        f"💡 משמעות: {data.get('strategy_implication', '—')}"
    )


async def get_macro_overview(_: Optional[dict] = None) -> str:
    """מצב מאקרו כלכלי – VIX, דולר, זהב, נפט."""
    return await asyncio.to_thread(MacroTool().get_market_summary)


async def get_econ_calendar(_: Optional[dict] = None) -> str:
    """אירועים כלכליים השבוע שישפיעו על השוק."""
    events = await asyncio.to_thread(MacroTool().get_economic_calendar)
    if not events:
        return "📅 לא נמצאו אירועים כלכליים בולטים השבוע."
    lines = ["📅 אירועים כלכליים השבוע:"]
    for ev in events:
        actual = ev.get("actual")
        estimate = ev.get("estimate")
        prev = ev.get("previous")
        lines.append(
            f"• {ev.get('date', '—')} | {ev.get('event', '—')} | השפעה: {ev.get('impact', '—')}\n"
            f"   צפי: {estimate if estimate is not None else '—'} | "
            f"בפועל: {actual if actual is not None else '—'} | "
            f"קודם: {prev if prev is not None else '—'}"
        )
    return "\n".join(lines)


async def get_unusual_options(min_volume: int = 1000) -> str:
    """מחזיר פעילות אופציות חריגה - איפה הכסף הגדול נכנס היום."""
    massive = MassiveTool()
    try:
        trades = await massive.get_unusual_options_activity(min_volume=int(min_volume))
    finally:
        await massive.close()
    if not trades:
        return "🤷 לא נמצאה פעילות אופציות חריגה כרגע."
    lines = ["🚨 פעילות אופציות חריגה (Top 10):"]
    for i, t in enumerate(trades[:10], start=1):
        opt_type = t.get("type", "")
        emoji = "🟢" if opt_type == "call" else "🔴" if opt_type == "put" else "⚪"
        premium = float(t.get("premium", 0) or 0)
        size = int(t.get("size", 0) or 0)
        lines.append(
            f"{i}. {emoji} ${t.get('ticker', '?')} – {opt_type.upper()} ${t.get('strike', '?')} "
            f"פקיעה {t.get('expiration', '?')}\n"
            f"   🔢 גודל: {size:,} | 💰 פרמיה: ${premium:,.0f}"
        )
    return "\n".join(lines)


async def get_options_flow(ticker: str) -> str:
    """מחזיר תזרים אופציות עם Sweeps ו-Blocks בזמן אמת (OptionsFlowEngine)."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return "❌ חסר ticker."
    from analytics.flow_engine import OptionsFlowEngine

    data = await OptionsFlowEngine().analyze_ticker_flow(ticker)
    if not data:
        return f"📊 אין נתוני תזרים אופציות עבור {ticker} כרגע."

    sentiment_he = {
        "bullish": "🟢 שורי",
        "bearish": "🔴 דובי",
        "neutral": "🟡 ניטרלי",
    }.get(data.get("overall_sentiment", "neutral"), "🟡 ניטרלי")

    lines = [
        f"📊 תזרים אופציות – {ticker}",
        f"סנטימנט: {sentiment_he}",
        f"🟢 שורי: {data.get('bull_pct', 0):.0f}% ({data.get('bullish_trades', 0)} עסקאות)",
        f"🔴 דובי: {data.get('bear_pct', 0):.0f}% ({data.get('bearish_trades', 0)} עסקאות)",
    ]
    sweeps = data.get("sweeps") or []
    if sweeps:
        lines.append("\n⚡ Sweeps עיקריים:")
        for s in sweeps[:3]:
            arrow = "🟢" if s.get("sentiment") == "bullish" else "🔴"
            premium = s.get("premium") or 0
            lines.append(
                f"{arrow} ${s.get('strike', '?')} exp {s.get('expiry', '?')} – ${premium:,}"
            )
    blocks = data.get("blocks") or []
    if blocks:
        lines.append("\n🏦 Blocks (Smart Money):")
        for b in blocks[:3]:
            arrow = "🟢" if b.get("sentiment") == "bullish" else "🔴"
            premium = b.get("premium") or 0
            lines.append(
                f"{arrow} ${b.get('strike', '?')} exp {b.get('expiry', '?')} – ${premium:,}"
            )
    return "\n".join(lines)


async def get_gex_analysis(ticker: str = "SPY") -> str:
    """ניתוח GEX מלא: Call Wall, Put Wall, Gamma Flip, משטר, פרופיל ואסטרטגיה."""
    ticker = (ticker or "SPY").upper().strip()
    try:
        from analytics.gex_engine import GEXEngine

        data = await GEXEngine().get_full_gex_analysis(ticker)
    except Exception as exc:  # noqa: BLE001
        import traceback
        logger.error("Tool GEX error:\n%s", traceback.format_exc())
        return (
            f"שגיאה טכנית בניתוח GEX ל-{ticker}.\n"
            "נסה שוב או השתמש ב-SPY."
        )

    if "error" in data:
        return (
            f"לא ניתן לקבל נתוני GEX ל-{ticker}.\n"
            f"נסה: SPY (במקום SPX) או QQQ.\n"
            f"שגיאה: {data['error']}"
        )

    body = data.get("analysis_hebrew") or _to_text(data)
    if data.get("warning") == "estimated_levels":
        return body + "\n\n⚠️ שים לב: נתונים משוערים בלבד"
    return body


async def get_market_structure(ticker: str = "SPY") -> str:
    """ניתוח מבנה שוק מלא: GEX + Flow + אסטרטגיה."""
    ticker = (ticker or "SPY").upper().strip()
    from analytics.market_structure import MarketStructureAnalyzer

    data = await MarketStructureAnalyzer().get_daily_report(ticker)
    return data.get("report_hebrew") or _to_text(data)


async def get_strategy_recommendation(
    ticker: str = "SPY",
    gex_data: Optional[dict] = None,
    flow_data: Optional[dict] = None,
) -> str:
    """המלצת אסטרטגיה מדויקת לפי GEX+Flow עם Strikes ספציפיים."""
    ticker = (ticker or "SPY").upper().strip()
    from analytics.strategy_selector import StrategySelector

    rec = await StrategySelector().select_strategy(
        ticker=ticker, gex_data=gex_data, flow_data=flow_data
    )

    confidence = rec.get("confidence") or 0
    emoji = "🟢🟢" if confidence > 0.90 else "🟢" if confidence > 0.80 else "🟡"

    lines = [
        f"{emoji} המלצה ל-{ticker} – {str(rec.get('strategy', '?')).upper()}",
        f"📋 {rec.get('recommendation', '')}",
        f"💭 {rec.get('reasoning', '')}",
        f"📊 ביטחון: {confidence * 100:.0f}%",
        "",
        f"GEX Regime: {rec.get('gex_regime', '—')} | "
        f"Profile: {rec.get('gex_profile', '—')} | "
        f"Flow: {rec.get('flow_sentiment', '—')}",
        f"IV Rank: {rec.get('iv_rank', '—')} | VIX: {rec.get('vix', '—')}",
    ]

    strikes = rec.get("strikes") or {}
    if strikes:
        lines.append("\n🎯 Strikes:")
        for key, val in strikes.items():
            if key in {"type", "width", "call_width", "put_width"}:
                continue
            lines.append(f"   {key}: ${val}")

    greeks = rec.get("greeks") or {}
    if greeks:
        lines.append("\n🔬 Greeks (אומדן):")
        lines.append(
            f"   Δ={greeks.get('delta', 0):.2f} | "
            f"Θ/יום={greeks.get('theta_per_day', 0):.2f} | "
            f"V={greeks.get('vega', 0):.2f} | "
            f"prob={greeks.get('probability', 0) * 100:.0f}%"
        )

    rr = rec.get("risk_reward") or {}
    if rr:
        lines.append("\n💰 סיכון/סיכוי:")
        for k, v in rr.items():
            lines.append(f"   {k}: {v}")

    actions = rec.get("action_items") or []
    if actions:
        lines.append("\n✅ צעדים:")
        for action in actions:
            lines.append(f"   • {action}")

    return "\n".join(lines)


async def check_wall_status(ticker: str = "SPY") -> str:
    """בודק אם ה-Walls נשמרים או נשברו לאחרונה."""
    ticker = (ticker or "SPY").upper().strip()
    from analytics.gex_engine import GEXEngine

    gex = await GEXEngine().get_full_gex_analysis(ticker)
    if "error" in gex:
        return f"❌ {ticker}: {gex['error']}"

    spot = gex.get("spot_price")
    cw = gex.get("call_wall")
    pw = gex.get("put_wall")
    gf = gex.get("gamma_flip")
    cw_dist = gex.get("call_wall_distance_pct")
    pw_dist = gex.get("put_wall_distance_pct")

    status_lines: list[str] = [f"🧱 סטטוס Walls – {ticker}", f"💰 ספוט: ${spot:,.2f}"]
    if cw is not None:
        broken = isinstance(spot, (int, float)) and isinstance(cw, (int, float)) and spot >= cw
        marker = "🚨 נשבר!" if broken else "✅ מחזיק"
        status_lines.append(f"🟢 Call Wall ${cw:,.0f} ({cw_dist:+.2f}%) – {marker}")
    if pw is not None:
        broken = isinstance(spot, (int, float)) and isinstance(pw, (int, float)) and spot <= pw
        marker = "🚨 נשבר!" if broken else "✅ מחזיק"
        status_lines.append(f"🔴 Put Wall ${pw:,.0f} (-{pw_dist:.2f}%) – {marker}")
    if gf is not None:
        side = "מעל" if isinstance(spot, (int, float)) and spot > gf else "מתחת"
        status_lines.append(f"⚡ Gamma Flip: ${gf:,.0f} (המחיר {side})")

    db = get_db()
    try:
        cutoff = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        recent = await db.wall_breaks.find(
            {"ticker": ticker, "timestamp": {"$gte": cutoff}}
        ).sort("timestamp", -1).to_list(length=5)
    except Exception:  # noqa: BLE001
        logger.exception("check_wall_status: db query failed")
        recent = []

    if recent:
        status_lines.append("\n📜 שבירות היום:")
        for r in recent:
            ts = r.get("timestamp")
            ts_text = ts.strftime("%H:%M") if hasattr(ts, "strftime") else "—"
            status_lines.append(f"• [{ts_text}] {r.get('type', '?')} @ ${r.get('spot', 0):,.2f}")
    else:
        status_lines.append("\n📜 לא נרשמו שבירות היום.")

    return "\n".join(status_lines)


async def get_real_greeks(option_symbol: str) -> str:
    """מחזיר Greeks מדויקים לאופציה ספציפית (Massive)."""
    symbol = (option_symbol or "").strip()
    if not symbol:
        return "❌ חסר option_symbol."
    massive = MassiveTool()
    try:
        data = await massive.get_option_quote(symbol)
    finally:
        await massive.close()
    if not data or data.get("last") is None:
        return f"❌ אין נתוני Greeks עבור {symbol}."

    def _fmt(value, suffix: str = "") -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):.4f}{suffix}"
        except (TypeError, ValueError):
            return str(value)

    iv = data.get("iv")
    iv_pct = f"{float(iv) * 100:.2f}%" if isinstance(iv, (int, float)) else "—"
    return (
        f"🔬 Greeks – {symbol}\n"
        f"💰 Bid/Ask/Last: {data.get('bid', '—')} / {data.get('ask', '—')} / {data.get('last', '—')}\n"
        f"📊 Volume: {data.get('volume', '—')} | OI: {data.get('open_interest', '—')}\n"
        f"🌪 IV: {iv_pct}\n"
        f"📈 Delta: {_fmt(data.get('delta'))} (כיוון מול ספוט)\n"
        f"📐 Gamma: {_fmt(data.get('gamma'))} (קצב שינוי בדלתא)\n"
        f"⏳ Theta: {_fmt(data.get('theta'))} (דעיכה זמנית ליום)\n"
        f"🌬 Vega: {_fmt(data.get('vega'))} (רגישות ל-IV)\n"
        f"🏦 Rho: {_fmt(data.get('rho'))} (רגישות לריבית)"
    )


async def recommend_stocks(_: Optional[dict] = None) -> str:
    """ממליץ על מניות איכותיות במגמה עולה לפי הסריקה היומית."""
    db = get_db()
    start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    cursor = (
        db.stock_analyses.find(
            {
                "analysis_date": {"$gte": start_of_day},
                "recommendation": {"$in": ["strong_buy", "buy"]},
            }
        )
        .sort("quality_score", -1)
        .limit(5)
    )
    docs = await cursor.to_list(length=5)
    if not docs:
        return "🤷 אין כרגע מניות מומלצות מהסריקה היומית. נסה אחרי 09:30 EST."

    lines = ["🏆 מניות מומלצות (סריקה יומית):"]
    for i, doc in enumerate(docs, start=1):
        catalysts = ", ".join(doc.get("catalysts") or []) or "אין"
        iv_rank = doc.get("iv_rank")
        iv_text = f"{iv_rank:.0f}" if isinstance(iv_rank, (int, float)) else "—"
        lines.append(
            f"{i}. {doc.get('ticker', '?')} – ציון {doc.get('quality_score', 0)}/100 "
            f"| {doc.get('recommendation', '?')}\n"
            f"   💰 ${doc.get('price', 0):.2f} ({doc.get('change_pct', 0):+.2f}%) | "
            f"IV Rank: {iv_text}\n"
            f"   📊 מגמה: {doc.get('trend', '—')} | קטליסטים: {catalysts}\n"
            f"   💡 אסטרטגיה: {doc.get('suggested_strategy') or '—'}\n"
            f"   📝 {doc.get('recommendation_reason') or '—'}"
        )
    return "\n\n".join(lines)


async def get_learned_patterns(_: Optional[dict] = None) -> str:
    """מציג דפוסים שהסוכן למד מהתחזיות הקודמות."""
    def _recall() -> dict:
        ltm = LongTermMemory()
        return ltm.recall("דפוס מנצח", "market_patterns", n_results=5)

    try:
        results = await asyncio.to_thread(_recall)
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_learned_patterns failed")
        return f"❌ לא ניתן לשלוף דפוסים: {exc}"

    if not results:
        return "📚 אין דפוסים שמורים עדיין – הסוכן עוד לומד."
    lines = ["🎓 דפוסים שנלמדו מתחזיות עבר:"]
    for idx, item in enumerate(results[:5], start=1):
        content = item.get("content") or item.get("document") or str(item)
        lines.append(f"\n{idx}. {content}")
    return "\n".join(lines)


async def get_reddit_sentiment(_: Optional[dict] = None) -> str:
    """מניות טרנדיות ברדיט וסנטימנט."""
    trending = await asyncio.to_thread(RedditSentimentTool().get_trending_tickers)
    if not trending:
        return "💬 לא נמצאו מניות טרנדיות ברדיט כרגע."
    lines = ["💬 מניות טרנדיות ברדיט:"]
    for item in trending:
        emoji = (
            "🟢" if item["sentiment"] == "bullish"
            else "🔴" if item["sentiment"] == "bearish"
            else "🟡"
        )
        lines.append(
            f"• {item['ticker']} – {item['mentions']} אזכורים {emoji} ({item['sentiment']})"
        )
    return "\n".join(lines)


async def get_iv_rank(ticker: str) -> str:
    """מחשב IV Rank למניה ספציפית ומסביר מה כדאי לעשות."""
    ticker = ticker.upper().strip()
    result = await asyncio.to_thread(IVRankCalculator().calculate_iv_rank, ticker)
    if result is None:
        return f"❌ לא ניתן לחשב IV Rank ל-{ticker} כרגע (אין נתוני אופציות או היסטוריה)."
    lines = [
        f"📊 IV Rank – {ticker}",
        f"📈 IV נוכחי: {result.current_iv}%",
        f"📉 טווח שנתי: {result.iv_52w_low}% – {result.iv_52w_high}%",
        f"🎯 IV Rank: {result.iv_rank} | Percentile: {result.iv_percentile}",
        f"📡 Signal: {result.signal} ({result.signal_strength})",
        f"💡 {result.explanation}",
        f"✅ אסטרטגיות מומלצות: {', '.join(result.recommended_strategies) or '—'}",
    ]
    if result.note:
        lines.append(f"📝 {result.note}")
    return "\n".join(lines)


# ───────────────────────── registry ─────────────────────────

class AgentTool:
    def __init__(self, name: str, description: str, func: Callable[..., Any]) -> None:
        self.name = name
        self.description = description
        self.func = func

    async def run(self, payload: Any = None) -> str:
        try:
            if asyncio.iscoroutinefunction(self.func):
                result = await self._call_async(payload)
            else:
                result = await asyncio.to_thread(self._call_sync, payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tool %s failed", self.name)
            return f"❌ כשל בכלי {self.name}: {exc}"
        return _to_text(result)

    async def _call_async(self, payload: Any) -> Any:
        if payload is None:
            return await self.func()
        if isinstance(payload, dict):
            return await self.func(**payload)
        return await self.func(payload)

    def _call_sync(self, payload: Any) -> Any:
        if payload is None:
            return self.func()
        if isinstance(payload, dict):
            return self.func(**payload)
        return self.func(payload)


TOOL_REGISTRY: dict[str, AgentTool] = {
    "scan_market": AgentTool(
        "scan_market", "סורק את השוק ומוצא מניות מתאימות למסחר", scan_market
    ),
    "analyze_ticker": AgentTool(
        "analyze_ticker", "מנתח מניה ספציפית לעומק כולל GEX וחדשות", analyze_ticker
    ),
    "get_gex_levels": AgentTool(
        "get_gex_levels", "מחזיר רמות GEX קריטיות לבחירת סטרייקים", get_gex_levels
    ),
    "get_market_news": AgentTool(
        "get_market_news", "שולף חדשות שוק עדכניות ומנתח השפעה", get_market_news
    ),
    "get_open_positions": AgentTool(
        "get_open_positions", "מחזיר את כל הפוזיציות הפתוחות של חיים", get_open_positions
    ),
    "save_position": AgentTool(
        "save_position", "שומר פוזיציה חדשה למסד הנתונים", save_position
    ),
    "recall_memory": AgentTool(
        "recall_memory", "שולף זיכרונות רלוונטיים על עסקאות ודפוסים קודמים", recall_memory
    ),
    "search_knowledge": AgentTool(
        "search_knowledge",
        "מחפש בספרייה המקצועית של חיים (קורסים ומדריכים על GEX/DEX/Order Flow מ-NotebookLM). "
        "השתמש כשצריך עומק תיאורטי, כללים או שיטות מהחומר שחיים לומד ממנו",
        search_knowledge,
    ),
    "save_memory": AgentTool(
        "save_memory",
        "שומר לזיכרון ארוך-טווח לקח/העדפה/דפוס. קטגוריות: preference, lesson, pattern, strategy. "
        "השתמש כשחיים מלמד אותך משהו או כשמתגלה תובנה ששווה לזכור",
        save_memory,
    ),
    "send_telegram": AgentTool(
        "send_telegram", "שולח הודעה לחיים בטלגרם", send_telegram
    ),
    "calculate_strategy": AgentTool(
        "calculate_strategy", "מחשב פרמטרים מדויקים לאסטרטגיה מבוקשת", calculate_strategy
    ),
    "check_trade_conditions": AgentTool(
        "check_trade_conditions",
        "בודק אם תנאי השוק מתאימים לכניסה לעסקה",
        check_trade_conditions,
    ),
    "scan_iv_opportunities": AgentTool(
        "scan_iv_opportunities",
        "סורק מניות עם IV גבוה להזדמנויות מסחר",
        scan_iv_opportunities,
    ),
    "get_iv_rank": AgentTool(
        "get_iv_rank",
        "מחשב IV Rank למניה ספציפית ומסביר מה כדאי לעשות",
        get_iv_rank,
    ),
    "web_search": AgentTool(
        "web_search",
        "מחפש מידע עדכני באינטרנט על שוק המניות",
        web_search,
    ),
    "market_overview": AgentTool(
        "market_overview",
        "מקבל סקירת שוק עדכנית מהאינטרנט",
        market_overview,
    ),
    "research_stock": AgentTool(
        "research_stock",
        "חוקר מניה ספציפית באינטרנט",
        research_stock,
    ),
    "check_earnings": AgentTool(
        "check_earnings",
        "בודק אם יש earnings קרוב למניה ומעריך את רמת הסיכון לפוזיציה",
        check_earnings,
    ),
    "get_analyst_recommendations": AgentTool(
        "get_analyst_recommendations",
        "מחזיר המלצות אנליסטים למניה",
        get_analyst_recommendations,
    ),
    "get_stock_news": AgentTool(
        "get_stock_news",
        "שולף חדשות אחרונות על מניה ספציפית",
        get_stock_news,
    ),
    "get_earnings_calendar": AgentTool(
        "get_earnings_calendar",
        "מציג את לוח הדוחות הקרובים לשבועיים הקרובים",
        get_earnings_calendar,
    ),
    "full_ticker_analysis": AgentTool(
        "full_ticker_analysis",
        "ניתוח מלא של מניה כולל מחיר, המלצות, חדשות וסיכון Earnings",
        full_ticker_analysis,
    ),
    "get_fear_greed": AgentTool(
        "get_fear_greed",
        "מחזיר מדד הפחד והחמדנות של השוק",
        get_fear_greed,
    ),
    "get_macro_overview": AgentTool(
        "get_macro_overview",
        "מצב מאקרו כלכלי – VIX, דולר, זהב, נפט",
        get_macro_overview,
    ),
    "get_reddit_sentiment": AgentTool(
        "get_reddit_sentiment",
        "מניות טרנדיות ברדיט וסנטימנט",
        get_reddit_sentiment,
    ),
    "get_econ_calendar": AgentTool(
        "get_econ_calendar",
        "אירועים כלכליים השבוע שישפיעו על השוק",
        get_econ_calendar,
    ),
    "recommend_stocks": AgentTool(
        "recommend_stocks",
        "ממליץ על מניות איכותיות במגמה עולה לפי הסריקה היומית",
        recommend_stocks,
    ),
    "get_learned_patterns": AgentTool(
        "get_learned_patterns",
        "מציג דפוסים שהסוכן למד מהתחזיות הקודמות",
        get_learned_patterns,
    ),
    "get_unusual_options": AgentTool(
        "get_unusual_options",
        "מחזיר פעילות אופציות חריגה - איפה הכסף הגדול נכנס היום",
        get_unusual_options,
    ),
    "get_options_flow": AgentTool(
        "get_options_flow",
        "מחזיר תזרים אופציות עם Sweeps ו-Blocks בזמן אמת",
        get_options_flow,
    ),
    "get_gex_analysis": AgentTool(
        "get_gex_analysis",
        "מחזיר ניתוח GEX מלא: Call Wall, Put Wall, Gamma Flip, משטר, פרופיל ואסטרטגיה",
        get_gex_analysis,
    ),
    "get_market_structure": AgentTool(
        "get_market_structure",
        "ניתוח מבנה שוק מלא: GEX + Flow + אסטרטגיה",
        get_market_structure,
    ),
    "check_wall_status": AgentTool(
        "check_wall_status",
        "בודק אם ה-Walls נשמרים או נשברו לאחרונה",
        check_wall_status,
    ),
    "get_strategy_recommendation": AgentTool(
        "get_strategy_recommendation",
        "מחזיר המלצת אסטרטגיה מדויקת לפי GEX+Flow עם Strikes ספציפיים",
        get_strategy_recommendation,
    ),
    "get_real_greeks": AgentTool(
        "get_real_greeks",
        "מחזיר Greeks מדויקים לאופציה ספציפית",
        get_real_greeks,
    ),
}


def list_tool_descriptions() -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in TOOL_REGISTRY.values())


# ─────────────────── Anthropic native tool-use schemas ───────────────────

def _json_type_for(annotation: Any) -> str:
    """Map a Python annotation to a JSON-schema type (best effort)."""
    text = str(annotation)
    if annotation in (int,) or text in ("<class 'int'>",):
        return "integer"
    if annotation in (float,) or "float" in text:
        return "number"
    if annotation in (bool,) or "bool" in text:
        return "boolean"
    if annotation in (dict,) or "dict" in text.lower():
        return "object"
    return "string"


def _schema_from_func(func: Callable[..., Any]) -> dict[str, Any]:
    """Derive a permissive JSON schema from a tool function's signature."""
    import inspect

    props: dict[str, Any] = {}
    required: list[str] = []
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}

    for name, param in sig.parameters.items():
        # "_" placeholders and **kwargs don't belong in the schema.
        if name.startswith("_") or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        props[name] = {"type": _json_type_for(param.annotation)}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def anthropic_tool_specs() -> list[dict[str, Any]]:
    """Tool definitions in the Anthropic Messages API format."""
    specs: list[dict[str, Any]] = []
    for tool in TOOL_REGISTRY.values():
        specs.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": _schema_from_func(tool.func),
            }
        )
    return specs


__all__ = ["AgentTool", "TOOL_REGISTRY", "list_tool_descriptions", "anthropic_tool_specs"]
