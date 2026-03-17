from __future__ import annotations

from typing import Any, Optional


class ContextBudgeter:
    """Priority-based context assembly within a token budget.

    Priority (highest → lowest):
        1. Inviolable constraints & story anchors
        2. Recent turns (STM)
        3. Emotion state & description
        4. High-significance episodes (EM)
        5. Semantic facts (SM)
        6. Background summaries

    Token estimation: ~1.5 chars per token for Chinese text.
    """

    CHARS_PER_TOKEN = 1.5
    DEFAULT_BUDGET = 3000  # tokens for memory context (not the whole prompt)

    def __init__(self, token_budget: Optional[int] = None) -> None:
        self.token_budget = token_budget or self.DEFAULT_BUDGET
        self._char_budget = int(self.token_budget * self.CHARS_PER_TOKEN)

    def _estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) / self.CHARS_PER_TOKEN))

    def build_memory_block(self, memory_context: dict[str, Any]) -> str:
        """Assemble memory layers into a single text block within budget."""
        sections: list[str] = []
        used = 0

        # 1. Emotion description (small, top priority after anchors)
        emotion = memory_context.get('emotion_description', '')
        if emotion:
            section = f"【角色情感状态】\n{emotion}"
            cost = len(section)
            if used + cost <= self._char_budget:
                sections.append(section)
                used += cost

        # 1.5 Vector recall (long-range memory)
        vector_recall = memory_context.get('vector_recall', [])
        if vector_recall:
            recall_lines: list[str] = []
            for vr in vector_recall[:5]:
                line = f"  [{vr.get('chunk_type', '')}] {vr.get('content', '')[:200]}"
                line_cost = len(line) + 1
                if used + line_cost > self._char_budget:
                    break
                recall_lines.append(line)
                used += line_cost
            if recall_lines:
                sections.append("【长期记忆回忆】\n" + "\n".join(recall_lines))

        # 2. High-significance episodes (EM)
        episodes = memory_context.get('episodes', [])
        if episodes:
            # Sort by significance descending, take top entries
            sorted_eps = sorted(episodes, key=lambda e: e.get('significance', 0), reverse=True)
            ep_lines: list[str] = []
            for ep in sorted_eps:
                line = f"  第{ep.get('turn_number', '?')}回 [{ep.get('event_type', '')}]: {ep.get('summary', '')}"
                line_cost = len(line) + 1
                if used + line_cost > self._char_budget:
                    break
                ep_lines.append(line)
                used += line_cost
            if ep_lines:
                sections.append("【重要事件回忆】\n" + "\n".join(ep_lines))

        # 3. Semantic facts (SM)
        facts = memory_context.get('facts', [])
        if facts:
            # High confidence first
            sorted_facts = sorted(facts, key=lambda f: f.get('confidence', 0), reverse=True)
            fact_lines: list[str] = []
            for f in sorted_facts:
                line = f"  {f.get('subject', '')}.{f.get('predicate', '')} = {f.get('value', '')}"
                line_cost = len(line) + 1
                if used + line_cost > self._char_budget:
                    break
                fact_lines.append(line)
                used += line_cost
            if fact_lines:
                sections.append("【已知事实】\n" + "\n".join(fact_lines))

        # 4. Summary (background)
        summaries = [e for e in episodes if e.get('event_type') == 'summary']
        if summaries:
            latest_summary = summaries[-1].get('summary', '')
            if latest_summary:
                section = f"【情节概要】\n{latest_summary}"
                cost = len(section)
                if used + cost <= self._char_budget:
                    sections.append(section)
                    used += cost

        return "\n\n".join(sections)

    def build_stm_messages(
        self,
        stm: list[dict[str, Any]],
        max_tokens: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Trim STM messages to fit within token budget (for chat history)."""
        budget = int((max_tokens or 2000) * self.CHARS_PER_TOKEN)
        result: list[dict[str, Any]] = []
        used = 0

        # Iterate from most recent to oldest, include as many as fit
        for turn in reversed(stm):
            narrative = str(turn.get('narrative', ''))[:600]
            user_input = str(turn.get('user_input', ''))[:200]
            cost = len(narrative) + len(user_input) + 20  # overhead
            if used + cost > budget:
                break
            result.insert(0, turn)
            used += cost

        return result
