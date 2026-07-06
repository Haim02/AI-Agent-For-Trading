"""Trading knowledge library — NotebookLM material indexed for RAG.

Markdown files extracted from Haim's NotebookLM notebooks (courses, guides,
video transcripts on GEX / DEX / order flow / 0DTE) live under
``data/notebooklm/``. This module chunks and indexes them into a dedicated
ChromaDB collection so the agent can quote real course material when
answering, and exposes a semantic ``query`` used by both ``perceive_node``
and the ``search_knowledge`` agent tool.

Re-syncs are cheap: a fingerprint of (path, size, mtime) is kept next to the
data; unchanged libraries are skipped at startup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from memory.long_term import LongTermMemory

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("NOTEBOOKLM_DIR", "./data/notebooklm"))
COLLECTION = "trading_library"
SOURCE_TAG = "notebooklm"
CHUNK_SIZE = 1400
CHUNK_OVERLAP = 200
FINGERPRINT_FILE = ".ingested.json"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", flags=re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[match.end():]


def _chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Paragraph-aware chunking with overlap between adjacent chunks."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        # Hard-split single paragraphs that exceed the chunk size.
        while len(para) > size:
            head, para = para[:size], para[size - overlap:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if len(current) + len(para) + 2 > size and current:
            chunks.append(current)
            current = current[-overlap:] + "\n\n" + para if overlap else para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if len(c.strip()) > 80]


class TradingLibrary:
    """Chunked semantic index over the extracted NotebookLM material."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir or DATA_DIR)
        self.ltm = LongTermMemory()
        self.collection = self.ltm._coll(COLLECTION)

    # ───────────────────────── ingest ─────────────────────────

    def _fingerprint(self) -> str:
        parts = []
        for path in sorted(self.data_dir.rglob("*.md")):
            stat = path.stat()
            parts.append(f"{path.relative_to(self.data_dir)}|{stat.st_size}|{int(stat.st_mtime)}")
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()

    def _load_sync(self, force: bool = False) -> int:
        if not self.data_dir.exists():
            logger.info("TradingLibrary: %s not found – nothing to index", self.data_dir)
            return 0

        files = sorted(self.data_dir.rglob("*.md"))
        if not files:
            logger.info("TradingLibrary: no markdown files under %s", self.data_dir)
            return 0

        fp_path = self.data_dir / FINGERPRINT_FILE
        fingerprint = self._fingerprint()
        if not force and fp_path.exists():
            try:
                if json.loads(fp_path.read_text())["fingerprint"] == fingerprint:
                    logger.info("TradingLibrary: unchanged (%d files) – skipping re-index", len(files))
                    return -1
            except Exception:  # noqa: BLE001
                pass

        total = 0
        for path in files:
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                logger.exception("TradingLibrary: cannot read %s", path)
                continue
            meta, body = _parse_frontmatter(raw)
            chunks = _chunk(body)
            if not chunks:
                continue

            rel = str(path.relative_to(self.data_dir))
            base_id = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
            ids = [f"nblm_{base_id}_{i:03d}" for i in range(len(chunks))]
            metadatas = [
                {
                    "source": SOURCE_TAG,
                    "notebook": meta.get("notebook", path.parent.name),
                    "doc_title": meta.get("source", path.stem),
                    "doc_type": meta.get("type", "document"),
                    "url": meta.get("url", ""),
                    "file": rel,
                    "chunk": i,
                }
                for i in range(len(chunks))
            ]
            try:
                self.collection.upsert(documents=chunks, metadatas=metadatas, ids=ids)
                total += len(chunks)
            except Exception:  # noqa: BLE001
                logger.exception("TradingLibrary: upsert failed for %s", rel)

        try:
            fp_path.write_text(json.dumps({"fingerprint": fingerprint, "chunks": total}))
        except OSError:
            pass
        logger.info("📚 TradingLibrary: indexed %d chunks from %d files", total, len(files))
        return total

    async def load_all(self, force: bool = False) -> int:
        """Index (or re-index) the library. Returns chunk count, -1 if skipped."""
        return await asyncio.to_thread(self._load_sync, force)

    # ───────────────────────── query ─────────────────────────

    def query(self, query: str, n_results: int = 4) -> list[dict[str, Any]]:
        if not query or not query.strip():
            return []
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=max(1, n_results),
                where={"source": SOURCE_TAG},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("TradingLibrary query failed: %s", exc)
            return []

        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        return [
            {
                "content": doc,
                "notebook": (meta or {}).get("notebook"),
                "doc_title": (meta or {}).get("doc_title"),
                "url": (meta or {}).get("url"),
            }
            for doc, meta in zip(docs, metas)
        ]

    def stats(self) -> dict[str, Any]:
        try:
            return {"chunks": self.collection.count()}
        except Exception:  # noqa: BLE001
            return {"chunks": 0}


__all__ = ["TradingLibrary"]
