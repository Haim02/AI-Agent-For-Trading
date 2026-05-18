"""Weekly P&L report.

Runs every Sunday at 05:00 UTC (08:00 Israel). Aggregates the last 7 days of
closed positions, computes wins/losses/strategy breakdown vs. the $1k weekly
target, and sends a Hebrew Telegram digest.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import pytz

from db.connection import get_db
from services.telegram_service import TelegramService, TelegramServiceError
from utils.time_helper import ISRAEL_TZ

logger = logging.getLogger(__name__)


class WeeklyReportService:
    """Build + ship the Sunday-morning performance digest."""

    WEEKLY_TARGET = 1000  # USD

    def __init__(self, telegram: Optional[TelegramService] = None) -> None:
        self.db = get_db()
        if telegram is not None:
            self._telegram: Optional[TelegramService] = telegram
        else:
            try:
                self._telegram = TelegramService()
            except TelegramServiceError as exc:
                logger.warning("Telegram disabled in WeeklyReportService: %s", exc)
                self._telegram = None

    async def generate_weekly_report(self) -> dict[str, Any]:
        now_il = datetime.now(ISRAEL_TZ)
        week_start_il = now_il - timedelta(days=7)
        week_start_utc = week_start_il.astimezone(pytz.UTC)

        logger.info("📊 יוצר דוח שבועי...")

        closed = await self.db.positions.find(
            {"status": "closed", "close_date": {"$gte": week_start_utc}}
        ).to_list(length=200)
        open_pos = await self.db.positions.find({"status": "open"}).to_list(length=200)

        total_trades = len(closed)
        winners = [p for p in closed if (p.get("realized_pnl") or 0) > 0]
        losers = [p for p in closed if (p.get("realized_pnl") or 0) <= 0]
        total_pnl = sum(p.get("realized_pnl") or 0 for p in closed)
        total_unrealized = sum(p.get("unrealized_pnl") or 0 for p in open_pos)
        win_rate = (len(winners) / total_trades * 100) if total_trades else 0.0
        avg_win = (
            sum(p.get("realized_pnl") or 0 for p in winners) / len(winners)
            if winners else 0.0
        )
        avg_loss = (
            sum(p.get("realized_pnl") or 0 for p in losers) / len(losers)
            if losers else 0.0
        )

        best = max(closed, key=lambda p: p.get("realized_pnl") or 0, default=None)
        worst = min(closed, key=lambda p: p.get("realized_pnl") or 0, default=None)

        by_strategy: dict[str, dict[str, float]] = {}
        for p in closed:
            s = p.get("strategy") or "unknown"
            bucket = by_strategy.setdefault(s, {"count": 0, "pnl": 0.0})
            bucket["count"] += 1
            bucket["pnl"] += p.get("realized_pnl") or 0

        target_pct = (total_pnl / self.WEEKLY_TARGET * 100) if self.WEEKLY_TARGET else 0
        hit_target = total_pnl >= self.WEEKLY_TARGET

        report = self._format_report(
            week_start=week_start_il,
            now_il=now_il,
            total_trades=total_trades,
            winners=len(winners),
            losers=len(losers),
            total_pnl=total_pnl,
            total_unrealized=total_unrealized,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            best=best,
            worst=worst,
            by_strategy=by_strategy,
            open_count=len(open_pos),
            target=self.WEEKLY_TARGET,
            target_pct=target_pct,
            hit_target=hit_target,
        )

        if self._telegram is not None:
            try:
                await self._telegram.send_alert(
                    title="📊 דוח שבועי",
                    body=report,
                    urgency="high",
                )
            except Exception:  # noqa: BLE001
                logger.exception("weekly_report: telegram failed")

        try:
            await self.db.weekly_reports.insert_one(
                {
                    "week_start": week_start_il,
                    "week_end": now_il,
                    "total_pnl": total_pnl,
                    "total_trades": total_trades,
                    "win_rate": win_rate,
                    "hit_target": hit_target,
                    "report_text": report,
                    "created_at": datetime.utcnow(),
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("weekly_report: db insert failed")

        logger.info("✅ דוח שבועי נשלח: P&L $%.0f", total_pnl)
        return {"total_pnl": total_pnl, "trades": total_trades, "win_rate": win_rate}

    def _format_report(self, **k: Any) -> str:
        target_emoji = "🎯✅" if k["hit_target"] else "🎯❌"
        pnl_emoji = "🟢" if k["total_pnl"] >= 0 else "🔴"

        strat_lines: list[str] = []
        for s, d in sorted(k["by_strategy"].items(), key=lambda x: x[1]["pnl"], reverse=True):
            emoji = "🟢" if d["pnl"] >= 0 else "🔴"
            strat_lines.append(f"{emoji} {s}: {int(d['count'])} עסקות | ${d['pnl']:+.0f}")
        strat_text = ("\n" + "\n".join(strat_lines)) if strat_lines else " —"

        best_str = (
            f"{k['best'].get('ticker', '?')} +${k['best'].get('realized_pnl', 0):.0f}"
            if k["best"] else "—"
        )
        worst_str = (
            f"{k['worst'].get('ticker', '?')} ${k['worst'].get('realized_pnl', 0):.0f}"
            if k["worst"] else "—"
        )

        return (
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 דוח שבועי – סיכום מסחר\n"
            f"{k['week_start'].strftime('%d/%m')} – {k['now_il'].strftime('%d/%m/%Y')}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{pnl_emoji} רווח שבועי: ${k['total_pnl']:+.0f}\n"
            f"{target_emoji} יעד $1,000: {k['target_pct']:.0f}%\n\n"
            f"📈 עסקות: {k['total_trades']}\n"
            f"✅ זכיות: {k['winners']} | ❌ הפסדים: {k['losers']}\n"
            f"🎯 Win Rate: {k['win_rate']:.0f}%\n\n"
            f"💰 ממוצע זכייה: ${k['avg_win']:+.0f}\n"
            f"💸 ממוצע הפסד: ${k['avg_loss']:+.0f}\n\n"
            f"🏆 הטובה: {best_str}\n"
            f"💀 הגרועה: {worst_str}\n\n"
            f"📂 לפי אסטרטגיה:{strat_text}\n\n"
            f"📌 פוזיציות פתוחות: {k['open_count']}\n"
            f"💼 Unrealized: ${k['total_unrealized']:+.0f}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        )


__all__ = ["WeeklyReportService"]
