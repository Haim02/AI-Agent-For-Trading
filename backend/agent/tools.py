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
from tools.perplexity_tool import PerplexityTool, PerplexityToolError
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
}


def list_tool_descriptions() -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in TOOL_REGISTRY.values())


__all__ = ["AgentTool", "TOOL_REGISTRY", "list_tool_descriptions"]
