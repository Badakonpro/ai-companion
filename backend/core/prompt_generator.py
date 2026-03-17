from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PromptGenerator:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.characters = self._load_characters()

    def _load_characters(self) -> dict[str, dict[str, Any]]:
        if not self.config_path.exists():
            return {}

        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        result: dict[str, dict[str, Any]] = {}
        for item in data.get("characters", []):
            character_id = str(item.get("id", "")).strip()
            if not character_id:
                continue
            result[character_id] = item
        return result

    def list_characters(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for character in self.characters.values():
            output.append(
                {
                    "id": character.get("id", ""),
                    "name": character.get("name", ""),
                    "archetype": character.get("archetype", ""),
                    "personality": character.get("personality", ""),
                }
            )
        return output

    def resolve_active_characters(self, active_character_ids: list[str] | None) -> list[dict[str, Any]]:
        if not active_character_ids:
            default_ids = ["protagonist", "linxi"]
            return [self.characters[cid] for cid in default_ids if cid in self.characters]

        selected: list[dict[str, Any]] = []
        for cid in active_character_ids[:3]:
            if cid in self.characters:
                selected.append(self.characters[cid])
        return selected

    def build_system_prompt(
        self,
        session_state: dict[str, float],
        active_character_ids: list[str] | None,
        constraints: list[str],
        memory_block: str = "",
    ) -> str:
        active = self.resolve_active_characters(active_character_ids)

        character_blocks: list[str] = []
        for c in active:
            tabs = c.get("taboos", [])
            taboo_text = "、".join(str(tab) for tab in tabs) if isinstance(tabs, list) else ""
            character_blocks.append(
                "\n".join(
                    [
                        f"【{c.get('name', '')}】({c.get('archetype', '')})",
                        f"  性格: {c.get('personality', '')}",
                        f"  说话风格: {c.get('speech_style', '')}",
                        f"  动机: {c.get('motivation', '')}",
                        f"  禁忌: {taboo_text}",
                    ]
                )
            )

        tension = session_state.get("tension", 0.0)
        trust = session_state.get("trust", 0.0)
        progress = session_state.get("progress", 0.0)

        constraints_text = "\n".join(f"- {item}" for item in constraints[:20])
        character_text = "\n\n".join(character_blocks)

        memory_section = ""
        if memory_block:
            memory_section = f"\n[記憶上下文]\n{memory_block}\n"

        return (
            "你是Galgame风格的言情互动小说引擎。必须输出严格JSON，不输出JSON以外任何文字。\n\n"
            "【核心规则】\n"
            "1. 玩家='你'，是男主角。全程第二人称视角叙事（'你走进房间''你看着她'）。\n"
            "2. 林夕是女主角，是这段故事的核心情感对象。\n"
            "3. narrative字段是完整的章节正文，必须至少2000字。\n"
            "4. 正文中要自然穿插对话（用引号标记），有环境描写、心理描写、动作细节、表情描写。\n"
            "5. 对话要符合角色性格，林夕的对话要有个性（冷淡、毒舌、害羞时口是心非）。\n"
            "6. 写作风格：细腻、有画面感、情感张力强，像优质网络言情小说。\n"
            "7. 每段正文结束后生成恰好5个选项，每个选项代表不同的剧情走向/行动方向。\n"
            "8. 选项不是对话选择，而是'接下来你打算做什么'的行动方向。\n"
            "9. 5个选项应涵盖不同风格：有大胆的、保守的、浪漫的、理性的、意外的。\n"
            "10. 叙事必须尊重[記憶上下文]中的已知事实和角色情感状态，不得与之矛盾。\n\n"
            f"【情感状态】tension={tension:.2f} trust={trust:.2f} progress={progress:.2f}\n"
            "  - tension影响场景氛围紧张度\n"
            "  - trust影响林夕对你的态度开放程度\n"
            "  - progress影响关系推进阶段\n\n"
            f"[角色设定]\n{character_text}\n\n"
            f"{memory_section}"
            f"[一致性约束]\n{constraints_text}\n\n"
            "[输出JSON格式]\n"
            '{"narrative": "至少2000字的正文，包含描写和对话", '
            '"choices": [{"id": "c1", "title": "选项标题", "description": "简述该走向"},...共5个], '
            '"state_delta": {"tension": float, "trust": float, "progress": float}}\n\n'
            "重要：narrative必须足够长且内容丰富！choices必须恰好5个！"
        )
