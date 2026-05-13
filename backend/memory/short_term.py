import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from db.connection import get_db

logger = logging.getLogger(__name__)

SESSIONS = "conversation_sessions"
MESSAGES = "conversations"
DEFAULT_TTL_DAYS = 30


class ShortTermMemory:
    """Per-session chat history backed by MongoDB."""

    def __init__(self, ttl_days: int = DEFAULT_TTL_DAYS) -> None:
        self.ttl_days = ttl_days

    @property
    def db(self):
        return get_db()

    # ───────────────────────── sessions ─────────────────────────

    async def get_or_create_session(self, session_id: str) -> dict[str, Any]:
        now = datetime.utcnow()
        existing = await self.db[SESSIONS].find_one({"session_id": session_id})
        if existing:
            await self.db[SESSIONS].update_one(
                {"session_id": session_id},
                {"$set": {"last_active": now}},
            )
            return existing

        doc = {
            "session_id": session_id,
            "started_at": now,
            "last_active": now,
            "message_count": 0,
            "topics": [],
            "expires_at": now + timedelta(days=self.ttl_days),
        }
        await self.db[SESSIONS].insert_one(doc)
        return doc

    # ───────────────────────── messages ─────────────────────────

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if role not in ("user", "assistant"):
            raise ValueError(f"Invalid role: {role}")

        now = datetime.utcnow()
        await self.get_or_create_session(session_id)

        await self.db[MESSAGES].insert_one(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "timestamp": now,
                "metadata": metadata or {},
            }
        )
        await self.db[SESSIONS].update_one(
            {"session_id": session_id},
            {"$set": {"last_active": now}, "$inc": {"message_count": 1}},
        )

    async def get_history(self, session_id: str, last_n: int = 20) -> list[dict[str, Any]]:
        cursor = (
            self.db[MESSAGES]
            .find({"session_id": session_id}, {"_id": 0, "role": 1, "content": 1, "timestamp": 1})
            .sort("timestamp", -1)
            .limit(last_n)
        )
        docs = await cursor.to_list(length=last_n)
        docs.reverse()
        return docs

    # ───────────────────────── maintenance ─────────────────────────

    async def cleanup_expired(self) -> dict[str, int]:
        cutoff = datetime.utcnow()
        expired = await self.db[SESSIONS].find(
            {"expires_at": {"$lte": cutoff}}, {"session_id": 1}
        ).to_list(length=10000)
        ids = [doc["session_id"] for doc in expired]
        if not ids:
            return {"sessions_deleted": 0, "messages_deleted": 0}

        msgs = await self.db[MESSAGES].delete_many({"session_id": {"$in": ids}})
        sess = await self.db[SESSIONS].delete_many({"session_id": {"$in": ids}})
        logger.info("ShortTermMemory cleanup: %d sessions / %d messages", sess.deleted_count, msgs.deleted_count)
        return {
            "sessions_deleted": sess.deleted_count,
            "messages_deleted": msgs.deleted_count,
        }

    # ───────────────────────── summary ─────────────────────────

    async def summarize_session(self, session_id: str) -> dict[str, Any]:
        msgs = await self.db[MESSAGES].find(
            {"session_id": session_id},
            {"_id": 0, "role": 1, "content": 1, "metadata": 1, "timestamp": 1},
        ).sort("timestamp", 1).to_list(length=2000)

        topics: set[str] = set()
        tickers: set[str] = set()
        decisions: list[str] = []

        for m in msgs:
            meta = m.get("metadata") or {}
            for t in meta.get("topics", []) or []:
                topics.add(str(t))
            for tk in meta.get("tickers", []) or []:
                tickers.add(str(tk).upper())
            if meta.get("decision"):
                decisions.append(str(meta["decision"]))

            content = m.get("content") or ""
            if m.get("role") == "assistant" and any(
                kw in content for kw in ("המלצה", "מומלץ", "אני מציע", "כדאי")
            ):
                # Take the first ~140 chars as a decision marker for downstream reflection.
                decisions.append(content.strip().split("\n")[0][:140])

        return {
            "session_id": session_id,
            "topics": sorted(topics),
            "tickers_discussed": sorted(tickers),
            "decisions_made": decisions[:20],
            "message_count": len(msgs),
        }
