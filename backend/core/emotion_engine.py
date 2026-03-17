from __future__ import annotations

import re
from typing import Any, Optional

from .event_store import EventStore


# Emotion dimension ranges: 0.0 ~ 1.0
EMOTION_DIMS = ('affection', 'tension', 'trust', 'comfort')

# Default neutral state
DEFAULT_EMOTION: dict[str, float] = {
    'affection': 0.3,
    'tension': 0.3,
    'trust': 0.4,
    'comfort': 0.5,
}

# Clamp helper
def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ── Rule definitions ────────────────────────────────────────────────
# Each rule: (regex_pattern, {dim: delta, ...})
NARRATIVE_RULES: list[tuple[str, dict[str, float]]] = [
    # Positive interactions
    (r'(微笑|笑了|嘴角上扬|轻笑)', {'affection': 0.05, 'comfort': 0.05, 'tension': -0.03}),
    (r'(大笑|开心地笑|笑出声)', {'affection': 0.08, 'comfort': 0.08, 'tension': -0.05}),
    (r'(感谢|谢谢|多亏了你)', {'trust': 0.06, 'affection': 0.03}),
    (r'(依靠|靠在|依偎)', {'comfort': 0.1, 'affection': 0.06, 'trust': 0.03}),

    # Physical closeness
    (r'(牵手|握住.*手|十指相扣)', {'affection': 0.12, 'tension': 0.05, 'trust': 0.05}),
    (r'(拥抱|抱住|搂)', {'affection': 0.15, 'comfort': 0.1, 'trust': 0.05}),
    (r'(亲吻|吻|嘴唇)', {'affection': 0.2, 'tension': 0.1, 'trust': 0.05}),
    (r'(告白|喜欢你|爱你|表白)', {'affection': 0.25, 'tension': 0.15, 'trust': 0.08}),

    # Negative interactions
    (r'(吵架|争吵|大声)', {'tension': 0.15, 'trust': -0.1, 'comfort': -0.1}),
    (r'(冷战|沉默不语|背过身)', {'tension': 0.1, 'comfort': -0.15, 'affection': -0.05}),
    (r'(欺骗|说谎|隐瞒)', {'trust': -0.2, 'tension': 0.1}),
    (r'(伤害|打了|推开)', {'trust': -0.15, 'affection': -0.1, 'comfort': -0.15}),

    # Emotional distress
    (r'(哭|流泪|红了眼|湿润)', {'tension': 0.08, 'comfort': -0.05}),
    (r'(害怕|恐惧|发抖)', {'tension': 0.12, 'comfort': -0.1}),
    (r'(愤怒|暴怒|发火)', {'tension': 0.15, 'comfort': -0.08, 'trust': -0.05}),

    # Vulnerability / Opening up
    (r'(坦白|说出.*秘密|打开心扉)', {'trust': 0.12, 'affection': 0.05, 'tension': 0.05}),
    (r'(脸红|害羞|不好意思)', {'affection': 0.04, 'tension': 0.03}),

    # Protective / Caring
    (r'(保护|守护|挡在.*前面)', {'trust': 0.1, 'affection': 0.08}),
    (r'(关心|担心|在意)', {'affection': 0.05, 'trust': 0.03}),

    # Calm / Relaxation
    (r'(平静|安心|放松|舒适)', {'comfort': 0.08, 'tension': -0.05}),
    (r'(一起[看赏散]|并肩|相视)', {'comfort': 0.06, 'affection': 0.04}),
]

# Rules based on user choice content
CHOICE_RULES: list[tuple[str, dict[str, float]]] = [
    (r'(安慰|鼓励|陪伴)', {'comfort': 0.08, 'affection': 0.05, 'trust': 0.03}),
    (r'(道歉|认错|对不起)', {'trust': 0.05, 'tension': -0.08}),
    (r'(质问|追问|逼问)', {'tension': 0.1, 'trust': -0.05}),
    (r'(忽视|无视|离开)', {'affection': -0.08, 'comfort': -0.05}),
    (r'(诚实|坦诚|说实话)', {'trust': 0.08}),
    (r'(搞笑|逗.*笑|开玩笑)', {'comfort': 0.05, 'tension': -0.05}),
]


class EmotionEngine:
    """Rule-based emotion simulator for character–protagonist dynamics.

    Priority: deterministic rules → stable, no LLM latency.
    """

    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    def get_emotion(self, session_id: str, character_id: str = 'linxi') -> dict[str, float]:
        """Get current emotion vector for a character in a session."""
        states = self.event_store.get_emotion_state(session_id)
        for s in states:
            if s.get('character_id') == character_id:
                return {d: float(s.get(d, DEFAULT_EMOTION.get(d, 0.5))) for d in EMOTION_DIMS}
        return dict(DEFAULT_EMOTION)

    def update_emotion(
        self,
        session_id: str,
        turn_number: int,
        narrative: str,
        user_input: str,
        character_id: str = 'linxi',
    ) -> dict[str, float]:
        """Apply rule-based deltas from narrative + user input, persist."""
        current = self.get_emotion(session_id, character_id)

        # Narrative-driven deltas
        for pattern, deltas in NARRATIVE_RULES:
            if re.search(pattern, narrative):
                for dim, delta in deltas.items():
                    current[dim] = _clamp(current[dim] + delta)

        # User-choice-driven deltas
        for pattern, deltas in CHOICE_RULES:
            if re.search(pattern, user_input):
                for dim, delta in deltas.items():
                    current[dim] = _clamp(current[dim] + delta)

        # Natural decay toward equilibrium (0.4) — prevents runaway extremes
        for dim in EMOTION_DIMS:
            equilibrium = 0.4
            diff = current[dim] - equilibrium
            current[dim] = _clamp(current[dim] - diff * 0.03)

        # Save
        self.event_store.save_emotion_state(
            session_id=session_id,
            character_id=character_id,
            turn_number=turn_number,
            emotion=current,
        )
        return current

    def get_emotion_description(self, emotion: dict[str, float]) -> str:
        """Generate a human-readable emotion summary for prompt injection."""
        parts: list[str] = []

        aff = emotion.get('affection', 0.3)
        if aff > 0.7:
            parts.append('对主角怀有深厚的好感')
        elif aff > 0.5:
            parts.append('对主角有好感')
        elif aff < 0.2:
            parts.append('对主角较为冷淡')

        tns = emotion.get('tension', 0.3)
        if tns > 0.7:
            parts.append('情绪非常紧张')
        elif tns > 0.5:
            parts.append('略显紧张')

        trt = emotion.get('trust', 0.4)
        if trt > 0.7:
            parts.append('高度信任主角')
        elif trt > 0.5:
            parts.append('信任主角')
        elif trt < 0.2:
            parts.append('对主角心存戒备')

        cmf = emotion.get('comfort', 0.5)
        if cmf > 0.7:
            parts.append('在主角身边很放松')
        elif cmf < 0.3:
            parts.append('在主角身边不太自在')

        return '；'.join(parts) if parts else '情绪状态平稳'
