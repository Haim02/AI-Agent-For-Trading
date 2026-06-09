import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from dataclasses import asdict, is_dataclass

logger = logging.getLogger(__name__)

from agent.autonomous_agent import get_autonomous_agent
from analytics.gex_calculator import GEXCalculator
from analytics.iv_rank_calculator import IVRankCalculator
from db.connection import get_db
from db.models import (
    JournalCreate,
    JournalEntry,
    PositionCreate,
    PositionUpdate,
    TradeCreate,
)
from memory.long_term import LongTermMemory
from scrapers.finviz_scraper import FinVizScraper
from scrapers.menthorq_scraper import MenthorQScraper
from services.scanner_service import ScannerService
from tools.perplexity_tool import PerplexityTool, PerplexityToolError

from api.analytics_routes import router as analytics_router  # noqa: E402

router = APIRouter()
router.include_router(analytics_router)


def _fix_id(doc: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if doc is None:
        return None
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid id: {value}")


# ───────────────────────── health & summary ─────────────────────────

@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@router.get("/summary")
async def summary() -> dict[str, Any]:
    db = get_db()

    open_positions = await db.positions.count_documents({"status": "open"})
    closed_positions = await db.positions.count_documents({"status": "closed"})

    pipeline = [
        {"$match": {"realized_pnl": {"$ne": None}}},
        {"$group": {"_id": None, "total": {"$sum": "$realized_pnl"}}},
    ]
    agg = await db.positions.aggregate(pipeline).to_list(length=1)
    total_realized_pnl = float(agg[0]["total"]) if agg else 0.0

    last_journal = await db.journal.find_one(sort=[("date", -1)])
    last_journal = _fix_id(last_journal)

    return {
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "total_realized_pnl": total_realized_pnl,
        "last_journal": last_journal,
    }


# ───────────────────────── positions ─────────────────────────

@router.get("/positions")
async def list_positions(
    status: Optional[str] = Query(default=None),
    ticker: Optional[str] = Query(default=None),
) -> list[dict[str, Any]]:
    db = get_db()
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    if ticker:
        query["ticker"] = ticker.upper()

    cursor = db.positions.find(query).sort("entry_date", -1)
    docs = await cursor.to_list(length=500)
    return [_fix_id(d) for d in docs]


@router.get("/positions/{position_id}")
async def get_position(position_id: str) -> dict[str, Any]:
    db = get_db()
    doc = await db.positions.find_one({"_id": _oid(position_id)})
    if doc is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return _fix_id(doc)


@router.post("/positions", status_code=201)
async def create_position(body: PositionCreate) -> dict[str, Any]:
    try:
        db = get_db()
        now = datetime.utcnow()
        doc = body.model_dump()
        doc.update(
            {
                "ticker": body.ticker.upper(),
                "status": "open",
                "entry_date": now,
                "created_at": now,
                "updated_at": now,
            }
        )
        result = await db.positions.insert_one(doc)
        return {"id": str(result.inserted_id)}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Position creation error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/positions/{position_id}")
async def update_position(position_id: str, payload: PositionUpdate) -> dict[str, Any]:
    db = get_db()
    update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")

    update["updated_at"] = datetime.utcnow()
    result = await db.positions.update_one({"_id": _oid(position_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"updated": result.modified_count}


@router.delete("/positions/{position_id}")
async def delete_position(position_id: str) -> dict[str, Any]:
    db = get_db()
    result = await db.positions.delete_one({"_id": _oid(position_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"deleted": result.deleted_count}


class ClosePositionRequest(BaseModel):
    pnl: float
    notes: Optional[str] = None


@router.put("/positions/{position_id}/close")
async def close_position(position_id: str, payload: ClosePositionRequest) -> dict[str, Any]:
    db = get_db()
    now = datetime.utcnow()
    update = {
        "status": "closed",
        "realized_pnl": payload.pnl,
        "close_date": now,
        "updated_at": now,
    }
    if payload.notes:
        update["close_notes"] = payload.notes
    result = await db.positions.update_one({"_id": _oid(position_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"closed": True, "realized_pnl": payload.pnl}


# ───────────────────────── journal ─────────────────────────

@router.get("/journal")
async def list_journal() -> list[dict[str, Any]]:
    db = get_db()
    cursor = db.journal.find({}).sort("date", -1).limit(30)
    docs = await cursor.to_list(length=30)
    return [_fix_id(d) for d in docs]


@router.get("/journal/{date}")
async def get_journal_entry(date: str) -> dict[str, Any]:
    db = get_db()
    doc = await db.journal.find_one({"date": date})
    if doc is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return _fix_id(doc)


@router.post("/journal", status_code=201)
async def create_journal(payload: JournalCreate) -> dict[str, Any]:
    db = get_db()
    entry = JournalEntry(**payload.model_dump()).model_dump()
    result = await db.journal.insert_one(entry)
    return {"id": str(result.inserted_id)}


# ───────────────────────── trades ─────────────────────────

@router.get("/trades")
async def list_trades(
    ticker: Optional[str] = Query(default=None),
) -> list[dict[str, Any]]:
    db = get_db()
    query: dict[str, Any] = {}
    if ticker:
        query["ticker"] = ticker.upper()

    cursor = db.trades.find(query).sort("timestamp", -1).limit(50)
    docs = await cursor.to_list(length=50)
    return [_fix_id(d) for d in docs]


@router.post("/trades", status_code=201)
async def create_trade(payload: TradeCreate) -> dict[str, Any]:
    db = get_db()
    now = datetime.utcnow()
    doc = payload.model_dump()
    doc.update(
        {
            "ticker": payload.ticker.upper(),
            "timestamp": now,
            "created_at": now,
        }
    )
    result = await db.trades.insert_one(doc)
    return {"id": str(result.inserted_id)}


# ───────────────────────── research (Perplexity) ─────────────────────────

class TickerRequest(BaseModel):
    ticker: str


class QueryRequest(BaseModel):
    query: str


def _perplexity() -> PerplexityTool:
    try:
        return PerplexityTool()
    except PerplexityToolError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/research/ticker")
async def research_ticker(payload: TickerRequest) -> dict[str, Any]:
    tool = _perplexity()
    try:
        return tool.research_ticker(payload.ticker)
    except PerplexityToolError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/research/market")
async def research_market() -> dict[str, Any]:
    tool = _perplexity()
    try:
        return tool.market_overview()
    except PerplexityToolError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/research/query")
async def research_query(payload: QueryRequest) -> dict[str, Any]:
    tool = _perplexity()
    try:
        return tool.search(payload.query)
    except PerplexityToolError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ───────────────────────── memory ─────────────────────────

class LearnRequest(BaseModel):
    content: str
    category: str = "preference"


_long_term: LongTermMemory | None = None


def _ltm() -> LongTermMemory:
    global _long_term
    if _long_term is None:
        try:
            _long_term = LongTermMemory()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"LongTermMemory unavailable: {exc}")
    return _long_term


@router.post("/memory/learn", status_code=201)
async def memory_learn(payload: LearnRequest) -> dict[str, Any]:
    ltm = _ltm()
    category = (payload.category or "preference").lower().strip()
    try:
        if category in ("preference", "behavior", "goal", "risk_tolerance"):
            doc_id = ltm.save_user_fact(payload.content, category=category)
            collection = "user_profile"
        elif category in ("trade_learning", "lesson"):
            doc_id = ltm.save_trade_learning(payload.content, context={})
            collection = "trade_learnings"
        elif category in ("market_pattern", "pattern"):
            doc_id = ltm.save_market_pattern(payload.content, conditions={})
            collection = "market_patterns"
        elif category in ("strategy", "strategy_knowledge"):
            doc_id = ltm.save_strategy_knowledge(payload.content, context={})
            collection = "strategy_knowledge"
        else:
            doc_id = ltm.save_knowledge(payload.content, {"category": category})
            collection = "knowledge_base"
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LongTermMemory write failed: {exc}")
    return {"id": doc_id, "collection": collection, "category": category}


@router.get("/memory/profile")
async def memory_profile(limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
    ltm = _ltm()
    items = ltm.list_collection("user_profile", limit=limit)
    return {"count": len(items), "items": items}


@router.get("/memory/learnings")
async def memory_learnings(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    ltm = _ltm()
    items = ltm.list_collection("trade_learnings", limit=limit)
    return {"count": len(items), "items": items}


@router.delete("/memory/raw-data")
async def memory_delete_raw(
    older_than_days: int = Query(default=7, ge=1, le=365),
) -> dict[str, Any]:
    ltm = _ltm()
    return ltm.delete_raw_data(older_than_days=older_than_days)


# ───────────────────────── scan & gex ─────────────────────────

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


# ───────────────────────── autonomous agent ─────────────────────────

class AgentChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class AgentTaskRequest(BaseModel):
    task: str


@router.post("/agent/chat")
async def agent_chat(payload: AgentChatRequest) -> dict[str, Any]:
    agent = get_autonomous_agent()
    session_id = payload.session_id or f"api:{datetime.utcnow().timestamp()}"
    try:
        response = await agent.run(payload.message, session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent run failed: {exc}")
    return {"response": response, "session_id": session_id}


@router.post("/agent/task")
async def agent_task(payload: AgentTaskRequest) -> dict[str, Any]:
    try:
        response = await get_autonomous_agent().run_autonomous_task(payload.task)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent task failed: {exc}")
    return {"response": response}


@router.get("/agent/status")
async def agent_status() -> dict[str, Any]:
    agent = get_autonomous_agent()
    memory_stats: dict[str, Any] = {}
    try:
        memory_stats = LongTermMemory().stats()
    except Exception as exc:  # noqa: BLE001
        memory_stats = {"error": str(exc)}
    return {
        "status": "active",
        "last_scan": agent.last_scan_at.isoformat() if agent.last_scan_at else None,
        "last_reflection": agent.last_reflection_at.isoformat() if agent.last_reflection_at else None,
        "memory_stats": memory_stats,
    }


@router.get("/scan/morning")
async def scan_morning() -> dict[str, Any]:
    try:
        return await ScannerService().run_morning_scan()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Morning scan failed: {exc}")


@router.get("/scan/ticker/{ticker}")
async def scan_ticker(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    try:
        with FinVizScraper(headless=True) as scraper:
            detail = scraper.scrape_ticker(ticker)
        try:
            gex = GEXCalculator().calculate_gex(ticker)
        except Exception:  # noqa: BLE001
            gex = None
        news: dict[str, Any] = {}
        try:
            news = PerplexityTool().research_ticker(ticker)
        except PerplexityToolError:
            news = {}
        return {
            "ticker": ticker,
            "detail": _serialize(detail),
            "gex": _serialize(gex) if gex else None,
            "news": news,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ticker scan failed: {exc}")


@router.get("/gex/menthorq")
async def gex_menthorq() -> dict[str, Any]:
    try:
        with MenthorQScraper(headless=True) as scraper:
            return _serialize(scraper.scrape_gex_data())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MenthorQ scrape failed: {exc}")


@router.get("/iv/scan")
async def iv_scan(min_iv_rank: float = Query(default=50.0, ge=0.0, le=100.0)) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(IVRankCalculator().get_sp500_iv_opportunities)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"IV scan failed: {exc}")
    payload["sell_opportunities"] = [
        _serialize(r) for r in payload["sell_opportunities"] if r.iv_rank >= min_iv_rank
    ]
    payload["golden_opportunities"] = [_serialize(r) for r in payload["golden_opportunities"]]
    payload["buy_opportunities"] = [_serialize(r) for r in payload["buy_opportunities"]]
    payload["scan_time"] = payload["scan_time"].isoformat()
    return payload


@router.get("/iv/golden")
async def iv_golden() -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(IVRankCalculator().get_sp500_iv_opportunities)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"IV scan failed: {exc}")
    golden = [_serialize(r) for r in payload["golden_opportunities"]]
    return {
        "count": len(golden),
        "golden_opportunities": golden,
        "scan_time": payload["scan_time"].isoformat(),
        "market_summary": payload["market_summary"],
    }


@router.get("/iv/{ticker}")
async def iv_for_ticker(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    try:
        result = await asyncio.to_thread(IVRankCalculator().calculate_iv_rank, ticker)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"IV rank failed: {exc}")
    if result is None:
        raise HTTPException(status_code=404, detail=f"No IV data available for {ticker}")
    return _serialize(result)


@router.get("/gex/levels/{ticker}")
async def gex_levels_chart(ticker: str = "SPX") -> dict[str, Any]:
    """GEX key levels + OHLC candles formatted for the frontend chart."""
    try:
        import pandas as pd
        import pytz
        import yfinance as yf

        ET = pytz.timezone("America/New_York")
        IL = pytz.timezone("Asia/Jerusalem")
        now_et = datetime.now(ET)

        # US session: 09:30–16:00 ET, Mon-Fri.
        market_open_dt = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close_dt = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        is_market_open = (
            now_et.weekday() < 5 and market_open_dt <= now_et <= market_close_dt
        )

        yf_ticker = ticker
        if ticker.upper() == "SPX":
            yf_ticker = "^GSPC"
        elif ticker.upper() == "NDX":
            yf_ticker = "^NDX"
        elif ticker.upper() == "VIX":
            yf_ticker = "^VIX"

        def _fetch_yfinance_sync(symbol: str) -> list[dict[str, Any]]:
            """Sync yfinance fetch – wrapped in a thread + asyncio.wait_for timeout below."""
            result: list[dict[str, Any]] = []
            try:
                t = yf.Ticker(symbol)
                h = t.history(period="1d", interval="1m", prepost=False, timeout=15)
                if h is None or h.empty:
                    h = t.history(period="5d", interval="5m", timeout=15)
                    if h is not None and not h.empty:
                        last_date = h.index[-1].date()
                        h = h[h.index.date == last_date]
                if h is None or h.empty:
                    return []

                if len(h) > 50:
                    try:
                        h = h.resample("5min").agg(
                            {"Open": "first", "High": "max", "Low": "min",
                             "Close": "last", "Volume": "sum"}
                        ).dropna()
                    except Exception:  # noqa: BLE001
                        pass

                for ts, row in h.iterrows():
                    try:
                        ts_il = ts.astimezone(IL) if ts.tzinfo else ts
                        ts_et = ts.astimezone(ET) if ts.tzinfo else ts
                        result.append(
                            {
                                "time": int(ts.timestamp()),
                                "time_il": ts_il.strftime("%H:%M"),
                                "time_et": ts_et.strftime("%H:%M"),
                                "open": round(float(row["Open"]), 2),
                                "high": round(float(row["High"]), 2),
                                "low": round(float(row["Low"]), 2),
                                "close": round(float(row["Close"]), 2),
                                "volume": int(row.get("Volume", 0) or 0),
                            }
                        )
                    except Exception:  # noqa: BLE001
                        continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("yfinance sync error: %s", exc)
            return result

        # Run yfinance in a thread with a hard 30s timeout. yfinance can hang
        # indefinitely on Railway when Yahoo is rate-limiting the egress IP.
        candles: list[dict[str, Any]] = []
        try:
            candles = await asyncio.wait_for(
                asyncio.to_thread(_fetch_yfinance_sync, yf_ticker), timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.warning("yfinance timeout for %s", yf_ticker)
            candles = []
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance executor error: %s", exc)
            candles = []

        spot = candles[-1]["close"] if candles else 0.0
        if candles:
            logger.info(
                "✅ yfinance: %d candles for %s, spot=%s", len(candles), yf_ticker, spot
            )

        # Yahoo direct-HTTP fallback when the yfinance lib path is blocked.
        if not candles or spot == 0:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}",
                        params={"interval": "5m", "range": "1d"},
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                if resp.status_code == 200:
                    jdata = resp.json()
                    result = (jdata.get("chart") or {}).get("result") or []
                    if result:
                        meta = result[0].get("meta") or {}
                        spot = float(meta.get("regularMarketPrice") or 0)
                        timestamps = result[0].get("timestamp") or []
                        ind = (result[0].get("indicators") or {}).get("quote") or [{}]
                        ind = ind[0] if ind else {}
                        opens = ind.get("open") or []
                        highs = ind.get("high") or []
                        lows = ind.get("low") or []
                        closes = ind.get("close") or []
                        volumes = ind.get("volume") or []
                        for i, ts_val in enumerate(timestamps):
                            try:
                                if i >= len(closes) or closes[i] is None:
                                    continue
                                ts_dt_il = datetime.fromtimestamp(ts_val, tz=IL)
                                ts_dt_et = datetime.fromtimestamp(ts_val, tz=ET)
                                close_val = float(closes[i])
                                candles.append(
                                    {
                                        "time": int(ts_val),
                                        "time_il": ts_dt_il.strftime("%H:%M"),
                                        "time_et": ts_dt_et.strftime("%H:%M"),
                                        "open": round(float(opens[i] or close_val), 2),
                                        "high": round(float(highs[i] or close_val), 2),
                                        "low": round(float(lows[i] or close_val), 2),
                                        "close": round(close_val, 2),
                                        "volume": int(volumes[i] or 0)
                                        if i < len(volumes)
                                        else 0,
                                    }
                                )
                            except Exception:  # noqa: BLE001
                                continue
                        if candles:
                            spot = candles[-1]["close"]
                            logger.info("✅ Yahoo direct API fallback: spot=%s", spot)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Yahoo direct API fallback failed: %s", exc)

        # Final fallback: realistic synthetic candles with seeded randomness.
        if not candles or spot == 0:
            import random as _random

            default_prices = {
                "SPX": 5880, "^GSPC": 5880,
                "SPY": 588, "QQQ": 510,
                "NDX": 21400, "^NDX": 21400,
                "IWM": 212, "VIX": 18, "^VIX": 18,
                "DJI": 43000, "^DJI": 43000,
                "NVDA": 135, "TSLA": 340, "META": 625,
                "AAPL": 230, "AMD": 165, "MSFT": 420,
                "GOOGL": 180, "AMZN": 200,
            }
            spot = float(
                default_prices.get(yf_ticker)
                or default_prices.get(ticker.upper(), 500)
            )
            now_il = datetime.now(IL)
            start = now_il.replace(hour=16, minute=30, second=0, microsecond=0)
            current_price = spot * 0.995
            _random.seed(42)
            for i in range(78):  # 6.5 hours × 12 five-minute bars
                ts = start + timedelta(minutes=5 * i)
                if ts > now_il:
                    break
                move = _random.uniform(-0.0015, 0.0018)
                current_price = current_price * (1 + move)
                ts_et = ts.astimezone(ET) if ts.tzinfo else ts
                candles.append(
                    {
                        "time": int(ts.timestamp()),
                        "time_il": ts.strftime("%H:%M"),
                        "time_et": ts_et.strftime("%H:%M"),
                        "open": round(current_price, 2),
                        "high": round(current_price * 1.0008, 2),
                        "low": round(current_price * 0.9992, 2),
                        "close": round(current_price, 2),
                        "volume": _random.randint(50_000, 200_000),
                    }
                )
            if candles:
                spot = candles[-1]["close"]
            logger.warning(
                "Using synthetic candles for %s, spot=%s", ticker, spot
            )

        call_wall: Optional[float] = None
        put_wall: Optional[float] = None
        gamma_flip: Optional[float] = None
        zero_dte_magnet: Optional[float] = None
        highest_oi_strike: Optional[float] = None
        top_strikes: list[dict[str, Any]] = []
        regime = "positive"

        # PRIMARY: FlashAlpha (supports SPX natively).
        if os.getenv("FLASHALPHA_API_KEY"):
            try:
                from tools.flashalpha_tool import FlashAlphaTool

                fa_data = await FlashAlphaTool().get_full_analysis(ticker.upper())
                if fa_data and "error" not in fa_data:
                    call_wall = fa_data.get("call_wall")
                    put_wall = fa_data.get("put_wall")
                    gamma_flip = fa_data.get("gamma_flip")
                    zero_dte_magnet = fa_data.get("zero_dte_magnet")
                    highest_oi_strike = fa_data.get("highest_oi_strike")
                    top_strikes = fa_data.get("top_strikes") or []
                    regime = fa_data.get("regime") or "positive"
            except Exception:  # noqa: BLE001
                logger.exception("gex_levels_chart: FlashAlpha failed – trying GEXEngine")

        # FALLBACK: GEXEngine (UW → Massive → yfinance → estimated).
        if call_wall is None and put_wall is None:
            try:
                from analytics.gex_engine import GEXEngine

                spy_ticker = "SPY" if ticker.upper() == "SPX" else ticker.upper()
                gex_data = await GEXEngine().get_full_gex_analysis(spy_ticker)
                if "error" not in gex_data:
                    call_wall = gex_data.get("call_wall")
                    put_wall = gex_data.get("put_wall")
                    gamma_flip = gex_data.get("gamma_flip")
                    top_strikes = gex_data.get("top_strikes") or []
                    regime = gex_data.get("regime") or "positive"
            except Exception:  # noqa: BLE001
                logger.exception("gex_levels_chart: GEXEngine failed – falling back to synthetic")

        # Synthetic fallback so the chart always renders something useful.
        if call_wall is None:
            call_wall = round(spot * 1.012, 0)
        if put_wall is None:
            put_wall = round(spot * 0.990, 0)
        if gamma_flip is None:
            gamma_flip = round(spot * 0.998, 0)

        # Clamp extreme levels: anything more than 15% from spot is almost
        # certainly a data artifact (wrong contract, very thin OI strike, etc.).
        # Snap it back to a sensible ±2-3% estimate so the chart stays readable.
        MAX_LEVEL_DIST = 0.15

        def _clamp_level(level: Optional[float], direction: str) -> Optional[float]:
            if level is None or spot <= 0:
                return level
            if abs(level - spot) / spot <= MAX_LEVEL_DIST:
                return level
            if direction == "call":
                return round(spot * 1.025, 0)
            if direction == "put":
                return round(spot * 0.975, 0)
            return round(spot * 0.998, 0)  # flip

        call_wall = _clamp_level(call_wall, "call")
        put_wall = _clamp_level(put_wall, "put")
        gamma_flip = _clamp_level(gamma_flip, "flip")

        levels: list[dict[str, Any]] = [
            {
                "id": "call_wall",
                "label": f"Call Wall {int(call_wall)}",
                "price": call_wall,
                "color": "#22c55e",
                "style": "solid",
                "width": 2,
                "side": "call",
                "description": "תקרה מבנית – Dealers מוכרים",
            },
            {
                "id": "gamma_flip",
                "label": f"Gamma Flip {int(gamma_flip)}",
                "price": gamma_flip,
                "color": "#f59e0b",
                "style": "dashed",
                "width": 2,
                "side": "flip",
                "description": "מעבר בין Positive/Negative",
            },
            {
                "id": "put_wall",
                "label": f"Put Wall {int(put_wall)}",
                "price": put_wall,
                "color": "#ef4444",
                "style": "solid",
                "width": 2,
                "side": "put",
                "description": "רצפה מבנית – Dealers קונים",
            },
        ]

        # FlashAlpha-only levels: 0DTE magnet + highest-OI strike.
        if zero_dte_magnet:
            levels.append(
                {
                    "id": "zero_dte_magnet",
                    "label": f"0DTE Magnet {int(zero_dte_magnet)}",
                    "price": zero_dte_magnet,
                    "color": "#a855f7",
                    "style": "dashed",
                    "width": 2,
                    "side": "magnet",
                    "description": "מגנט 0DTE – ריכוז פקיעות יומיות",
                }
            )
        if highest_oi_strike:
            levels.append(
                {
                    "id": "highest_oi",
                    "label": f"Max OI {int(highest_oi_strike)}",
                    "price": highest_oi_strike,
                    "color": "#06b6d4",
                    "style": "solid",
                    "width": 1,
                    "side": "oi",
                    "description": "Strike עם הכי הרבה Open Interest",
                }
            )

        ut_count = 1
        dt_count = 1
        for s in top_strikes[:6]:
            strike = s.get("strike") or 0
            if not strike:
                continue
            # Skip strikes more than ±8% from spot – they'd compress the chart.
            if spot > 0 and abs(strike - spot) / spot > 0.08:
                continue
            if strike > spot and ut_count <= 3:
                levels.append(
                    {
                        "id": f"ut{ut_count}",
                        "label": f"UT{ut_count} {int(strike)}",
                        "price": strike,
                        "color": "#86efac",
                        "style": "solid",
                        "width": 1,
                        "side": "call",
                        "description": f"GEX Upside Target {ut_count}",
                    }
                )
                ut_count += 1
            elif strike < spot and dt_count <= 3:
                levels.append(
                    {
                        "id": f"dt{dt_count}",
                        "label": f"DT{dt_count} {int(strike)}",
                        "price": strike,
                        "color": "#fca5a5",
                        "style": "solid",
                        "width": 1,
                        "side": "put",
                        "description": f"GEX Downside Target {dt_count}",
                    }
                )
                dt_count += 1

        price_step = spot * 0.003
        call_entry = round(call_wall - price_step, 0)
        put_entry = round(put_wall + price_step, 0)
        arrows = [
            {
                "id": "call_entry",
                "label": f"Call above {int(call_entry)}",
                "price": call_entry,
                "color": "#4ade80",
                "direction": "up",
                "description": "כניסת Call אם מחיר עולה מעל",
            },
            {
                "id": "put_entry",
                "label": f"Put below {int(put_entry)}",
                "price": put_entry,
                "color": "#f87171",
                "direction": "down",
                "description": "כניסת Put אם מחיר יורד מתחת",
            },
        ]

        if candles:
            day_open = candles[0]["open"]
            day_high = max(c["high"] for c in candles)
            day_low = min(c["low"] for c in candles)
            day_change = spot - day_open
            day_change_pct = (day_change / day_open * 100) if day_open else 0.0
        else:
            day_open = day_high = day_low = spot
            day_change = 0.0
            day_change_pct = 0.0

        return {
            "ticker": ticker,
            "spot": spot,
            "regime": regime,
            "candles": candles,
            "levels": levels,
            "arrows": arrows,
            "day_info": {
                "date": now_et.strftime("%d/%m/%Y"),
                "open": day_open,
                "high": day_high,
                "low": day_low,
                "change": round(day_change, 2),
                "change_pct": round(day_change_pct, 2),
                "is_market_open": is_market_open,
                "session_label": "פתוח" if is_market_open else "סגור",
                "candle_count": len(candles),
                "interval": "5m",
                "timeframe": "0DTE – יום נוכחי",
            },
            "timestamp": datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("gex_levels_chart failed for %s – returning empty shell", ticker)
        # Never 500 the chart endpoint – the UI shows "שגיאה בטעינת נתונים".
        # Return a minimal valid payload so the chart at least renders empty.
        fallback_spot = 100.0
        return {
            "ticker": ticker,
            "spot": fallback_spot,
            "regime": "positive",
            "candles": [],
            "levels": [],
            "arrows": [],
            "day_info": {
                "date": datetime.utcnow().strftime("%d/%m/%Y"),
                "open": fallback_spot,
                "high": fallback_spot,
                "low": fallback_spot,
                "change": 0.0,
                "change_pct": 0.0,
                "is_market_open": False,
                "session_label": "סגור",
                "candle_count": 0,
                "interval": "5m",
                "timeframe": "0DTE – יום נוכחי",
            },
            "timestamp": datetime.utcnow().isoformat(),
        }


@router.get("/gex/{ticker}")
async def gex_for_ticker(ticker: str) -> dict[str, Any]:
    requested = ticker.upper().strip()
    lookup = "SPX" if requested in {"SPX", "^SPX", "SPXW", "^GSPC"} else requested
    try:
        calc = GEXCalculator()
        gex = calc.calculate_gex(lookup)
        key_levels = calc.get_key_levels(lookup)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"GEX calculation failed: {exc}")

    interpretation = _interpret_gex(gex)
    return {
        "ticker": gex.ticker,
        "spot_price": gex.spot_price,
        "gex_total_millions": gex.gex_total,
        "gamma_flip": gex.gamma_flip_level,
        "call_wall": gex.call_wall,
        "put_wall": gex.put_wall,
        "regime": gex.regime,
        "dealer_behavior": gex.dealer_behavior,
        "top_strikes": gex.top_gex_strikes,
        "interpretation": interpretation,
        # Keep the legacy shape for existing frontend consumers.
        "gex": _serialize(gex),
        "key_levels": key_levels,
    }


def _interpret_gex(gex: Any) -> str:
    spot = float(getattr(gex, "spot_price", 0) or 0)
    flip = float(getattr(gex, "gamma_flip_level", 0) or 0)
    regime = getattr(gex, "regime", "unknown")
    ticker = getattr(gex, "ticker", "SPX")
    if spot <= 0 or flip <= 0:
        return f"{ticker}: אין מספיק נתוני GEX לפרשנות."
    if spot > flip:
        return (
            f"{ticker} נמצא מעל Gamma Flip – שוק ב-Long Gamma, "
            "צפי לתנודתיות נמוכה"
        )
    return (
        f"{ticker} נמצא מתחת ל-Gamma Flip – שוק ב-Short Gamma, "
        "צפי לתנודתיות גבוהה" if regime != "positive" else
        f"{ticker} מתחת ל-Gamma Flip אך GEX חיובי – תמונה מעורבת"
    )


@router.get("/memory/recall")
async def memory_recall(
    q: str = Query(..., min_length=1),
    n: int = Query(default=3, ge=1, le=20),
    collection: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    ltm = _ltm()
    if collection:
        return {"query": q, "results": {collection: ltm.recall(q, collection, n_results=n)}}
    return {"query": q, "results": ltm.recall_all(q, n_results=n)}


# ───────────────────────── smart news monitor ─────────────────────────


@router.get("/news/recent")
async def news_recent(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    db = get_db()
    cursor = db.news_items.find().sort("sent_to_user_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [_fix_id(d) for d in docs if d is not None]


@router.get("/news/stats")
async def news_stats() -> dict[str, Any]:
    db = get_db()
    total = await db.news_items.count_documents({"sent_to_user_at": {"$ne": None}})
    pipeline = [
        {"$match": {"sent_to_user_at": {"$ne": None}}},
        {
            "$group": {
                "_id": "$category",
                "count": {"$sum": 1},
                "avg_impact": {"$avg": "$impact_score"},
            }
        },
        {"$sort": {"count": -1}},
    ]
    by_category = []
    async for row in db.news_items.aggregate(pipeline):
        by_category.append(
            {
                "category": row.get("_id"),
                "count": row.get("count", 0),
                "avg_impact": (
                    round(float(row["avg_impact"]), 2) if row.get("avg_impact") is not None else None
                ),
            }
        )
    return {"total_sent": total, "by_category": by_category}


@router.get("/scanner/daily-stocks")
async def scanner_daily_stocks(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    db = get_db()
    start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    cursor = (
        db.stock_analyses.find({"analysis_date": {"$gte": start_of_day}})
        .sort("quality_score", -1)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [_fix_id(d) for d in docs if d is not None]


@router.get("/scanner/learning-stats")
async def scanner_learning_stats() -> dict[str, Any]:
    db = get_db()
    base_filter = {"prediction_correct": {"$ne": None}, "actual_move_pct": {"$ne": None}}
    total = await db.stock_analyses.count_documents(base_filter)
    correct = await db.stock_analyses.count_documents(
        {**base_filter, "prediction_correct": True}
    )
    accuracy = round((correct / total) * 100.0, 2) if total else 0.0

    winners_pipeline = [
        {"$match": {"actual_move_pct": {"$gt": 0}}},
        {"$group": {"_id": None, "avg": {"$avg": "$actual_move_pct"}}},
    ]
    losers_pipeline = [
        {"$match": {"actual_move_pct": {"$lt": 0}}},
        {"$group": {"_id": None, "avg": {"$avg": "$actual_move_pct"}}},
    ]
    win_agg = await db.stock_analyses.aggregate(winners_pipeline).to_list(length=1)
    lose_agg = await db.stock_analyses.aggregate(losers_pipeline).to_list(length=1)
    avg_winner = round(float(win_agg[0]["avg"]), 2) if win_agg else 0.0
    avg_loser = round(float(lose_agg[0]["avg"]), 2) if lose_agg else 0.0

    pattern_pipeline = [
        {"$match": {"prediction_correct": True, "actual_move_pct": {"$ne": None}}},
        {
            "$group": {
                "_id": {"trend": "$trend", "catalysts": "$catalysts"},
                "count": {"$sum": 1},
                "avg_move": {"$avg": "$actual_move_pct"},
            }
        },
        {"$match": {"count": {"$gte": 3}}},
        {"$sort": {"avg_move": -1}},
        {"$limit": 5},
    ]
    best_patterns: list[dict[str, Any]] = []
    async for row in db.stock_analyses.aggregate(pattern_pipeline):
        best_patterns.append(
            {
                "trend": (row.get("_id") or {}).get("trend"),
                "catalysts": (row.get("_id") or {}).get("catalysts") or [],
                "sample_size": row.get("count", 0),
                "avg_move": round(float(row.get("avg_move") or 0.0), 2),
            }
        )

    return {
        "total_predictions": total,
        "correct_predictions": correct,
        "accuracy_pct": accuracy,
        "best_patterns": best_patterns,
        "avg_winner_move": avg_winner,
        "avg_loser_move": avg_loser,
    }


@router.post("/scanner/trigger-scan")
async def scanner_trigger_scan() -> dict[str, Any]:
    from services.daily_stock_scanner import DailyStockScanner

    try:
        analyses = await DailyStockScanner().run_daily_scan()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"trigger-scan failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"analyzed": len(analyses)}


@router.post("/news/trigger")
async def news_trigger(source: str = Query(default="company")) -> dict[str, Any]:
    from services.smart_news_monitor import SmartNewsMonitor

    monitor = SmartNewsMonitor()
    source = (source or "company").lower().strip()
    try:
        if source == "company":
            count = await monitor.check_company_news()
        elif source == "macro":
            count = await monitor.check_macro_news()
        elif source == "earnings":
            count = await monitor.check_earnings_today()
        elif source == "all":
            results = await asyncio.gather(
                monitor.check_company_news(),
                monitor.check_macro_news(),
                monitor.check_earnings_today(),
                return_exceptions=True,
            )
            count = sum(r for r in results if isinstance(r, int))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown source: {source}")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"news/trigger failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"source": source, "sent": count}


# ───────────────────────── Massive API endpoints ─────────────────────────


async def _with_massive(coro_factory) -> Any:
    from tools.massive_tool import MassiveTool

    massive = MassiveTool()
    try:
        return await coro_factory(massive)
    finally:
        await massive.close()


@router.get("/massive/quote/{ticker}")
async def massive_quote(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    return await _with_massive(lambda m: m.get_stock_quote(ticker))


@router.get("/massive/options-chain/{ticker}")
async def massive_options_chain(
    ticker: str,
    expiration: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    return await _with_massive(lambda m: m.get_options_chain(ticker, expiration))


@router.get("/massive/options-flow/{ticker}")
async def massive_options_flow(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    return await _with_massive(lambda m: m.get_options_flow(ticker))


@router.get("/massive/unusual")
async def massive_unusual(
    min_volume: int = Query(default=1000, ge=1),
) -> list[dict[str, Any]]:
    return await _with_massive(lambda m: m.get_unusual_options_activity(min_volume))


@router.get("/massive/gex/{ticker}")
async def massive_gex(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    return await _with_massive(lambda m: m.calculate_precise_gex(ticker))


@router.get("/massive/iv-rank/{ticker}")
async def massive_iv_rank(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    return await _with_massive(lambda m: m.get_precise_iv_rank(ticker))


# ───────────────────────── Unusual Whales scraper ─────────────────────────

@router.get("/uw/report/{ticker}")
async def get_uw_report(ticker: str = "SPY") -> dict[str, Any]:
    """Full GEX + Flow + Market Tide + News snapshot from Unusual Whales."""
    from scrapers.uw_scraper import UnusualWhalesScraper
    scraper = UnusualWhalesScraper()
    try:
        return await scraper.get_full_report(ticker.upper().strip())
    except Exception as exc:  # noqa: BLE001
        logger.exception("uw_report failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await scraper.close()


@router.get("/uw/flow")
async def get_uw_flow(ticker: Optional[str] = Query(default=None)) -> dict[str, Any]:
    """Options flow alerts from Unusual Whales. ``ticker`` is optional."""
    from scrapers.uw_scraper import UnusualWhalesScraper
    scraper = UnusualWhalesScraper()
    try:
        data = await scraper.get_options_flow(
            ticker.upper().strip() if ticker else None
        )
        return {"flow": data}
    except Exception as exc:  # noqa: BLE001
        logger.exception("uw_flow failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await scraper.close()


# ───────────────────────── FlashAlpha Lab ─────────────────────────

@router.get("/flashalpha/levels/{ticker}")
async def fa_levels(ticker: str) -> Optional[dict[str, Any]]:
    """Key levels (gamma flip, walls, OI, 0DTE magnet) from FlashAlpha."""
    from tools.flashalpha_tool import FlashAlphaTool
    return await FlashAlphaTool().get_levels(ticker.upper().strip())


@router.get("/flashalpha/gex/{ticker}")
async def fa_gex(
    ticker: str, expiration: Optional[str] = Query(default=None)
) -> Optional[dict[str, Any]]:
    """Full GEX by strike from FlashAlpha. ``expiration`` filter for non-Growth plans."""
    from tools.flashalpha_tool import FlashAlphaTool
    return await FlashAlphaTool().get_gex(ticker.upper().strip(), expiration)


@router.get("/flashalpha/zerodte/{ticker}")
async def fa_zerodte(ticker: str) -> Optional[dict[str, Any]]:
    """0DTE-specific exposure data from FlashAlpha."""
    from tools.flashalpha_tool import FlashAlphaTool
    return await FlashAlphaTool().get_zero_dte(ticker.upper().strip())


@router.get("/flashalpha/analysis/{ticker}")
async def fa_analysis(ticker: str) -> dict[str, Any]:
    """Combined Hebrew GEX analysis (levels + GEX + regime) from FlashAlpha."""
    from tools.flashalpha_tool import FlashAlphaTool
    return await FlashAlphaTool().get_full_analysis(ticker.upper().strip())


@router.get("/flashalpha/narrative/{ticker}")
async def fa_narrative(ticker: str) -> dict[str, Any]:
    """AI narrative – FlashAlpha (Growth plan) first, then Claude AI fallback."""
    ticker = ticker.upper().strip()
    try:
        from tools.flashalpha_tool import FlashAlphaTool
        result = await FlashAlphaTool().get_narrative_hebrew(ticker)
        if result and "error" not in result:
            return result
    except Exception:  # noqa: BLE001
        logger.exception("fa_narrative: FlashAlpha path failed")
    return await _generate_claude_narrative(ticker)


async def _generate_claude_narrative(ticker: str) -> dict[str, Any]:
    """Claude Haiku narrative built on top of cached FlashAlpha GEX levels + VIX."""
    import pytz as _pytz

    israel_tz = _pytz.timezone("Asia/Jerusalem")

    gex_data: dict[str, Any] = {}
    try:
        from tools.flashalpha_tool import FlashAlphaTool
        gex_data = await FlashAlphaTool().get_full_analysis(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.warning("narrative: GEX fetch failed (%s)", exc)

    spot = gex_data.get("spot_price") or 0
    call_wall = gex_data.get("call_wall")
    put_wall = gex_data.get("put_wall")
    gamma_flip = gex_data.get("gamma_flip")
    regime = gex_data.get("regime") or "positive"
    net_gex = gex_data.get("total_gex") or 0
    zero_dte = gex_data.get("zero_dte_magnet")

    # VIX in a thread so it can't hang the request.
    vix_val = 18.0
    try:
        def _vix() -> float:
            import yfinance as yf
            h = yf.Ticker("^VIX").history(period="1d")
            return float(h["Close"].iloc[-1]) if not h.empty else 18.0
        vix_val = await asyncio.wait_for(asyncio.to_thread(_vix), timeout=10.0)
    except Exception:  # noqa: BLE001
        pass

    if spot and call_wall and put_wall:
        cw_pct = (call_wall - spot) / spot * 100
        pw_pct = (put_wall - spot) / spot * 100
        context = (
            f"Ticker: {ticker}\n"
            f"Current Price: ${spot:,.2f}\n"
            f"GEX Regime: {regime.upper()} (Net GEX: ${net_gex:.2f}B)\n"
            f"Call Wall: ${call_wall:,.0f} ({cw_pct:+.1f}%)\n"
            f"Put Wall: ${put_wall:,.0f} ({pw_pct:+.1f}%)\n"
            f"Gamma Flip: ${gamma_flip:,.0f}\n"
            f"0DTE Magnet: ${(zero_dte or 0):,.0f}\n"
            f"VIX: {vix_val:.1f}\n"
        )
    else:
        context = (
            f"Ticker: {ticker}\n"
            f"GEX Regime: {regime.upper()}\n"
            f"VIX: {vix_val:.1f}\n"
        )

    prompt = (
        "You are an expert options market analyst.\n"
        "Based on this GEX (Gamma Exposure) data, provide a structured market\n"
        "analysis in Hebrew.\n\n"
        f"{context}\n\n"
        "Respond with ONLY valid JSON, no markdown, exactly:\n"
        "{\n"
        '  "regime": "...",\n'
        '  "gex_change": "...",\n'
        '  "key_levels": "...",\n'
        '  "flow": "...",\n'
        '  "vanna": "...",\n'
        '  "charm": "...",\n'
        '  "zero_dte": "...",\n'
        '  "outlook": "..."\n'
        "}\n\n"
        "Each value: 1-2 Hebrew sentences. Be specific, professional, actionable."
    )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key)
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = ""
            for block in getattr(resp, "content", []) or []:
                if getattr(block, "type", None) == "text":
                    raw += getattr(block, "text", "")
            import json as _json
            import re as _re
            raw = _re.sub(r"```json|```", "", raw).strip()
            sections = _json.loads(raw)

            regime_emoji = "🟢" if regime == "positive" else "🔴"
            formatted = (
                f"📊 ניתוח AI מלא – {ticker}\n"
                f"💰 ספוט: ${spot:,.2f}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"{regime_emoji} משטר Gamma\n"
                f"{sections.get('regime', '—')}\n\n"
                "📈 שינוי יומי\n"
                f"{sections.get('gex_change', '—')}\n\n"
                "🎯 רמות מפתח\n"
                f"{sections.get('key_levels', '—')}\n\n"
                "🌊 תזרים אופציות\n"
                f"{sections.get('flow', '—')}\n\n"
                "🌀 Vanna\n"
                f"{sections.get('vanna', '—')}\n\n"
                "⏳ Charm\n"
                f"{sections.get('charm', '—')}\n\n"
                "⚡ 0DTE\n"
                f"{sections.get('zero_dte', '—')}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🔮 תחזית\n"
                f"{sections.get('outlook', '—')}\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "מקור: Claude AI + FlashAlpha GEX"
            )
            return {
                "symbol": ticker,
                "spot_price": spot,
                "sections_hebrew": sections,
                "formatted_message": formatted,
                "source": "claude_ai_narrative",
                "timestamp": datetime.now(israel_tz).strftime("%H:%M | %d/%m/%Y"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Claude narrative error: %s", exc)

    # Final fallback if Claude is unavailable.
    regime_emoji = "🟢" if regime == "positive" else "🔴"
    strategy = (
        "מכירת פרמיה מומלצת: Iron Condor / Credit Spreads"
        if regime == "positive"
        else "קניית פרמיה: Debit Spreads – הימנע ממכירה"
    )
    levels_line = (
        f"Call Wall: ${call_wall:,.0f} | Put Wall: ${put_wall:,.0f}"
        if call_wall and put_wall
        else "נתונים לא זמינים"
    )
    basic = {
        "regime": f"{regime.title()} Gamma – VIX {vix_val:.1f}",
        "key_levels": levels_line,
        "outlook": strategy,
    }
    return {
        "symbol": ticker,
        "spot_price": spot,
        "sections_hebrew": basic,
        "formatted_message": (
            f"📊 {ticker} | {regime_emoji} {regime.upper()}\n\n"
            f"💰 ספוט: ${spot:,.2f}\n\n"
            f"🎯 {basic['key_levels']}\n\n"
            f"💡 {basic['outlook']}"
        ),
        "source": "basic_fallback",
        "timestamp": datetime.now(israel_tz).strftime("%H:%M | %d/%m/%Y"),
    }


@router.get("/flashalpha/signals/{ticker}/raw")
async def fa_signals_raw(
    ticker: str,
    min_score: int = Query(default=0),
    structure: Optional[str] = Query(default=None),
    window_minutes: int = Query(default=240),
    limit: int = Query(default=50),
) -> dict[str, Any]:
    """Flow signals – FlashAlpha (Alpha plan) first, then yfinance unusual-options."""
    ticker = ticker.upper().strip()
    try:
        from tools.flashalpha_tool import FlashAlphaTool
        result = await FlashAlphaTool().get_flow_signals(
            symbol=ticker,
            min_score=min_score,
            structure=structure,
            window_minutes=window_minutes,
            limit=limit,
        )
        if result and "error" not in result:
            return result
    except Exception:  # noqa: BLE001
        logger.exception("fa_signals_raw: FlashAlpha path failed")
    return await _get_yfinance_unusual_options(ticker, min_score, structure)


async def _get_yfinance_unusual_options(
    ticker: str, min_score: int = 0, structure_filter: Optional[str] = None
) -> dict[str, Any]:
    """Unusual options activity assembled from yfinance chains + a simple 0-100 score."""
    import pytz as _pytz

    israel_tz = _pytz.timezone("Asia/Jerusalem")

    def _fetch() -> tuple[float, list[dict[str, Any]]]:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            spot_hist = t.history(period="1d")
            if spot_hist.empty:
                return 0.0, []
            spot = float(spot_hist["Close"].iloc[-1])

            expirations = (t.options or [])[:3]
            if not expirations:
                return spot, []

            all_options: list[dict[str, Any]] = []
            for exp in expirations:
                try:
                    chain = t.option_chain(exp)
                except Exception:  # noqa: BLE001
                    continue

                try:
                    exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                    dte = (exp_dt - datetime.now()).days
                except Exception:  # noqa: BLE001
                    dte = 0

                def _row_to_signal(row, right: str) -> Optional[dict[str, Any]]:
                    try:
                        vol = int(row.get("volume", 0) or 0)
                        oi = int(row.get("openInterest", 1) or 1)
                        strike = float(row["strike"])
                        price = float(row.get("lastPrice", 0) or 0)
                        if vol < 10 or price < 0.05:
                            return None
                        premium = price * vol * 100
                        vol_oi = vol / max(oi, 1)
                        dist = abs(strike - spot) / spot if spot else 0
                        if right == "C":
                            moneyness = (
                                "ITM" if strike < spot
                                else "ATM" if dist < 0.01
                                else "OTM"
                            )
                            intent = "bullish"
                            default_delta = 0.3
                        else:
                            moneyness = (
                                "ITM" if strike > spot
                                else "ATM" if dist < 0.01
                                else "OTM"
                            )
                            intent = "bearish"
                            default_delta = -0.3
                        score = min(
                            100,
                            int(
                                (min(vol_oi, 5) / 5) * 40
                                + min(premium / 500_000, 1) * 35
                                + (1 - min(dist, 0.1) / 0.1) * 15
                                + (1 - min(dte, 30) / 30) * 10
                            ),
                        )
                        tags: list[str] = []
                        if premium > 1_000_000:
                            tags.append("whale")
                        if vol_oi > 1:
                            tags.append("opening")
                        if dte == 0:
                            tags.append("0dte")
                        return {
                            "ts": datetime.now(israel_tz).isoformat(),
                            "expiry": exp,
                            "strike": strike,
                            "right": right,
                            "side": "buy",
                            "price": round(price, 2),
                            "size": vol,
                            "premium": round(premium),
                            "dte": dte,
                            "structure": "sweep" if vol_oi > 2 else "block",
                            "aggressor": "above_ask" if vol_oi > 3 else "at_ask",
                            "intent": intent,
                            "score": score,
                            "conviction": (
                                "high" if score >= 75
                                else "medium" if score >= 50
                                else "low"
                            ),
                            "tags": tags,
                            "enrichment": {
                                "iv": float(row.get("impliedVolatility", 0.3) or 0.3),
                                "delta": float(row.get("delta", default_delta) or default_delta),
                                "moneyness": moneyness,
                            },
                        }
                    except Exception:  # noqa: BLE001
                        return None

                for _, row in chain.calls.iterrows():
                    sig = _row_to_signal(row, "C")
                    if sig is not None:
                        all_options.append(sig)
                for _, row in chain.puts.iterrows():
                    sig = _row_to_signal(row, "P")
                    if sig is not None:
                        all_options.append(sig)

            all_options.sort(key=lambda x: x["score"], reverse=True)
            return spot, all_options[:50]
        except Exception as exc:  # noqa: BLE001
            logger.error("yfinance options error: %s", exc)
            return 0.0, []

    try:
        spot, signals = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Options fetch timeout: %s", exc)
        spot, signals = 0.0, []

    if min_score > 0:
        signals = [s for s in signals if s["score"] >= min_score]
    if structure_filter:
        signals = [s for s in signals if s["structure"] == structure_filter]

    chain: dict[str, Any] = {}
    try:
        from tools.flashalpha_tool import FlashAlphaTool
        levels = await FlashAlphaTool().get_levels(ticker)
        if levels and "levels" in levels:
            lv = levels["levels"]
            chain = {
                "call_wall": lv.get("call_wall"),
                "put_wall": lv.get("put_wall"),
                "gamma_flip": lv.get("gamma_flip"),
                "max_pain": lv.get("highest_oi_strike"),
            }
    except Exception:  # noqa: BLE001
        pass

    return {
        "symbol": ticker,
        "underlying_price": spot,
        "window_minutes": 240,
        "expiry": None,
        "chain": chain,
        "count": len(signals),
        "signals": signals,
        "source": "yfinance_unusual_options",
    }


@router.get("/flashalpha/signals/{ticker}")
async def fa_signals(
    ticker: str,
    min_score: int = Query(default=70),
    intent: Optional[str] = Query(default=None),
    structure: Optional[str] = Query(default=None),
    window_minutes: int = Query(default=240),
) -> dict[str, Any]:
    """Top scored flow signals as a Hebrew chat-ready message."""
    raw = await fa_signals_raw(
        ticker=ticker,
        min_score=min_score,
        structure=structure,
        window_minutes=window_minutes,
        limit=25,
    )
    signals = raw.get("signals") or []
    spot = raw.get("underlying_price") or 0
    chain = raw.get("chain") or {}

    if not signals:
        return {
            "symbol": ticker,
            "count": 0,
            "signals": [],
            "chain": chain,
            "formatted_message": f"לא נמצאו סיגנלים חזקים ל-{ticker}",
            "source": raw.get("source", ""),
        }

    lines = [
        f"🐋 Flow Signals – {ticker}",
        f"💰 ספוט: ${spot:,.2f}",
        "━━━━━━━━━━━━━",
    ]
    intent_emoji = {"bullish": "🟢", "bearish": "🔴"}
    for i, sig in enumerate(signals[:8], start=1):
        right = "Call" if sig.get("right") == "C" else "Put"
        ie = intent_emoji.get(sig.get("intent", ""), "🟡")
        tags = sig.get("tags") or []
        whale = "🐋" if "whale" in tags else ""
        struct_e = "⚡" if sig.get("structure") == "sweep" else "🏦"
        lines.append(
            f"\n{i}. {struct_e}{whale} {ie} {right} ${sig.get('strike', 0):,.0f}\n"
            f"   פרמיה: ${(sig.get('premium') or 0):,.0f} | "
            f"ניקוד: {sig.get('score', 0)}/100\n"
            f"   {sig.get('expiry', '?')} ({sig.get('dte', 0)}d)"
        )

    return {
        "symbol": ticker,
        "spot_price": spot,
        "count": len(signals),
        "signals": signals,
        "chain": chain,
        "formatted_message": "\n".join(lines),
        "source": raw.get("source", ""),
    }


@router.get("/scanner/momentum")
async def scan_momentum(limit: int = 20) -> dict[str, Any]:
    """Live FinViz momentum scan with chart URLs + Perplexity news reasons."""
    try:
        from services.daily_stock_scanner import DailyStockScanner

        scanner = DailyStockScanner()
        return await scanner.scan_momentum_stocks(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("scanner/momentum failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/scanner/latest")
async def get_latest_scan() -> dict[str, Any]:
    """Return the most recently persisted momentum scan (cheap, no scraping)."""
    try:
        db = get_db()
        latest = await db.scanner_results.find_one(sort=[("created_at", -1)])
        if not latest:
            return {"stocks": [], "count": 0}
        latest["_id"] = str(latest["_id"])
        return latest
    except Exception as exc:  # noqa: BLE001
        logger.exception("scanner/latest failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/uw/market-tide")
async def get_uw_market_tide() -> dict[str, Any]:
    """Aggregate call/put premium tide from Unusual Whales."""
    from scrapers.uw_scraper import UnusualWhalesScraper
    scraper = UnusualWhalesScraper()
    try:
        return await scraper.get_market_tide()
    except Exception as exc:  # noqa: BLE001
        logger.exception("uw_market_tide failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await scraper.close()
