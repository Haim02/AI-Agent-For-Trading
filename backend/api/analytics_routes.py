"""Analytics endpoints used by the dashboard charts."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter

from db.connection import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ───────────────────────── helpers ─────────────────────────


def _pnl(doc: dict[str, Any]) -> float:
    val = doc.get("realized_pnl")
    if val is None:
        val = doc.get("pnl") or 0.0
    try:
        return float(val or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _date(doc: dict[str, Any]) -> Optional[datetime]:
    for key in ("closed_at", "exit_date", "close_date", "entry_date", "created_at"):
        value = doc.get(key)
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def _month_key(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m") if dt else None


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


async def _closed_positions() -> list[dict[str, Any]]:
    db = get_db()
    cursor = db.positions.find({"status": "closed"})
    return await cursor.to_list(length=2000)


# ───────────────────────── endpoints ─────────────────────────


@router.get("/performance")
async def performance() -> dict[str, Any]:
    docs = await _closed_positions()
    pnls = [_pnl(d) for d in docs]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = round(_safe_div(len(wins), len(pnls)) * 100.0, 2)
    profit_factor = round(_safe_div(sum(wins), abs(sum(losses))), 2)
    avg_win = round(_safe_div(sum(wins), len(wins)), 2)
    avg_loss = round(_safe_div(sum(losses), len(losses)), 2)

    # Equity curve for max drawdown calc
    sorted_docs = sorted(docs, key=lambda d: _date(d) or datetime.min)
    equity: list[float] = []
    cumulative = 0.0
    for doc in sorted_docs:
        cumulative += _pnl(doc)
        equity.append(cumulative)
    peak = float("-inf")
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = value - peak
        max_dd = min(max_dd, drawdown)

    # Monthly best/worst
    by_month: dict[str, float] = defaultdict(float)
    for doc in docs:
        month = _month_key(_date(doc))
        if month:
            by_month[month] += _pnl(doc)
    best_month = max(by_month.items(), key=lambda kv: kv[1], default=None)
    worst_month = min(by_month.items(), key=lambda kv: kv[1], default=None)

    return {
        "total_pnl": round(total, 2),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_drawdown": round(max_dd, 2),
        "total_trades": len(pnls),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "best_month": {"month": best_month[0], "pnl": round(best_month[1], 2)} if best_month else None,
        "worst_month": {"month": worst_month[0], "pnl": round(worst_month[1], 2)} if worst_month else None,
    }


@router.get("/equity-curve")
async def equity_curve() -> list[dict[str, Any]]:
    docs = await _closed_positions()
    daily: dict[str, float] = defaultdict(float)
    for doc in docs:
        dt = _date(doc)
        if not dt:
            continue
        daily[dt.date().isoformat()] += _pnl(doc)
    sorted_days = sorted(daily.keys())
    cumulative = 0.0
    series: list[dict[str, Any]] = []
    for day in sorted_days:
        cumulative += daily[day]
        series.append(
            {
                "date": day,
                "daily_pnl": round(daily[day], 2),
                "cumulative_pnl": round(cumulative, 2),
            }
        )
    return series


@router.get("/by-strategy")
async def by_strategy() -> list[dict[str, Any]]:
    docs = await _closed_positions()
    buckets: dict[str, list[float]] = defaultdict(list)
    for doc in docs:
        strategy = doc.get("strategy") or "unknown"
        buckets[strategy].append(_pnl(doc))

    results: list[dict[str, Any]] = []
    for strategy, pnls in buckets.items():
        wins = [p for p in pnls if p > 0]
        results.append(
            {
                "strategy": strategy,
                "trades": len(pnls),
                "win_rate": round(_safe_div(len(wins), len(pnls)) * 100.0, 2),
                "avg_pnl": round(_safe_div(sum(pnls), len(pnls)), 2),
                "total_pnl": round(sum(pnls), 2),
            }
        )
    results.sort(key=lambda r: r["total_pnl"], reverse=True)
    return results


@router.get("/monthly")
async def monthly() -> list[dict[str, Any]]:
    docs = await _closed_positions()
    buckets: dict[str, list[float]] = defaultdict(list)
    for doc in docs:
        month = _month_key(_date(doc))
        if not month:
            continue
        buckets[month].append(_pnl(doc))
    out: list[dict[str, Any]] = []
    for month in sorted(buckets.keys()):
        pnls = buckets[month]
        wins = [p for p in pnls if p > 0]
        out.append(
            {
                "month": month,
                "pnl": round(sum(pnls), 2),
                "trades": len(pnls),
                "win_rate": round(_safe_div(len(wins), len(pnls)) * 100.0, 2),
            }
        )
    return out


@router.get("/heatmap")
async def heatmap() -> list[dict[str, Any]]:
    """P&L heatmap: rows = ISO week, columns = weekday (0=Sun..6=Sat)."""
    docs = await _closed_positions()
    cells: dict[tuple[int, int, int], float] = defaultdict(float)
    counts: dict[tuple[int, int, int], int] = defaultdict(int)
    for doc in docs:
        dt = _date(doc)
        if not dt:
            continue
        iso_year, iso_week, _ = dt.isocalendar()
        # Python: Monday=0 .. Sunday=6. We want Sunday=0..Saturday=6.
        weekday = (dt.weekday() + 1) % 7
        key = (iso_year, iso_week, weekday)
        cells[key] += _pnl(doc)
        counts[key] += 1

    out: list[dict[str, Any]] = []
    for (year, week, weekday), pnl in cells.items():
        out.append(
            {
                "year": year,
                "week": week,
                "weekday": weekday,
                "pnl": round(pnl, 2),
                "trades": counts[(year, week, weekday)],
            }
        )
    out.sort(key=lambda x: (x["year"], x["week"], x["weekday"]))
    return out


@router.get("/best-worst")
async def best_worst() -> dict[str, list[dict[str, Any]]]:
    docs = await _closed_positions()
    ordered = sorted(docs, key=_pnl, reverse=True)

    def _row(doc: dict[str, Any]) -> dict[str, Any]:
        entry = _date({"entry_date": doc.get("entry_date")})
        exit_dt = _date({"closed_at": doc.get("closed_at") or doc.get("exit_date")})
        dte = None
        if entry and exit_dt:
            dte = max((exit_dt - entry).days, 0)
        return {
            "id": str(doc.get("_id", "")),
            "ticker": doc.get("ticker"),
            "strategy": doc.get("strategy"),
            "entry_date": entry.date().isoformat() if entry else None,
            "exit_date": exit_dt.date().isoformat() if exit_dt else None,
            "pnl": round(_pnl(doc), 2),
            "dte": dte,
        }

    return {
        "best": [_row(d) for d in ordered[:5]],
        "worst": [_row(d) for d in ordered[-5:][::-1]],
    }


__all__ = ["router"]
