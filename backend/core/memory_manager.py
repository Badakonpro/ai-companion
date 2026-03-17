from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

from .event_store import EventStore


class MemoryManager:
    """Multi-layer memory system: STM, EM, SM, AEM.

    STM  = Short-Term Memory   – recent N turns verbatim
    EM   = Episodic Memory     – key event timeline
    SM   = Semantic Memory     – character relations & world fact triples
    AEM  = Affective Memory    – emotion vector trajectory (delegated to EmotionEngine)
    """

    # How many recent turns to keep as STM
    STM_WINDOW = 6
    # How many turns between automatic summarisation
    SUMMARIZE_EVERY_K = 5
    # Max episodic events to keep
    MAX_EPISODES = 50
    # Max semantic facts to keep
    MAX_FACTS = 60

    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    # ── STM: Short-Term Memory ──────────────────────────────────────

    def get_stm(self, session_id: str, window: Optional[int] = None) -> list[dict[str, Any]]:
        """Return the most recent N turns as raw text for direct context."""
        n = window or self.STM_WINDOW
        history = self.event_store.get_session_history(session_id)
        return history[-n:] if len(history) > n else history

    # ── EM: Episodic Memory ─────────────────────────────────────────

    def get_episodes(self, session_id: str) -> list[dict[str, Any]]:
        return self.event_store.get_episodes(session_id)

    def add_episode(
        self,
        session_id: str,
        turn_number: int,
        event_type: str,
        summary: str,
        participants: list[str],
        significance: float = 0.5,
    ) -> None:
        self.event_store.add_episode(
            session_id=session_id,
            turn_number=turn_number,
            event_type=event_type,
            summary=summary,
            participants=participants,
            significance=significance,
        )

    def extract_episodes_from_narrative(
        self, session_id: str, turn_number: int, narrative: str
    ) -> list[dict[str, Any]]:
        """Rule-based extraction of key events from narrative text."""
        episodes: list[dict[str, Any]] = []

        # Detect significant emotional events
        emotion_patterns = [
            (r'(林夕|她).{0,20}(哭|流泪|红了眼|湿润)', 'emotion_event', 0.8),
            (r'(林夕|她).{0,20}(笑|微笑|大笑|嘴角)', 'emotion_event', 0.5),
            (r'(林夕|她).{0,20}(生气|愤怒|发火|怒)', 'emotion_event', 0.7),
            (r'(林夕|她).{0,20}(脸红|害羞|不好意思)', 'emotion_event', 0.6),
        ]

        # Detect relationship milestones
        relationship_patterns = [
            (r'(牵手|握住.*手|十指相扣)', 'relationship_milestone', 0.8),
            (r'(拥抱|抱住|搂)', 'relationship_milestone', 0.8),
            (r'(亲吻|吻|嘴唇)', 'relationship_milestone', 0.9),
            (r'(告白|喜欢你|爱你|表白)', 'relationship_milestone', 1.0),
            (r'(吵架|争吵|冷战|分开)', 'conflict_event', 0.8),
        ]

        # Detect location changes
        location_patterns = [
            (r'(来到|走进|到达|抵达|走入)了?([^，。！？\n]{2,10})', 'location_change', 0.4),
        ]

        # Detect plot events
        plot_patterns = [
            (r'(发现|得知|看到|听到)了?(.{5,30}秘密)', 'plot_revelation', 0.7),
            (r'(突然|忽然|意外).{0,20}(出现|发生|响起)', 'plot_twist', 0.6),
            (r'(决定|下定决心|打算)', 'decision_point', 0.5),
        ]

        all_patterns = emotion_patterns + relationship_patterns + location_patterns + plot_patterns
        seen_summaries: set[str] = set()

        for pattern, event_type, significance in all_patterns:
            for match in re.finditer(pattern, narrative):
                summary = match.group(0).strip()[:80]
                if summary in seen_summaries:
                    continue
                seen_summaries.add(summary)

                participants = []
                text = match.group(0)
                if re.search(r'林夕|她', text):
                    participants.append('linxi')
                if re.search(r'你|我', text):
                    participants.append('protagonist')

                episode = {
                    'turn_number': turn_number,
                    'event_type': event_type,
                    'summary': summary,
                    'participants': participants or ['protagonist', 'linxi'],
                    'significance': significance,
                }
                episodes.append(episode)
                self.add_episode(session_id, **episode)

        return episodes

    # ── SM: Semantic Memory ─────────────────────────────────────────

    def get_semantic_facts(self, session_id: str) -> list[dict[str, Any]]:
        return self.event_store.get_session_facts(session_id)

    def extract_semantic_facts(self, narrative: str) -> list[dict[str, Any]]:
        """Enhanced fact extraction: relations, traits, locations, possessions."""
        facts: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def _add(subject: str, predicate: str, value: str, confidence: float) -> None:
            key = (subject, predicate)
            if key in seen:
                return
            seen.add(key)
            facts.append({
                'subject': subject,
                'predicate': predicate,
                'value': value.strip()[:60],
                'confidence': confidence,
            })

        # Location facts
        for subj, loc in re.findall(r'(你|林夕|主角)(?:来到|走进|在|到了)([^，。！？\n]{2,15})', narrative):
            _add(subj, 'location', loc, 0.7)

        # Emotion facts
        for subj, emo in re.findall(r'(你|林夕|主角)(?:显得|看起来|变得|感到)?很?([^，。！？\n]{1,8}(?:开心|难过|生气|害羞|紧张|不安|高兴|烦躁|平静|伤心|激动|尴尬))', narrative):
            _add(subj, 'emotion', emo, 0.6)

        # Relationship attitude
        for pattern, pred, val, conf in [
            (r'林夕.{0,15}(信任|相信|依赖)', 'linxi', 'trust_protagonist', 0.7),
            (r'林夕.{0,15}(讨厌|厌恶|反感)', 'linxi', 'dislike_protagonist', 0.7),
            (r'林夕.{0,15}(在意|关心|担心)', 'linxi', 'cares_protagonist', 0.6),
            (r'(你|主角).{0,15}(在意|关心|担心).{0,5}林夕', 'protagonist', 'cares_linxi', 0.6),
        ]:
            if re.search(pattern, narrative):
                _add(pred, 'attitude', val, conf)

        # Time/setting facts
        for time_word in re.findall(r'(清晨|早上|上午|中午|下午|傍晚|晚上|深夜|凌晨)', narrative):
            _add('scene', 'time_of_day', time_word, 0.5)

        for weather in re.findall(r'(下雨|阴天|晴天|下雪|大风|雾|闷热)', narrative):
            _add('scene', 'weather', weather, 0.5)

        return facts

    # ── Summary & Tidying ───────────────────────────────────────────

    def should_summarize(self, session_id: str) -> bool:
        """Check if we've accumulated enough turns to trigger summarisation."""
        history = self.event_store.get_session_history(session_id)
        episodes = self.event_store.get_episodes(session_id)
        # Summarise if we have enough turns and no recent summary in episodes
        if len(history) < self.SUMMARIZE_EVERY_K:
            return False
        if len(history) % self.SUMMARIZE_EVERY_K == 0:
            return True
        return False

    def build_summary_prompt(self, session_id: str) -> Optional[str]:
        """Build a prompt for the AI to compress recent history into a summary.
        Returns None if no summarisation needed."""
        if not self.should_summarize(session_id):
            return None

        history = self.event_store.get_session_history(session_id)
        if not history:
            return None

        # Get the last K turns for summarisation
        recent = history[-self.SUMMARIZE_EVERY_K:]
        text_block = ""
        for turn in recent:
            text_block += f"[玩家] {turn['user_input']}\n"
            text_block += f"[叙事] {turn['narrative'][:500]}...\n\n"

        episodes = self.event_store.get_episodes(session_id)
        episode_text = ""
        for ep in episodes[-10:]:
            episode_text += f"  - 第{ep['turn_number']}回: [{ep['event_type']}] {ep['summary']}\n"

        facts = self.event_store.get_session_facts(session_id)
        fact_text = ""
        for f in facts[:15]:
            fact_text += f"  - {f['subject']}.{f['predicate']} = {f['value']}\n"

        return (
            "请阅读以下最近几回合的叙事内容，输出严格JSON，包含以下字段：\n"
            "1. summary: 50-100字的情节概要\n"
            "2. new_facts: 新发现的事实三元组数组 [{subject, predicate, value}]\n"
            "3. relationship_change: 角色关系的变化描述（一句话）\n"
            "4. key_events: 关键事件数组 [{summary, significance}]\n\n"
            f"【已知事件线】\n{episode_text}\n"
            f"【已知事实】\n{fact_text}\n"
            f"【最近叙事】\n{text_block}\n"
            "请只输出JSON，不要输出其他文字。 /no_think"
        )

    def apply_summary_result(self, session_id: str, parsed: dict[str, Any], turn_number: int) -> None:
        """Apply AI-generated summary results to memory stores."""
        # Add summary as an episode
        summary_text = str(parsed.get('summary', '')).strip()
        if summary_text:
            self.add_episode(
                session_id=session_id,
                turn_number=turn_number,
                event_type='summary',
                summary=summary_text,
                participants=['protagonist', 'linxi'],
                significance=0.3,
            )

        # Merge new facts
        new_facts = parsed.get('new_facts', [])
        if isinstance(new_facts, list):
            existing = self.event_store.get_session_facts(session_id)
            for nf in new_facts:
                if isinstance(nf, dict) and nf.get('subject') and nf.get('predicate') and nf.get('value'):
                    existing.append({
                        'subject': str(nf['subject']),
                        'predicate': str(nf['predicate']),
                        'value': str(nf['value']),
                        'confidence': 0.8,
                    })
            # Deduplicate by (subject, predicate)
            merged: dict[tuple[str, str], dict[str, Any]] = {}
            for f in existing:
                key = (str(f.get('subject', '')), str(f.get('predicate', '')))
                merged[key] = f
            self.event_store.replace_session_facts(session_id, list(merged.values()))

        # Add key events
        key_events = parsed.get('key_events', [])
        if isinstance(key_events, list):
            for ke in key_events:
                if isinstance(ke, dict) and ke.get('summary'):
                    self.add_episode(
                        session_id=session_id,
                        turn_number=turn_number,
                        event_type='key_event',
                        summary=str(ke['summary'])[:80],
                        participants=['protagonist', 'linxi'],
                        significance=float(ke.get('significance', 0.6)),
                    )

    # ── Context Assembly ────────────────────────────────────────────

    def assemble_memory_context(self, session_id: str) -> dict[str, Any]:
        """Gather all memory layers into a dict for prompt injection."""
        stm = self.get_stm(session_id)
        episodes = self.get_episodes(session_id)
        facts = self.get_semantic_facts(session_id)
        emotion_state = self.event_store.get_emotion_state(session_id)

        return {
            'stm': stm,
            'episodes': episodes,
            'facts': facts,
            'emotion': emotion_state,
        }
