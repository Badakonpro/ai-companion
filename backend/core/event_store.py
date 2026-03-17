from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .perf_cache import l1_cache, cache_key


class EventStore:
    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    ai_response TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS story_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    narrative TEXT NOT NULL,
                    protagonist_action TEXT NOT NULL,
                    choices_json TEXT NOT NULL,
                    state_delta_json TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_state (
                    session_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_meta (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    seed_json TEXT NOT NULL DEFAULT '{}',
                    nsfw_level TEXT NOT NULL DEFAULT 'mild',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # ── Episodic memory (EM) ────────────────────────────────
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodic_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    participants_json TEXT NOT NULL DEFAULT '[]',
                    significance REAL NOT NULL DEFAULT 0.5,
                    created_at TEXT NOT NULL
                )
                """
            )
            # ── Affective / Emotion memory (AEM) ───────────────────
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS emotion_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    affection REAL NOT NULL DEFAULT 0.3,
                    tension REAL NOT NULL DEFAULT 0.3,
                    trust REAL NOT NULL DEFAULT 0.4,
                    comfort REAL NOT NULL DEFAULT 0.5,
                    created_at TEXT NOT NULL
                )
                """
            )
            # ── Story Arcs ─────────────────────────────────────────
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS story_arcs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    arc_number INTEGER NOT NULL DEFAULT 1,
                    title TEXT NOT NULL DEFAULT '',
                    seed_json TEXT NOT NULL DEFAULT '{}',
                    summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    start_turn INTEGER NOT NULL DEFAULT 0,
                    end_turn INTEGER,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            # ── Snapshots (5.1) ────────────────────────────────────
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    turn_number INTEGER NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    facts_json TEXT NOT NULL DEFAULT '[]',
                    emotion_json TEXT NOT NULL DEFAULT '[]',
                    arcs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS protagonist_profile (
                    session_id TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS script_blueprints (
                    session_id TEXT PRIMARY KEY,
                    blueprint_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

            conn.commit()

    @staticmethod
    def _default_state() -> dict[str, float]:
        return {
            "tension": 0.0,
            "trust": 0.0,
            "progress": 0.0,
        }

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(value, high))

    def log_turn(
        self,
        user_message: str,
        ai_response: str,
        model_used: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_turns (created_at, user_message, ai_response, model_used, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (datetime.utcnow().isoformat(), user_message, ai_response, model_used, payload),
            )
            conn.commit()

    def health(self) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(1) AS count FROM chat_turns").fetchone()
            return {"ready": True, "turn_count": row["count"] if row else 0}
        except Exception as exc:
            return {"ready": False, "error": str(exc)}

    def log_story_turn(
        self,
        session_id: str,
        user_input: str,
        narrative: str,
        protagonist_action: str,
        choices: list[dict[str, Any]],
        state_delta: dict[str, Any],
        model_used: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO story_turns (
                    created_at,
                    session_id,
                    user_input,
                    narrative,
                    protagonist_action,
                    choices_json,
                    state_delta_json,
                    model_used,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
                    session_id,
                    user_input,
                    narrative,
                    protagonist_action,
                    json.dumps(choices, ensure_ascii=False),
                    json.dumps(state_delta, ensure_ascii=False),
                    model_used,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_session_state(self, session_id: str) -> dict[str, float]:
        ck = cache_key('state', session_id)
        cached = l1_cache.get(ck)
        if cached is not None:
            return cached
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM session_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        if not row:
            return self._default_state()

        try:
            data = json.loads(row["state_json"])
        except Exception:
            return self._default_state()

        merged = self._default_state()
        for key in merged:
            raw = data.get(key)
            if isinstance(raw, (int, float)):
                merged[key] = float(raw)
        l1_cache.put(ck, merged)
        return merged

    def apply_state_delta(self, session_id: str, delta: dict[str, Any] | None) -> dict[str, float]:
        current = self.get_session_state(session_id)
        patch = delta or {}

        for key in current.keys():
            raw = patch.get(key)
            if isinstance(raw, (int, float)):
                if key == "progress":
                    current[key] = self._clamp(current[key] + float(raw), 0.0, 1.0)
                else:
                    current[key] = self._clamp(current[key] + float(raw), -1.0, 1.0)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_state (session_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id)
                DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    json.dumps(current, ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        l1_cache.invalidate(cache_key('state', session_id))
        return current

    def get_session_facts(self, session_id: str) -> list[dict[str, Any]]:
        ck = cache_key('facts', session_id)
        cached = l1_cache.get(ck)
        if cached is not None:
            return cached
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT subject, predicate, value, confidence
                FROM session_facts
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        result = [
            {
                "subject": row["subject"],
                "predicate": row["predicate"],
                "value": row["value"],
                "confidence": row["confidence"],
            }
            for row in rows
        ]
        l1_cache.put(ck, result)
        return result

    def replace_session_facts(self, session_id: str, facts: list[dict[str, Any]]) -> None:
        timestamp = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM session_facts WHERE session_id = ?", (session_id,))
            for fact in facts:
                conn.execute(
                    """
                    INSERT INTO session_facts (session_id, subject, predicate, value, confidence, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        str(fact.get("subject", "")).strip(),
                        str(fact.get("predicate", "")).strip(),
                        str(fact.get("value", "")).strip(),
                        float(fact.get("confidence", 1.0)),
                        timestamp,
                    ),
                )
            conn.commit()
        l1_cache.invalidate(cache_key('facts', session_id))

    # ── Session management ──────────────────────────────────────────

    def save_session_meta(
        self,
        session_id: str,
        title: str,
        seed: dict[str, Any] | None = None,
        nsfw_level: str = "mild",
    ) -> None:
        now = datetime.utcnow().isoformat()
        seed_json = json.dumps(seed or {}, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_meta (session_id, title, seed_json, nsfw_level, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id)
                DO UPDATE SET title = excluded.title, seed_json = excluded.seed_json,
                             nsfw_level = excluded.nsfw_level, updated_at = excluded.updated_at
                """,
                (session_id, title, seed_json, nsfw_level, now, now),
            )
            conn.commit()

    def touch_session(self, session_id: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE session_meta SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            conn.commit()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.session_id, m.title, m.seed_json, m.nsfw_level,
                       m.created_at, m.updated_at,
                       COALESCE(s.state_json, '{}') AS state_json,
                       (SELECT COUNT(1) FROM story_turns t WHERE t.session_id = m.session_id) AS turn_count
                FROM session_meta m
                LEFT JOIN session_state s ON s.session_id = m.session_id
                ORDER BY m.updated_at DESC
                """,
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                seed = json.loads(row["seed_json"])
            except Exception:
                seed = {}
            try:
                state = json.loads(row["state_json"])
            except Exception:
                state = {}
            result.append({
                "session_id": row["session_id"],
                "title": row["title"],
                "seed": seed,
                "nsfw_level": row["nsfw_level"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "state": state,
                "turn_count": row["turn_count"],
            })
        return result

    def get_session_history(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_input, narrative, protagonist_action, choices_json, state_delta_json, created_at
                FROM story_turns
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        turns: list[dict[str, Any]] = []
        for row in rows:
            try:
                choices = json.loads(row["choices_json"])
            except Exception:
                choices = []
            try:
                state_delta = json.loads(row["state_delta_json"])
            except Exception:
                state_delta = {}
            turns.append({
                "user_input": row["user_input"],
                "narrative": row["narrative"],
                "protagonist_action": row["protagonist_action"],
                "choices": choices,
                "state_delta": state_delta,
                "created_at": row["created_at"],
            })
        return turns

    def delete_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM story_turns WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_state WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_facts WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_meta WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM episodic_events WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM emotion_state WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM story_arcs WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM protagonist_profile WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM script_blueprints WHERE session_id = ?", (session_id,))
            conn.commit()
        l1_cache.invalidate_prefix(f'state:{session_id}')
        l1_cache.invalidate_prefix(f'facts:{session_id}')
        l1_cache.invalidate_prefix(f'episodes:{session_id}')
        l1_cache.invalidate_prefix(f'emotion:{session_id}')

    # ── Episodic Memory (EM) ────────────────────────────────────────

    def add_episode(
        self,
        session_id: str,
        turn_number: int,
        event_type: str,
        summary: str,
        participants: list[str] | None = None,
        significance: float = 0.5,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO episodic_events
                    (session_id, turn_number, event_type, summary, participants_json, significance, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_number,
                    event_type,
                    summary,
                    json.dumps(participants or [], ensure_ascii=False),
                    significance,
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        l1_cache.invalidate(cache_key('episodes', session_id))

    def get_episodes(self, session_id: str) -> list[dict[str, Any]]:
        ck = cache_key('episodes', session_id)
        cached = l1_cache.get(ck)
        if cached is not None:
            return cached
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT turn_number, event_type, summary, participants_json, significance
                FROM episodic_events
                WHERE session_id = ?
                ORDER BY turn_number ASC, id ASC
                """,
                (session_id,),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                participants = json.loads(row["participants_json"])
            except Exception:
                participants = []
            result.append({
                "turn_number": row["turn_number"],
                "event_type": row["event_type"],
                "summary": row["summary"],
                "participants": participants,
                "significance": row["significance"],
            })
        l1_cache.put(ck, result)
        return result

    # ── Emotion State (AEM) ─────────────────────────────────────────

    def save_emotion_state(
        self,
        session_id: str,
        character_id: str,
        turn_number: int,
        emotion: dict[str, float],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO emotion_state
                    (session_id, character_id, turn_number, affection, tension, trust, comfort, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    character_id,
                    turn_number,
                    emotion.get("affection", 0.3),
                    emotion.get("tension", 0.3),
                    emotion.get("trust", 0.4),
                    emotion.get("comfort", 0.5),
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        l1_cache.invalidate(cache_key('emotion', session_id))

    def get_emotion_state(self, session_id: str) -> list[dict[str, Any]]:
        """Get the latest emotion state per character for a session."""
        ck = cache_key('emotion', session_id)
        cached = l1_cache.get(ck)
        if cached is not None:
            return cached
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT character_id, affection, tension, trust, comfort, turn_number
                FROM emotion_state
                WHERE session_id = ?
                  AND id IN (
                    SELECT MAX(id) FROM emotion_state
                    WHERE session_id = ?
                    GROUP BY character_id
                  )
                """,
                (session_id, session_id),
            ).fetchall()

        result = [
            {
                "character_id": row["character_id"],
                "affection": row["affection"],
                "tension": row["tension"],
                "trust": row["trust"],
                "comfort": row["comfort"],
                "turn_number": row["turn_number"],
            }
            for row in rows
        ]
        l1_cache.put(ck, result)
        return result

    def get_emotion_history(self, session_id: str, character_id: str) -> list[dict[str, Any]]:
        """Get full emotion trajectory for a character (for frontend charts)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT turn_number, affection, tension, trust, comfort
                FROM emotion_state
                WHERE session_id = ? AND character_id = ?
                ORDER BY turn_number ASC
                """,
                (session_id, character_id),
            ).fetchall()

        return [
            {
                "turn_number": row["turn_number"],
                "affection": row["affection"],
                "tension": row["tension"],
                "trust": row["trust"],
                "comfort": row["comfort"],
            }
            for row in rows
        ]

    # ── Story Arcs ──────────────────────────────────────────────────

    def create_arc(
        self,
        session_id: str,
        arc_number: int,
        title: str,
        seed: dict[str, Any] | None = None,
        start_turn: int = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO story_arcs (session_id, arc_number, title, seed_json, status, start_turn, created_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (session_id, arc_number, title, json.dumps(seed or {}, ensure_ascii=False), start_turn, now),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_arcs(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, arc_number, title, seed_json, summary, status, start_turn, end_turn, created_at, completed_at
                FROM story_arcs
                WHERE session_id = ?
                ORDER BY arc_number ASC
                """,
                (session_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                seed = json.loads(row["seed_json"])
            except Exception:
                seed = {}
            result.append({
                "id": row["id"],
                "arc_number": row["arc_number"],
                "title": row["title"],
                "seed": seed,
                "summary": row["summary"],
                "status": row["status"],
                "start_turn": row["start_turn"],
                "end_turn": row["end_turn"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            })
        return result

    def get_active_arc(self, session_id: str) -> dict[str, Any] | None:
        arcs = self.get_arcs(session_id)
        for arc in reversed(arcs):
            if arc["status"] == "active":
                return arc
        return None

    def complete_arc(self, session_id: str, arc_number: int, summary: str, end_turn: int) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE story_arcs
                SET status = 'completed', summary = ?, end_turn = ?, completed_at = ?
                WHERE session_id = ? AND arc_number = ?
                """,
                (summary, end_turn, now, session_id, arc_number),
            )
            conn.commit()

    # ── Snapshots (5.1) ─────────────────────────────────────────────

    def create_snapshot(self, session_id: str, label: str = "") -> dict[str, Any]:
        """Capture full session state at current turn as a snapshot."""
        history = self.get_session_history(session_id)
        turn_number = len(history)
        state = self.get_session_state(session_id)
        facts = self.get_session_facts(session_id)
        emotion = self.get_emotion_state(session_id)
        arcs = self.get_arcs(session_id)

        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO session_snapshots
                    (session_id, label, turn_number, state_json, facts_json, emotion_json, arcs_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    label or f"Turn {turn_number}",
                    turn_number,
                    json.dumps(state, ensure_ascii=False),
                    json.dumps(facts, ensure_ascii=False),
                    json.dumps(emotion, ensure_ascii=False),
                    json.dumps(arcs, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
            snap_id = cursor.lastrowid or 0

        return {
            "id": snap_id,
            "session_id": session_id,
            "label": label or f"Turn {turn_number}",
            "turn_number": turn_number,
            "created_at": now,
        }

    def list_snapshots(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, label, turn_number, created_at
                FROM session_snapshots
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "label": row["label"],
                "turn_number": row["turn_number"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def restore_snapshot(self, session_id: str, snapshot_id: int) -> dict[str, Any]:
        """Restore session to a previous snapshot, truncating turns after the snapshot point (branch)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_snapshots WHERE id = ? AND session_id = ?",
                (snapshot_id, session_id),
            ).fetchone()

        if not row:
            return {}

        turn_number = row["turn_number"]
        state = json.loads(row["state_json"])
        facts = json.loads(row["facts_json"])
        emotion_list = json.loads(row["emotion_json"])
        arcs_list = json.loads(row["arcs_json"])
        now = datetime.utcnow().isoformat()

        with self._connect() as conn:
            # Truncate story_turns after snapshot turn
            # Get the id of the turn at the snapshot boundary
            boundary = conn.execute(
                """
                SELECT id FROM story_turns
                WHERE session_id = ?
                ORDER BY id ASC
                LIMIT 1 OFFSET ?
                """,
                (session_id, turn_number),
            ).fetchone()
            if boundary:
                conn.execute(
                    "DELETE FROM story_turns WHERE session_id = ? AND id >= ?",
                    (session_id, boundary["id"]),
                )

            # Restore session state
            conn.execute(
                """
                INSERT INTO session_state (session_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id)
                DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at
                """,
                (session_id, json.dumps(state, ensure_ascii=False), now),
            )

            # Restore facts
            conn.execute("DELETE FROM session_facts WHERE session_id = ?", (session_id,))
            for fact in facts:
                conn.execute(
                    """
                    INSERT INTO session_facts (session_id, subject, predicate, value, confidence, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        str(fact.get("subject", "")),
                        str(fact.get("predicate", "")),
                        str(fact.get("value", "")),
                        float(fact.get("confidence", 1.0)),
                        now,
                    ),
                )

            # Truncate episodes after snapshot turn
            conn.execute(
                "DELETE FROM episodic_events WHERE session_id = ? AND turn_number > ?",
                (session_id, turn_number),
            )

            # Truncate emotion records after snapshot turn
            conn.execute(
                "DELETE FROM emotion_state WHERE session_id = ? AND turn_number > ?",
                (session_id, turn_number),
            )

            # Restore arcs: remove arcs that started after snapshot turn
            conn.execute(
                "DELETE FROM story_arcs WHERE session_id = ? AND start_turn > ?",
                (session_id, turn_number),
            )
            # Re-activate arc that was active at snapshot time
            for arc_data in arcs_list:
                if arc_data.get("status") == "active":
                    conn.execute(
                        """
                        UPDATE story_arcs
                        SET status = 'active', end_turn = NULL, completed_at = NULL, summary = ''
                        WHERE session_id = ? AND arc_number = ?
                        """,
                        (session_id, arc_data.get("arc_number")),
                    )

            # Delete snapshots created after this one (they're invalid now)
            conn.execute(
                "DELETE FROM session_snapshots WHERE session_id = ? AND id > ?",
                (session_id, snapshot_id),
            )

            conn.commit()

        # Invalidate all caches for this session
        l1_cache.invalidate_prefix(f'state:{session_id}')
        l1_cache.invalidate_prefix(f'facts:{session_id}')
        l1_cache.invalidate_prefix(f'episodes:{session_id}')
        l1_cache.invalidate_prefix(f'emotion:{session_id}')

        return {
            "id": snapshot_id,
            "session_id": session_id,
            "label": row["label"],
            "turn_number": turn_number,
            "restored_at": now,
        }

    # ── Protagonist Profile ──────────────────────────────────────────

    def get_protagonist_profile(self, session_id: str) -> dict[str, float] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT profile_json FROM protagonist_profile WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["profile_json"])
        except (json.JSONDecodeError, TypeError):
            return None

    def save_protagonist_profile(self, session_id: str, profile: dict[str, float]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO protagonist_profile (session_id, profile_json, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(session_id) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, json.dumps(profile, ensure_ascii=False)),
            )
            conn.commit()

    # ── Script Blueprint ─────────────────────────────────────────────

    def get_blueprint(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT blueprint_json FROM script_blueprints WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["blueprint_json"])
        except (json.JSONDecodeError, TypeError):
            return None

    def save_blueprint(self, session_id: str, blueprint: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO script_blueprints (session_id, blueprint_json, created_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(session_id) DO UPDATE SET
                    blueprint_json = excluded.blueprint_json,
                    created_at = excluded.created_at
                """,
                (session_id, json.dumps(blueprint, ensure_ascii=False)),
            )
            conn.commit()
