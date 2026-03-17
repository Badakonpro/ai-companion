"""Protagonist Profiler — 男主画像分析模块

通过累积分析玩家的选择和输入文本，构建男主角的性格画像，
使叙事引擎能生成符合男主行为模式的剧情。
"""
from __future__ import annotations

import re
from typing import Any

from .event_store import EventStore

# ── 性格维度 (0.0 ~ 1.0) ────────────────────────────────────────────
TRAIT_DIMS = (
    'boldness',      # 大胆 vs 保守
    'warmth',        # 温柔体贴 vs 冷漠疏离
    'dominance',     # 主导控制 vs 顺从配合
    'honesty',       # 坦诚直接 vs 迂回隐瞒
    'rationality',   # 理性冷静 vs 感性冲动
)

DEFAULT_PROFILE: dict[str, float] = {
    'boldness':    0.5,
    'warmth':      0.5,
    'dominance':   0.5,
    'honesty':     0.5,
    'rationality': 0.5,
}

# ── 从玩家选择文本推断特征 ─────────────────────────────────────────────
CHOICE_TRAIT_RULES: list[tuple[str, dict[str, float]]] = [
    # Bold / aggressive
    (r'(直接|正面|大胆|主动|表白|告白|冲上去|拦住)', {'boldness': 0.08, 'dominance': 0.04}),
    (r'(质问|逼问|追问|抓住|对质|揭穿)', {'boldness': 0.06, 'dominance': 0.06, 'honesty': 0.04}),

    # Conservative / cautious
    (r'(观察|等待|沉默|不动|假装|回避|保持距离)', {'boldness': -0.06, 'rationality': 0.04}),
    (r'(犹豫|考虑|再想想|算了)', {'boldness': -0.04, 'rationality': 0.03}),

    # Warm / caring
    (r'(安慰|关心|陪伴|温柔|体贴|照顾)', {'warmth': 0.08, 'dominance': -0.03}),
    (r'(道歉|认错|对不起|是我的错)', {'warmth': 0.05, 'honesty': 0.05}),
    (r'(拥抱|抱住|牵手|披上外套)', {'warmth': 0.06, 'boldness': 0.03}),

    # Cold / distant
    (r'(无视|忽视|离开|转身走|不理)', {'warmth': -0.08, 'dominance': 0.03}),
    (r'(冷淡|敷衍|随便|无所谓)', {'warmth': -0.05, 'rationality': 0.02}),

    # Dominant / controlling
    (r'(命令|要求|必须|你给我|听我说)', {'dominance': 0.08, 'boldness': 0.04}),
    (r'(决定|掌控|带她|拉住她|不准)', {'dominance': 0.06, 'boldness': 0.03}),

    # Submissive / adaptive
    (r'(听她的|配合|顺从|好吧|你说了算)', {'dominance': -0.06, 'warmth': 0.02}),
    (r'(妥协|让步|退让|算你赢)', {'dominance': -0.04}),

    # Honest / direct
    (r'(坦白|说实话|诚实|直说|不隐瞒|承认)', {'honesty': 0.08, 'boldness': 0.03}),

    # Evasive / deceptive
    (r'(隐瞒|说谎|编造|找借口|绕开话题)', {'honesty': -0.08, 'rationality': 0.02}),

    # Rational
    (r'(分析|冷静|理性|仔细想|客观)', {'rationality': 0.06}),
    (r'(计划|策略|安排|筹备)', {'rationality': 0.05, 'dominance': 0.03}),

    # Impulsive / emotional
    (r'(冲动|不管了|管不了|凭感觉)', {'rationality': -0.06, 'boldness': 0.04}),
    (r'(吼|发火|摔|砸)', {'rationality': -0.08, 'boldness': 0.05, 'warmth': -0.05}),
]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


class ProtagonistProfiler:
    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def get_profile(self, session_id: str) -> dict[str, float]:
        """读取当前会话的男主画像，不存在则返回默认值。"""
        stored = self.event_store.get_protagonist_profile(session_id)
        if stored:
            return stored
        return dict(DEFAULT_PROFILE)

    def update_from_choice(self, session_id: str, choice_text: str, user_input: str) -> dict[str, float]:
        """根据玩家选择和自由输入文本更新画像。"""
        profile = self.get_profile(session_id)
        combined = f"{choice_text} {user_input}"

        for pattern, deltas in CHOICE_TRAIT_RULES:
            if re.search(pattern, combined):
                for dim, delta in deltas.items():
                    if dim in profile:
                        # 越极端越难继续偏移（阻尼衰减）
                        current = profile[dim]
                        if delta > 0:
                            effective = delta * (1.0 - current * 0.3)
                        else:
                            effective = delta * (1.0 - (1.0 - current) * 0.3)
                        profile[dim] = _clamp(profile[dim] + effective)

        self.event_store.save_protagonist_profile(session_id, profile)
        return profile

    def describe_protagonist(self, profile: dict[str, float]) -> str:
        """将画像数值转化为自然语言描述，注入到系统提示中。"""
        parts: list[str] = []

        b = profile.get('boldness', 0.5)
        if b > 0.7:
            parts.append('行事果断大胆，倾向主动出击')
        elif b > 0.55:
            parts.append('偏向积极但会衡量风险')
        elif b < 0.3:
            parts.append('性格谨慎内敛，习惯观望后再行动')
        elif b < 0.45:
            parts.append('偏向保守，不喜欢冒险')

        w = profile.get('warmth', 0.5)
        if w > 0.7:
            parts.append('对人温柔体贴，善于照顾他人情绪')
        elif w > 0.55:
            parts.append('有一定体贴心，但不过分热情')
        elif w < 0.3:
            parts.append('情感表达淡漠，不擅长也不喜欢示好')
        elif w < 0.45:
            parts.append('偏冷淡，不太主动表达关心')

        d = profile.get('dominance', 0.5)
        if d > 0.7:
            parts.append('习惯掌控局面，有较强的控制欲')
        elif d > 0.55:
            parts.append('偏向主导但能接受他人意见')
        elif d < 0.3:
            parts.append('性格柔和，更愿意顺应对方意愿')
        elif d < 0.45:
            parts.append('不太强势，倾向于配合')

        h = profile.get('honesty', 0.5)
        if h > 0.7:
            parts.append('为人坦诚直接，不喜欢遮掩')
        elif h < 0.3:
            parts.append('善于掩饰真实想法，常常回避直面问题')

        r = profile.get('rationality', 0.5)
        if r > 0.7:
            parts.append('冷静理性，遇事先分析后行动')
        elif r < 0.3:
            parts.append('感性冲动，容易被情绪驱使行动')

        if not parts:
            return '男主角性格尚未明确，正在通过玩家的选择逐渐成形。'

        return '男主角的行为画像：' + '；'.join(parts) + '。'
