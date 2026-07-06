import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Iterable, Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

COLLECTIONS = (
    "user_profile",
    "trade_learnings",
    "market_patterns",
    "strategy_knowledge",
    "knowledge_base",
    "trading_library",  # NotebookLM course material (see memory/trading_library.py)
)

DEFAULT_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chromadb")
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_PROFILE_CATEGORIES = {"preference", "behavior", "goal", "risk_tolerance"}


class LongTermMemory:
    """Semantic memory backed by ChromaDB + sentence-transformers."""

    _client: Optional[chromadb.api.ClientAPI] = None
    _lock = Lock()

    def __init__(
        self,
        persist_directory: str = DEFAULT_PERSIST_DIR,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        self.persist_directory = persist_directory
        self.embedding_model = embedding_model
        try:
            self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
        except (ImportError, ValueError) as exc:
            logger.warning(
                "SentenceTransformer unavailable (%s) – falling back to Chroma's default embedder",
                exc,
            )
            self._ef = embedding_functions.DefaultEmbeddingFunction()

        os.makedirs(self.persist_directory, exist_ok=True)
        with self._lock:
            if LongTermMemory._client is None:
                LongTermMemory._client = chromadb.PersistentClient(
                    path=self.persist_directory,
                    settings=Settings(anonymized_telemetry=False, allow_reset=False),
                )
        self.client = LongTermMemory._client
        self._collections: dict[str, Any] = {}
        for name in COLLECTIONS:
            self._collections[name] = self.client.get_or_create_collection(
                name=name, embedding_function=self._ef
            )

    # ───────────────────────── internals ─────────────────────────

    def _coll(self, name: str):
        if name not in self._collections:
            raise KeyError(f"Unknown collection: {name}")
        return self._collections[name]

    @staticmethod
    def _now_iso() -> str:
        return datetime.utcnow().isoformat()

    @staticmethod
    def _flatten_metadata(meta: dict[str, Any]) -> dict[str, Any]:
        """Chroma metadata only accepts str/int/float/bool — serialize anything else."""
        flat: dict[str, Any] = {}
        for k, v in meta.items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                flat[k] = v
            else:
                flat[k] = json.dumps(v, ensure_ascii=False, default=str)
        return flat

    def _add(self, collection: str, content: str, metadata: dict[str, Any]) -> str:
        doc_id = str(uuid.uuid4())
        meta = self._flatten_metadata({**metadata, "created_at": self._now_iso()})
        self._coll(collection).add(documents=[content], metadatas=[meta], ids=[doc_id])
        return doc_id

    # ───────────────────────── public — writes ─────────────────────────

    def save_user_fact(self, fact: str, category: str) -> str:
        category = category.lower().strip()
        if category not in _PROFILE_CATEGORIES:
            logger.warning("Unknown user fact category %r — saving anyway", category)
        return self._add(
            "user_profile",
            fact,
            {"category": category, "kind": "user_fact"},
        )

    def save_trade_learning(self, learning: str, context: dict[str, Any]) -> str:
        meta = {
            "kind": "trade_learning",
            "ticker": (context or {}).get("ticker"),
            "strategy": (context or {}).get("strategy"),
            "outcome": (context or {}).get("outcome"),
            "market_conditions": (context or {}).get("market_conditions"),
        }
        return self._add("trade_learnings", learning, meta)

    def save_market_pattern(self, pattern: str, conditions: dict[str, Any]) -> str:
        meta = {"kind": "market_pattern", **(conditions or {})}
        return self._add("market_patterns", pattern, meta)

    def save_strategy_knowledge(self, knowledge: str, context: dict[str, Any]) -> str:
        meta = {"kind": "strategy_knowledge", **(context or {})}
        return self._add("strategy_knowledge", knowledge, meta)

    def save_knowledge(self, content: str, metadata: Optional[dict[str, Any]] = None) -> str:
        return self._add("knowledge_base", content, metadata or {"kind": "user_taught"})

    def update_user_profile(self, new_info: str, category: str = "preference") -> str:
        """Append new fact about Haim. Existing facts are not removed — recency wins via timestamp."""
        return self.save_user_fact(new_info, category)

    # ───────────────────────── public — reads ─────────────────────────

    def recall(self, query: str, collection: str, n_results: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        try:
            result = self._coll(collection).query(
                query_texts=[query],
                n_results=max(1, n_results),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Chroma query failed for collection=%s", collection)
            return []

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        ids = (result.get("ids") or [[]])[0]

        items: list[dict[str, Any]] = []
        for doc, meta, dist, doc_id in zip(documents, metadatas, distances, ids):
            score = 1.0 / (1.0 + float(dist)) if dist is not None else None
            items.append(
                {
                    "id": doc_id,
                    "content": doc,
                    "metadata": meta or {},
                    "relevance_score": score,
                }
            )
        return items

    def recall_all(self, query: str, n_results: int = 3) -> dict[str, list[dict[str, Any]]]:
        return {
            name: self.recall(query, name, n_results=n_results)
            for name in (
                "user_profile",
                "trade_learnings",
                "market_patterns",
                "strategy_knowledge",
            )
        }

    def list_collection(
        self,
        collection: str,
        limit: int = 200,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        try:
            kwargs: dict[str, Any] = {"limit": limit, "include": ["documents", "metadatas"]}
            if where:
                kwargs["where"] = where
            result = self._coll(collection).get(**kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("Chroma get failed for collection=%s", collection)
            return []

        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        ids = result.get("ids") or []
        items = [
            {"id": _id, "content": d, "metadata": m or {}}
            for _id, d, m in zip(ids, docs, metas)
        ]
        items.sort(
            key=lambda it: it["metadata"].get("created_at", ""),
            reverse=True,
        )
        return items

    # ───────────────────────── public — maintenance ─────────────────────────

    def delete_raw_data(self, older_than_days: int = 7) -> dict[str, int]:
        """Delete raw market_patterns/knowledge_base items older than X days.
        Keeps distilled learnings (trade_learnings, user_profile, strategy_knowledge).
        """
        cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
        deleted: dict[str, int] = {}
        for name in ("market_patterns", "knowledge_base"):
            coll = self._coll(name)
            try:
                result = coll.get(
                    where={
                        "$and": [
                            {"kind": "raw"},
                            {"created_at": {"$lt": cutoff}},
                        ]
                    },
                    include=[],
                )
                ids: Iterable[str] = result.get("ids") or []
                ids = list(ids)
                if ids:
                    coll.delete(ids=ids)
                deleted[name] = len(ids)
            except Exception:  # noqa: BLE001
                logger.exception("delete_raw_data failed for %s", name)
                deleted[name] = 0
        logger.info("LongTermMemory raw-data purge: %s", deleted)
        return deleted

    def stats(self) -> dict[str, int]:
        return {name: self._coll(name).count() for name in COLLECTIONS}
