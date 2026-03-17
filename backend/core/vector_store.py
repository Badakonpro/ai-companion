from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Graceful imports ────────────────────────────────────────────────
_HAS_VSS = False
try:
    import apsw
    import sqlite_vss

    _HAS_VSS = True
except ImportError:
    apsw = None  # type: ignore
    sqlite_vss = None  # type: ignore


class VectorStore:
    """sqlite-vss backed vector store for long-range memory recall.

    Uses apsw (not stdlib sqlite3) because macOS system Python lacks
    enable_load_extension.  Falls back to disabled mode if apsw/sqlite-vss
    are not installed — all query methods return empty results.

    Embedding is obtained via Ollama /api/embed (configurable model).
    """

    def __init__(
        self,
        db_path: Path,
        embedding_model: str = "nomic-embed-text",
        embedding_dim: int = 768,
        ollama_base_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self.db_path = str(db_path)
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.ollama_base_url = ollama_base_url
        self._enabled = _HAS_VSS
        self._embed_ok: Optional[bool] = None  # lazy probe
        if self._enabled:
            self._init_schema()

    # ── Connection ──────────────────────────────────────────────────

    def _connect(self) -> Any:  # apsw.Connection
        conn = apsw.Connection(self.db_path)
        conn.enableloadextension(True)
        sqlite_vss.load(conn)
        conn.enableloadextension(False)
        return conn

    def _init_schema(self) -> None:
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    chunk_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    turn_number INTEGER NOT NULL DEFAULT 0,
                    arc_number INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            # Create vss virtual table if not exists
            # sqlite-vss requires a separate virtual table
            existing = list(
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_vss'"
                )
            )
            if not existing:
                cursor.execute(
                    f"CREATE VIRTUAL TABLE memory_vss USING vss0(embedding({self.embedding_dim}))"
                )
            conn.close()
        except Exception as exc:
            logger.warning("VectorStore init failed, disabling: %s", exc)
            self._enabled = False

    # ── Embedding ───────────────────────────────────────────────────

    def _get_embedding_sync(self, text: str) -> Optional[list[float]]:
        """Call Ollama /api/embed synchronously. Returns None on failure."""
        if self._embed_ok is False:
            return None
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    f"{self.ollama_base_url}/api/embed",
                    json={"model": self.embedding_model, "input": text},
                )
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings", [[]])
                if embeddings and len(embeddings[0]) == self.embedding_dim:
                    return embeddings[0]
        except Exception as exc:
            if self._embed_ok is None:
                logger.warning(
                    "Embedding model '%s' not available (%s). "
                    "Vector recall disabled. Run: ollama pull %s",
                    self.embedding_model,
                    exc,
                    self.embedding_model,
                )
                self._embed_ok = False
        return None

    async def _get_embedding_async(self, text: str) -> Optional[list[float]]:
        """Async version of embedding call."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.ollama_base_url}/api/embed",
                    json={"model": self.embedding_model, "input": text},
                )
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings", [[]])
                if embeddings and len(embeddings[0]) == self.embedding_dim:
                    if self._embed_ok is None:
                        self._embed_ok = True
                    return embeddings[0]
        except Exception as exc:
            if self._embed_ok is None:
                logger.warning(
                    "Embedding model '%s' not available (%s). "
                    "Vector recall disabled. Run: ollama pull %s",
                    self.embedding_model,
                    exc,
                    self.embedding_model,
                )
                self._embed_ok = False
        return None

    # ── Write ───────────────────────────────────────────────────────

    def add_chunk(
        self,
        session_id: str,
        chunk_type: str,
        content: str,
        turn_number: int = 0,
        arc_number: int = 1,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Index a text chunk. Returns True if successfully embedded + stored."""
        if not self._enabled:
            return False

        embedding = self._get_embedding_sync(content)
        if embedding is None:
            return False

        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO memory_chunks (session_id, chunk_type, content, turn_number, arc_number, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    chunk_type,
                    content,
                    turn_number,
                    arc_number,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            rowid = conn.last_insert_rowid()
            # Insert into vss table (rowid must match)
            embedding_json = json.dumps(embedding)
            cursor.execute(
                "INSERT INTO memory_vss (rowid, embedding) VALUES (?, ?)",
                (rowid, embedding_json),
            )
            conn.close()
            if self._embed_ok is None:
                self._embed_ok = True
            return True
        except Exception as exc:
            logger.warning("VectorStore add_chunk failed: %s", exc)
            return False

    # ── Query ───────────────────────────────────────────────────────

    def search(
        self,
        session_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Return top-k most similar chunks for a session."""
        if not self._enabled or self._embed_ok is False:
            return []

        embedding = self._get_embedding_sync(query)
        if embedding is None:
            return []

        try:
            conn = self._connect()
            cursor = conn.cursor()
            # Guard: FAISS crashes on empty index with k > 0
            count = list(cursor.execute(
                "SELECT COUNT(1) FROM memory_chunks WHERE session_id = ?", (session_id,)
            ))
            if not count or count[0][0] == 0:
                conn.close()
                return []
            embedding_json = json.dumps(embedding)
            rows = list(
                cursor.execute(
                    """
                    SELECT mc.id, mc.chunk_type, mc.content, mc.turn_number, mc.arc_number,
                           mc.metadata_json, v.distance
                    FROM (
                        SELECT rowid, distance
                        FROM memory_vss
                        WHERE vss_search(embedding, ?)
                        LIMIT ?
                    ) v
                    JOIN memory_chunks mc ON mc.id = v.rowid
                    WHERE mc.session_id = ?
                    """,
                    (embedding_json, top_k, session_id),
                )
            )
            conn.close()
            results: list[dict[str, Any]] = []
            for row in rows:
                try:
                    meta = json.loads(row[5])
                except Exception:
                    meta = {}
                results.append(
                    {
                        "id": row[0],
                        "chunk_type": row[1],
                        "content": row[2],
                        "turn_number": row[3],
                        "arc_number": row[4],
                        "metadata": meta,
                        "distance": row[6],
                    }
                )
            return results
        except Exception as exc:
            logger.warning("VectorStore search failed: %s", exc)
            return []

    # ── Maintenance ─────────────────────────────────────────────────

    def delete_session(self, session_id: str) -> None:
        """Remove all vectors for a session."""
        if not self._enabled:
            return
        try:
            conn = self._connect()
            cursor = conn.cursor()
            # Get rowids to delete from vss
            rowids = list(
                cursor.execute(
                    "SELECT id FROM memory_chunks WHERE session_id = ?",
                    (session_id,),
                )
            )
            for (rid,) in rowids:
                cursor.execute("DELETE FROM memory_vss WHERE rowid = ?", (rid,))
            cursor.execute(
                "DELETE FROM memory_chunks WHERE session_id = ?",
                (session_id,),
            )
            conn.close()
        except Exception as exc:
            logger.warning("VectorStore delete_session failed: %s", exc)

    def is_available(self) -> bool:
        return self._enabled and self._embed_ok is not False

    def status(self) -> dict[str, Any]:
        return {
            "vss_extension": _HAS_VSS,
            "enabled": self._enabled,
            "embedding_model": self.embedding_model,
            "embedding_available": self._embed_ok,
        }
