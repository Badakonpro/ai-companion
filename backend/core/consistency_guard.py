from __future__ import annotations

import re
from typing import Any, Optional


class ConsistencyGuard:
    """Multi-type conflict detection & drift suppression.

    Conflict types:
      1. Fact conflict        – same (subject, predicate) has contradictory values
      2. Timeline conflict    – event ordering / time-of-day contradictions
      3. Motivation conflict  – character acts against established personality/relationship
    """

    # ── Inviolable anchors ──────────────────────────────────────────
    def __init__(self) -> None:
        self.story_anchors = [
            "玩家='你'，是Galgame男主角，第二人称叙事。",
            "林夕是女主角，其行为要符合既有动机和关系变化。",
            "剧情必须连续推进，不允许无理由重置设定。",
        ]
        # Inviolable constraints — NEVER violated under any circumstances
        self.inviolable_constraints = [
            "不得让角色突然性格反转（需要至少1-2回合铺垫）。",
            "不得违背已确立的世界观设定（地点、时间线、角色关系）。",
            "不得出现前后矛盾的物理空间描述（角色不能同时在两个地方）。",
            "NPC的行为动机必须与已建立的性格特征一致。",
        ]

    # ── Fact extraction (enhanced) ──────────────────────────────────

    @staticmethod
    def extract_facts(text: str) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def _add(subject: str, predicate: str, value: str, confidence: float) -> None:
            key = (subject, predicate)
            if key in seen:
                return
            seen.add(key)
            facts.append({
                "subject": subject,
                "predicate": predicate,
                "value": value.strip()[:60],
                "confidence": confidence,
            })

        # Location facts
        for subject, value in re.findall(r"(主角|你|林夕)(?:来到|走进|在|到了)([^，。！？\n]{2,15})", text):
            _add(subject, "location", value, 0.7)

        # Emotion facts
        for subject, value in re.findall(r"(主角|你|林夕)(?:显得|看起来|变得|感到)?很?([^，。！？\n]{1,8}(?:开心|难过|生气|害羞|紧张|不安|高兴|烦躁|平静|伤心|激动|尴尬))", text):
            _add(subject, "emotion", value, 0.6)

        # Time-of-day
        for time_word in re.findall(r"(清晨|早上|上午|中午|下午|傍晚|晚上|深夜|凌晨)", text):
            _add("scene", "time_of_day", time_word, 0.5)

        # Weather
        for weather in re.findall(r"(下雨|阴天|晴天|下雪|大风|雾|闷热)", text):
            _add("scene", "weather", weather, 0.5)

        # Relationship attitude
        for pattern, subj, pred, val, conf in [
            (r"林夕.{0,15}(信任|相信|依赖)", "linxi", "attitude", "trust_protagonist", 0.7),
            (r"林夕.{0,15}(讨厌|厌恶|反感)", "linxi", "attitude", "dislike_protagonist", 0.7),
            (r"林夕.{0,15}(在意|关心|担心)", "linxi", "attitude", "cares_protagonist", 0.6),
        ]:
            if re.search(pattern, text):
                _add(subj, pred, val, conf)

        return facts

    # ── 3-Type Conflict Detection ───────────────────────────────────

    def detect_conflicts(
        self,
        existing_facts: list[dict[str, Any]],
        new_facts: list[dict[str, Any]],
        episodes: Optional[list[dict[str, Any]]] = None,
        emotion: Optional[dict[str, float]] = None,
    ) -> list[str]:
        """Detect fact, timeline, and motivation conflicts."""
        conflicts: list[str] = []
        conflicts.extend(self._detect_fact_conflicts(existing_facts, new_facts))
        conflicts.extend(self._detect_timeline_conflicts(existing_facts, new_facts))
        if emotion:
            conflicts.extend(self._detect_motivation_conflicts(new_facts, emotion))
        return conflicts

    def _detect_fact_conflicts(
        self,
        existing_facts: list[dict[str, Any]],
        new_facts: list[dict[str, Any]],
    ) -> list[str]:
        """Type 1: Same (subject, predicate) has contradictory values."""
        known: dict[tuple[str, str], str] = {}
        for fact in existing_facts:
            key = (str(fact.get("subject", "")), str(fact.get("predicate", "")))
            value = str(fact.get("value", ""))
            if key[0] and key[1] and value:
                known[key] = value

        conflicts: list[str] = []
        for fact in new_facts:
            key = (str(fact.get("subject", "")), str(fact.get("predicate", "")))
            new_value = str(fact.get("value", ""))
            old_value = known.get(key)
            if old_value and new_value and old_value != new_value:
                conflicts.append(
                    f"[事实冲突] {key[0]}的{key[1]}：历史={old_value}，当前={new_value}"
                )
        return conflicts

    def _detect_timeline_conflicts(
        self,
        existing_facts: list[dict[str, Any]],
        new_facts: list[dict[str, Any]],
    ) -> list[str]:
        """Type 2: Time-of-day goes backwards without explicit time skip."""
        TIME_ORDER = ['清晨', '早上', '上午', '中午', '下午', '傍晚', '晚上', '深夜', '凌晨']

        old_time = None
        for f in existing_facts:
            if f.get('predicate') == 'time_of_day':
                old_time = f.get('value', '')

        new_time = None
        for f in new_facts:
            if f.get('predicate') == 'time_of_day':
                new_time = f.get('value', '')

        conflicts: list[str] = []
        if old_time and new_time and old_time != new_time:
            old_idx = TIME_ORDER.index(old_time) if old_time in TIME_ORDER else -1
            new_idx = TIME_ORDER.index(new_time) if new_time in TIME_ORDER else -1
            if old_idx >= 0 and new_idx >= 0 and new_idx < old_idx:
                # Time went backwards — could be next-day but flag it
                conflicts.append(
                    f"[时间线冲突] 时间从'{old_time}'退回到'{new_time}'，需确认是否经过了新一天"
                )
        return conflicts

    def _detect_motivation_conflicts(
        self,
        new_facts: list[dict[str, Any]],
        emotion: dict[str, float],
    ) -> list[str]:
        """Type 3: Character action contradicts established emotion state."""
        conflicts: list[str] = []

        # If trust is very low but narrative shows trust-gaining event
        trust = emotion.get('trust', 0.4)
        affection = emotion.get('affection', 0.3)

        for fact in new_facts:
            val = str(fact.get('value', ''))
            subj = str(fact.get('subject', ''))
            pred = str(fact.get('predicate', ''))

            # Low trust character suddenly trusting
            if subj == 'linxi' and pred == 'attitude' and 'trust' in val and trust < 0.2:
                conflicts.append(
                    f"[动机冲突] 林夕当前信任度极低({trust:.2f})，突然表现出信任行为不合理"
                )

            # Very low affection but showing intimacy
            if subj == 'linxi' and pred == 'attitude' and 'cares' in val and affection < 0.15:
                conflicts.append(
                    f"[动机冲突] 林夕好感度极低({affection:.2f})，突然表现出关心不自然"
                )

        return conflicts

    # ── Merge (unchanged logic) ─────────────────────────────────────

    def merge_facts(
        self,
        existing_facts: list[dict[str, Any]],
        new_facts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for fact in existing_facts + new_facts:
            subject = str(fact.get("subject", "")).strip()
            predicate = str(fact.get("predicate", "")).strip()
            value = str(fact.get("value", "")).strip()
            if not subject or not predicate or not value:
                continue
            key = (subject, predicate)
            merged[key] = {
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "confidence": float(fact.get("confidence", 1.0)),
            }
        return list(merged.values())

    # ── Constraint Building (enhanced) ──────────────────────────────

    def build_constraints(
        self,
        session_state: dict[str, float],
        facts: list[dict[str, Any]],
        extra_constraints: Optional[list[str]] = None,
        emotion: Optional[dict[str, float]] = None,
        episodes: Optional[list[dict[str, Any]]] = None,
    ) -> list[str]:
        constraints: list[str] = []

        # 1. Inviolable constraints first (highest priority)
        constraints.extend(self.inviolable_constraints)

        # 2. Story anchors
        constraints.extend(self.story_anchors)

        # 3. State
        constraints.append(
            f"当前状态: tension={session_state.get('tension', 0.0):.2f}, "
            f"trust={session_state.get('trust', 0.0):.2f}, "
            f"progress={session_state.get('progress', 0.0):.2f}"
        )

        # 4. Emotion context
        if emotion:
            emo_parts = []
            for dim, val in emotion.items():
                emo_parts.append(f"{dim}={val:.2f}")
            constraints.append(f"情感维度: {', '.join(emo_parts)}")

        # 5. Key episodes as anchoring context
        if episodes:
            recent = [e for e in episodes if e.get('significance', 0) > 0.5][-5:]
            for ep in recent:
                constraints.append(
                    f"关键事件(第{ep.get('turn_number', '?')}回): {ep.get('summary', '')}"
                )

        # 6. Fact anchors
        sorted_facts = sorted(facts, key=lambda f: f.get('confidence', 0), reverse=True)
        for fact in sorted_facts[:12]:
            subject = fact.get("subject")
            predicate = fact.get("predicate")
            value = fact.get("value")
            if subject and predicate and value:
                constraints.append(f"事实约束: {subject}.{predicate}={value}")

        # 7. Extra
        if extra_constraints:
            constraints.extend(extra_constraints)

        return constraints
