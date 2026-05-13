import logging
from datetime import datetime
from typing import Any, Optional

from db.connection import get_db
from memory.long_term import LongTermMemory
from memory.short_term import ShortTermMemory

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Assembles a rich Hebrew context block to prepend to the system prompt."""

    def __init__(
        self,
        short_term: Optional[ShortTermMemory] = None,
        long_term: Optional[LongTermMemory] = None,
    ) -> None:
        self.short_term = short_term or ShortTermMemory()
        self.long_term = long_term or LongTermMemory()

    async def build_context(self, user_message: str, session_id: str) -> str:
        recall = self._safe_recall_all(user_message)
        profile_facts = self._safe_recall_collection("חיים העדפות פרופיל", "user_profile", 8)

        recent = await self.short_term.get_history(session_id, last_n=10)
        market = await self._market_state()
        positions = await self._open_positions()

        parts: list[str] = []

        parts.append("=== מה אני יודע על חיים ===")
        if profile_facts:
            parts.extend(f"- {item['content']}" for item in profile_facts)
        else:
            parts.append("(אין עדיין עובדות שמורות בפרופיל)")

        parts.append("\n=== היסטוריה רלוונטית ===")
        learnings = recall.get("trade_learnings") or []
        if learnings:
            for item in learnings:
                meta = item.get("metadata") or {}
                tag = " | ".join(
                    str(v) for v in (meta.get("ticker"), meta.get("strategy"), meta.get("outcome")) if v
                )
                prefix = f"[{tag}] " if tag else ""
                parts.append(f"- {prefix}{item['content']}")
        else:
            parts.append("(אין עדיין לקחים שמורים)")

        parts.append("\n=== דפוסי שוק רלוונטיים ===")
        patterns = recall.get("market_patterns") or []
        if patterns:
            parts.extend(f"- {item['content']}" for item in patterns)
        else:
            parts.append("(אין עדיין דפוסים שמורים)")

        strategy_knowledge = recall.get("strategy_knowledge") or []
        if strategy_knowledge:
            parts.append("\n=== ידע אסטרטגי רלוונטי ===")
            parts.extend(f"- {item['content']}" for item in strategy_knowledge)

        parts.append("\n=== מצב שוק נוכחי ===")
        if market:
            for k, v in market.items():
                parts.append(f"- {k}: {v}")
        else:
            parts.append("(אין נתוני שוק זמינים)")

        parts.append("\n=== פוזיציות פתוחות כרגע ===")
        if positions:
            for p in positions:
                ticker = p.get("ticker", "—")
                strategy = p.get("strategy", "—")
                exp = p.get("expiration_date")
                exp_s = exp.date().isoformat() if hasattr(exp, "date") else str(exp or "—")
                parts.append(f"- {ticker} | {strategy} | תפוגה {exp_s}")
        else:
            parts.append("(אין פוזיציות פתוחות)")

        parts.append("\n=== שיחה אחרונה ===")
        if recent:
            for msg in recent:
                role = "חיים" if msg.get("role") == "user" else "סוכן"
                text = (msg.get("content") or "").strip().replace("\n", " ")
                if len(text) > 280:
                    text = text[:280] + "…"
                parts.append(f"{role}: {text}")
        else:
            parts.append("(אין היסטוריית שיחה זמינה)")

        return "\n".join(parts)

    # ───────────────────────── internals ─────────────────────────

    def _safe_recall_all(self, query: str) -> dict[str, list[dict[str, Any]]]:
        try:
            return self.long_term.recall_all(query, n_results=3)
        except Exception:  # noqa: BLE001
            logger.exception("recall_all failed")
            return {}

    def _safe_recall_collection(self, query: str, collection: str, n: int) -> list[dict[str, Any]]:
        try:
            return self.long_term.recall(query, collection, n_results=n)
        except Exception:  # noqa: BLE001
            logger.exception("recall failed for %s", collection)
            return []

    async def _market_state(self) -> dict[str, Any]:
        db = get_db()
        today = datetime.utcnow().date().isoformat()
        journal = await db.journal.find_one(
            {"date": today},
            {"_id": 0, "vix_open": 1, "vix_close": 1, "gex_regime": 1, "gamma_flip_level": 1, "spx_change_pct": 1},
        )
        if journal:
            return {k: v for k, v in journal.items() if v is not None}

        last = await db.journal.find_one(
            sort=[("date", -1)],
            projection={"_id": 0, "date": 1, "vix_close": 1, "gex_regime": 1, "gamma_flip_level": 1},
        )
        return {k: v for k, v in (last or {}).items() if v is not None}

    async def _open_positions(self) -> list[dict[str, Any]]:
        db = get_db()
        cursor = db.positions.find(
            {"status": "open"},
            {"_id": 0, "ticker": 1, "strategy": 1, "expiration_date": 1, "premium_received": 1},
        ).sort("entry_date", -1).limit(20)
        return await cursor.to_list(length=20)
