"""Pre-trade checklist endpoint.

Runs six independent checks (VIX, GEX regime, strikes vs walls, IV rank, DTE,
earnings) and returns a normalized 0-100 score plus per-check verdicts in Hebrew.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/checklist/{ticker}")
async def run_pretrade_checklist(
    ticker: str,
    strategy: str = "iron_condor",
    short_call: Optional[float] = Query(default=None),
    short_put: Optional[float] = Query(default=None),
    credit: Optional[float] = Query(default=None),
    dte: int = 45,
) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    checks: list[dict[str, Any]] = []
    score = 0
    max_score = 0

    # ───────────────────────── 1. VIX ─────────────────────────
    max_score += 20
    vix_val: Optional[float] = None
    try:
        def _fetch_vix() -> Optional[float]:
            import yfinance as yf
            h = yf.Ticker("^VIX").history(period="1d")
            return float(h["Close"].iloc[-1]) if not h.empty else None

        vix_val = await asyncio.to_thread(_fetch_vix)
    except Exception:  # noqa: BLE001
        logger.exception("checklist: VIX lookup failed")

    if vix_val is None:
        checks.append(
            {"id": "vix", "name": "VIX Level", "status": "warning",
             "value": "N/A", "message": "לא ניתן לבדוק VIX", "points": 10}
        )
        score += 10
    elif vix_val < 15:
        checks.append(
            {"id": "vix", "name": "VIX Level", "status": "warning",
             "value": f"{vix_val:.1f}",
             "message": f"VIX נמוך ({vix_val:.1f}) – פרמיה נמוכה. שקול לדלג.",
             "points": 10}
        )
        score += 10
    elif vix_val <= 30:
        checks.append(
            {"id": "vix", "name": "VIX Level", "status": "pass",
             "value": f"{vix_val:.1f}",
             "message": f"VIX תקין ({vix_val:.1f}) – תנאים טובים.",
             "points": 20}
        )
        score += 20
    else:
        checks.append(
            {"id": "vix", "name": "VIX Level", "status": "fail",
             "value": f"{vix_val:.1f}",
             "message": f"VIX גבוה מדי ({vix_val:.1f}) – סיכון גבוה!",
             "points": 0}
        )

    # ───────────────────────── 2. GEX Regime ─────────────────────────
    max_score += 25
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
    try:
        from analytics.gex_engine import GEXEngine
        gex_data = await GEXEngine().get_full_gex_analysis(ticker)
        regime = gex_data.get("regime") or "unknown"
        call_wall = gex_data.get("call_wall")
        put_wall = gex_data.get("put_wall")
        is_credit = strategy in {"iron_condor", "bull_put", "bear_call", "short_strangle"}

        if regime == "positive" and is_credit:
            checks.append(
                {"id": "gex_regime", "name": "GEX Regime", "status": "pass",
                 "value": "POSITIVE",
                 "message": "Positive Gamma – תנאים אידיאליים למכירת פרמיה!",
                 "points": 25}
            )
            score += 25
        elif regime == "negative" and is_credit:
            checks.append(
                {"id": "gex_regime", "name": "GEX Regime", "status": "fail",
                 "value": "NEGATIVE",
                 "message": "Negative Gamma – אל תמכור פרמיה! סיכון גבוה.",
                 "points": 0}
            )
        else:
            checks.append(
                {"id": "gex_regime", "name": "GEX Regime", "status": "warning",
                 "value": regime.upper(),
                 "message": f"Regime: {regime} – בדוק התאמה לאסטרטגיה.",
                 "points": 12}
            )
            score += 12
    except Exception:  # noqa: BLE001
        logger.exception("checklist: GEX lookup failed")
        checks.append(
            {"id": "gex_regime", "name": "GEX Regime", "status": "warning",
             "value": "N/A", "message": "לא ניתן לבדוק GEX", "points": 12}
        )
        score += 12

    # ───────────────────────── 3. Strikes vs Walls ─────────────────────────
    max_score += 20
    if short_call and short_put and call_wall and put_wall:
        call_ok = short_call >= call_wall
        put_ok = short_put <= put_wall
        if call_ok and put_ok:
            checks.append(
                {"id": "strikes_vs_walls", "name": "Strikes vs GEX Walls", "status": "pass",
                 "value": f"C:{short_call} P:{short_put}",
                 "message": "Strikes מחוץ ל-Walls – GEX מגן על הפוזיציה!",
                 "points": 20}
            )
            score += 20
        elif not call_ok and not put_ok:
            checks.append(
                {"id": "strikes_vs_walls", "name": "Strikes vs GEX Walls", "status": "fail",
                 "value": f"C:{short_call} P:{short_put}",
                 "message": f"Strikes בתוך ה-Walls! Call Wall:{call_wall} Put Wall:{put_wall}",
                 "points": 0}
            )
        else:
            checks.append(
                {"id": "strikes_vs_walls", "name": "Strikes vs GEX Walls", "status": "warning",
                 "value": f"C:{short_call} P:{short_put}",
                 "message": "Strike אחד בתוך ה-Wall. שקול להזיז.",
                 "points": 10}
            )
            score += 10
    else:
        checks.append(
            {"id": "strikes_vs_walls", "name": "Strikes vs GEX Walls", "status": "info",
             "value": "—", "message": "הכנס Strikes לבדיקה", "points": 10}
        )
        score += 10

    # ───────────────────────── 4. IV Rank ─────────────────────────
    max_score += 20
    iv_rank: float = 50.0
    try:
        from analytics.iv_rank_calculator import IVRankCalculator
        result = await asyncio.to_thread(IVRankCalculator().calculate_iv_rank, ticker)
        if result is not None:
            iv_rank = float(result.iv_rank)
    except Exception:  # noqa: BLE001
        logger.exception("checklist: IV rank lookup failed")

    if iv_rank >= 50:
        checks.append(
            {"id": "iv_rank", "name": "IV Rank", "status": "pass",
             "value": f"{iv_rank:.0f}%",
             "message": f"IV Rank {iv_rank:.0f}% – פרמיה גבוהה, תנאים טובים!",
             "points": 20}
        )
        score += 20
    elif iv_rank >= 30:
        checks.append(
            {"id": "iv_rank", "name": "IV Rank", "status": "warning",
             "value": f"{iv_rank:.0f}%",
             "message": f"IV Rank {iv_rank:.0f}% – סביר אבל לא אידיאלי.",
             "points": 10}
        )
        score += 10
    else:
        checks.append(
            {"id": "iv_rank", "name": "IV Rank", "status": "fail",
             "value": f"{iv_rank:.0f}%",
             "message": f"IV Rank {iv_rank:.0f}% – נמוך מדי למכירת פרמיה.",
             "points": 0}
        )

    # ───────────────────────── 5. DTE ─────────────────────────
    max_score += 10
    if 21 <= dte <= 60:
        checks.append(
            {"id": "dte", "name": "DTE", "status": "pass",
             "value": f"{dte} ימים", "message": f"{dte} DTE – Tastytrade zone!",
             "points": 10}
        )
        score += 10
    elif dte < 21:
        checks.append(
            {"id": "dte", "name": "DTE", "status": "fail",
             "value": f"{dte} ימים", "message": f"{dte} DTE – קצר מדי, Gamma Risk!",
             "points": 0}
        )
    else:
        checks.append(
            {"id": "dte", "name": "DTE", "status": "warning",
             "value": f"{dte} ימים", "message": f"{dte} DTE – ארוך, בדוק IV.",
             "points": 5}
        )
        score += 5

    # ───────────────────────── 6. Earnings ─────────────────────────
    max_score += 5
    try:
        def _has_upcoming_earnings() -> bool:
            import yfinance as yf
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None:
                return False
            if hasattr(cal, "empty"):
                return not cal.empty
            return bool(cal)

        upcoming = await asyncio.to_thread(_has_upcoming_earnings)
        if upcoming:
            checks.append(
                {"id": "earnings", "name": "Earnings", "status": "warning",
                 "value": "יש Earnings!",
                 "message": "יש Earnings בקרוב – IV עלולה לקפוץ/לצנוח!",
                 "points": 0}
            )
        else:
            checks.append(
                {"id": "earnings", "name": "Earnings", "status": "pass",
                 "value": "נקי", "message": "אין Earnings בקרוב.", "points": 5}
            )
            score += 5
    except Exception:  # noqa: BLE001
        logger.exception("checklist: earnings lookup failed")
        checks.append(
            {"id": "earnings", "name": "Earnings", "status": "info",
             "value": "—", "message": "בדוק Earnings ידנית.", "points": 3}
        )
        score += 3

    # ───────────────────────── verdict ─────────────────────────
    score_pct = round(score / max_score * 100) if max_score else 0
    if score_pct >= 80:
        verdict = "go"
        verdict_text = "✅ GO – כנס לעסקה!"
        verdict_color = "green"
    elif score_pct >= 60:
        verdict = "caution"
        verdict_text = "⚠️ זהירות – שקול שנית"
        verdict_color = "yellow"
    else:
        verdict = "no_go"
        verdict_text = "❌ NO GO – אל תיכנס!"
        verdict_color = "red"

    return {
        "ticker": ticker,
        "strategy": strategy,
        "checks": checks,
        "score": score_pct,
        "verdict": verdict,
        "verdict_text": verdict_text,
        "verdict_color": verdict_color,
        "timestamp": datetime.utcnow().isoformat(),
    }
