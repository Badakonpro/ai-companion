from __future__ import annotations

import json
import re
from typing import Any, Optional

from .event_store import EventStore
from .vector_store import VectorStore


class ArcManager:
    """Manages story arc lifecycle: creation, progression, completion, and transition.

    An arc represents a distinct plot thread (e.g. a mystery case, a chapter)
    within the same persistent world and character relationships.
    """

    # How many turns per arc before suggesting completion
    ARC_MIN_TURNS = 15
    # Token budget for arc summary prompt
    SUMMARY_TURNS = 8

    def __init__(
        self,
        event_store: EventStore,
        vector_store: VectorStore,
    ) -> None:
        self.event_store = event_store
        self.vector_store = vector_store

    # ── Arc lifecycle ───────────────────────────────────────────────

    def get_or_create_arc(self, session_id: str, seed: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Get the active arc, or create arc #1 if none exists."""
        active = self.event_store.get_active_arc(session_id)
        if active:
            return active
        # Create first arc
        history = self.event_store.get_session_history(session_id)
        arc_id = self.event_store.create_arc(
            session_id=session_id,
            arc_number=1,
            title=seed.get("title", "第一章") if seed else "第一章",
            seed=seed,
            start_turn=len(history),
        )
        return self.event_store.get_active_arc(session_id) or {"arc_number": 1, "status": "active"}

    def should_suggest_completion(self, session_id: str) -> bool:
        """Check if the current arc has enough turns to suggest completion."""
        active = self.event_store.get_active_arc(session_id)
        if not active:
            return False
        history = self.event_store.get_session_history(session_id)
        turns_in_arc = len(history) - active.get("start_turn", 0)
        return turns_in_arc >= self.ARC_MIN_TURNS

    def complete_current_arc(
        self,
        session_id: str,
        summary: str,
    ) -> Optional[dict[str, Any]]:
        """Mark the current arc as completed and index its summary."""
        active = self.event_store.get_active_arc(session_id)
        if not active:
            return None

        history = self.event_store.get_session_history(session_id)
        end_turn = len(history)

        self.event_store.complete_arc(
            session_id=session_id,
            arc_number=active["arc_number"],
            summary=summary,
            end_turn=end_turn,
        )

        # Index arc summary into vector store for long-range recall
        self.vector_store.add_chunk(
            session_id=session_id,
            chunk_type="arc_summary",
            content=summary,
            turn_number=end_turn,
            arc_number=active["arc_number"],
            metadata={"title": active.get("title", ""), "arc_number": active["arc_number"]},
        )

        return {**active, "status": "completed", "summary": summary, "end_turn": end_turn}

    def start_new_arc(
        self,
        session_id: str,
        title: str,
        seed: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Start a new arc after the previous one is completed.

        Preserves: character relationships, emotion, all facts.
        Resets: progress state to 0.
        """
        arcs = self.event_store.get_arcs(session_id)
        next_number = max((a["arc_number"] for a in arcs), default=0) + 1
        history = self.event_store.get_session_history(session_id)

        self.event_store.create_arc(
            session_id=session_id,
            arc_number=next_number,
            title=title,
            seed=seed,
            start_turn=len(history),
        )

        # Reset progress (but NOT tension/trust/emotion — those carry over)
        state = self.event_store.get_session_state(session_id)
        if state.get("progress", 0) > 0:
            self.event_store.apply_state_delta(session_id, {"progress": -state["progress"]})

        return self.event_store.get_active_arc(session_id) or {"arc_number": next_number}

    # ── Arc summary generation ──────────────────────────────────────

    def build_arc_summary_prompt(self, session_id: str) -> Optional[str]:
        """Build a prompt for the AI to summarize the current arc for completion."""
        active = self.event_store.get_active_arc(session_id)
        if not active:
            return None

        history = self.event_store.get_session_history(session_id)
        start = active.get("start_turn", 0)
        arc_turns = history[start:]

        if not arc_turns:
            return None

        # Take first few + last few turns for summary
        sample = arc_turns[:4] + arc_turns[-4:] if len(arc_turns) > 8 else arc_turns
        text_block = ""
        for i, turn in enumerate(sample):
            text_block += f"[玩家] {turn['user_input']}\n"
            text_block += f"[叙事] {turn['narrative'][:400]}...\n\n"

        facts = self.event_store.get_session_facts(session_id)
        fact_text = ""
        for f in facts[:15]:
            fact_text += f"  - {f['subject']}.{f['predicate']} = {f['value']}\n"

        episodes = self.event_store.get_episodes(session_id)
        arc_episodes = [e for e in episodes if e["turn_number"] >= start]
        ep_text = ""
        for ep in arc_episodes[-12:]:
            ep_text += f"  - 第{ep['turn_number']}回 [{ep['event_type']}]: {ep['summary']}\n"

        arc_title = active.get("title", "当前篇章")
        seed_info = active.get("seed", {})
        world_seed_text = seed_info.get("world_seed", "") if isinstance(seed_info, dict) else ""

        return (
            "请为以下篇章生成一段完结摘要，用于存档和后续剧情衔接。输出严格JSON。\n\n"
            f"【篇章标题】{arc_title}\n"
            f"【世界设定】{world_seed_text[:200]}\n\n"
            f"【关键事件】\n{ep_text}\n"
            f"【已知事实】\n{fact_text}\n"
            f"【叙事节选】\n{text_block}\n"
            'JSON格式: {"summary": "100-200字的篇章总结，涵盖主要事件、关系变化和情感走向",'
            ' "title_suggestion": "对下一篇章的标题建议"}\n'
            "请只输出JSON。 /no_think"
        )

    def build_new_arc_prompt(
        self,
        session_id: str,
        user_hint: str = "",
        nsfw_level: str = "mild",
    ) -> str:
        """Build a prompt for AI to generate a new arc seed continuing the story."""
        arcs = self.event_store.get_arcs(session_id)
        facts = self.event_store.get_session_facts(session_id)
        episodes = self.event_store.get_episodes(session_id)

        # Previous arc summaries
        arc_summaries = ""
        for arc in arcs:
            if arc["status"] == "completed" and arc.get("summary"):
                arc_summaries += f"  第{arc['arc_number']}篇「{arc['title']}」: {arc['summary']}\n"

        # Core character facts (filter for character-intrinsic)
        character_facts = ""
        for f in facts[:20]:
            character_facts += f"  - {f['subject']}.{f['predicate']} = {f['value']}\n"

        # Recent emotion
        from .emotion_engine import DEFAULT_EMOTION
        emotion_states = self.event_store.get_emotion_state(session_id)
        emotion_text = ""
        for es in emotion_states:
            emotion_text += f"  {es['character_id']}: 好感{es['affection']:.1f} 紧张{es['tension']:.1f} 信任{es['trust']:.1f} 舒适{es['comfort']:.1f}\n"

        hint_line = f"【用户要求】{user_hint.strip()}\n" if user_hint.strip() else ""

        return (
            "你是一位言情互动小说的世界观架构师。"
            "基于以下已完成的篇章、角色关系和世界设定，生成一个新的剧情弧（篇章）大纲。\n"
            "新篇章必须延续同一世界观和人物关系，但引入新的剧情主线。\n\n"
            f"【已完成篇章】\n{arc_summaries}\n"
            f"【角色关系与事实】\n{character_facts}\n"
            f"【当前情感状态】\n{emotion_text}\n"
            f"{hint_line}"
            "要求：\n"
            "1. 新篇章应延续现有人物关系和情感基础\n"
            "2. 引入新的冲突/悬念/情感催化剂\n"
            "3. 不重复之前篇章的核心桥段\n\n"
            'JSON格式: {"title": "新篇章标题", "world_seed": "100-200字的新篇章设定和剧情起点"}\n'
            "请只输出JSON。 /no_think"
        )

    # ── Vector-backed long-range recall ─────────────────────────────

    def index_turn(
        self,
        session_id: str,
        turn_number: int,
        narrative: str,
        arc_number: int = 1,
    ) -> None:
        """Index a turn's narrative into vector store for future recall."""
        # Only index substantial narratives
        if len(narrative) < 100:
            return
        # Compress to a summary-length chunk for embedding efficiency
        chunk = narrative[:500]
        self.vector_store.add_chunk(
            session_id=session_id,
            chunk_type="narrative",
            content=chunk,
            turn_number=turn_number,
            arc_number=arc_number,
        )

    def recall_relevant(
        self,
        session_id: str,
        query: str,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Retrieve the most relevant past chunks for current context."""
        return self.vector_store.search(session_id, query, top_k=top_k)

    # ── Periodic summarization trigger ──────────────────────────────

    def should_summarize_period(self, session_id: str, every_k: int = 8) -> bool:
        """Check if enough turns have passed since last summary for periodic compression."""
        history = self.event_store.get_session_history(session_id)
        if len(history) < every_k:
            return False
        # Check last summary episode
        episodes = self.event_store.get_episodes(session_id)
        summary_eps = [e for e in episodes if e["event_type"] == "summary"]
        if not summary_eps:
            return len(history) >= every_k
        last_summary_turn = max(e["turn_number"] for e in summary_eps)
        return (len(history) - last_summary_turn) >= every_k

    def index_summary(
        self,
        session_id: str,
        summary: str,
        turn_number: int,
        arc_number: int = 1,
    ) -> None:
        """Index a periodic summary into vector store."""
        self.vector_store.add_chunk(
            session_id=session_id,
            chunk_type="periodic_summary",
            content=summary,
            turn_number=turn_number,
            arc_number=arc_number,
        )
