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
        protagonist_desc: str = "",
        blueprint: dict[str, Any] | None = None,
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

        protagonist_section = ""
        if protagonist_desc:
            protagonist_section = f"\n[男主画像]\n{protagonist_desc}\n叙事中男主的行为、内心活动和对话必须符合此画像，保持性格一致性。\n"

        blueprint_section = ""
        if blueprint:
            bp_parts: list[str] = []
            if blueprint.get("heroine_detail"):
                bp_parts.append(f"林夕详细人设：{blueprint['heroine_detail']}")
            if blueprint.get("opening_scene"):
                bp_parts.append(f"开场设定：{blueprint['opening_scene']}")
            nodes = blueprint.get("story_nodes", [])
            if nodes:
                node_lines = []
                for n in nodes[:8]:
                    node_lines.append(
                        f"  节点{n.get('node_id', '?')}: {n.get('title', '')} — {n.get('description', '')} "
                        f"(情感赌注: {n.get('emotional_stakes', '')})"
                    )
                bp_parts.append("剧情节点预设：\n" + "\n".join(node_lines))
            routes = blueprint.get("route_map", [])
            if routes:
                route_lines = []
                for r in routes[:5]:
                    route_lines.append(f"  {r.get('route_name', '')}: {r.get('tone', '')} — {r.get('ending_preview', '')}")
                bp_parts.append("可能路线：\n" + "\n".join(route_lines))
            if bp_parts:
                blueprint_section = "\n[剧本蓝图]\n" + "\n".join(bp_parts) + "\n"

        # Derive choice generation instructions based on progress
        choice_instructions = self._build_choice_instructions(progress, blueprint)

        return (
            "你是Galgame风格的言情互动小说引擎。必须输出严格JSON，不输出JSON以外任何文字。\n\n"
            "【核心规则】\n"
            "1. 玩家='你'，是男主角。全程第二人称视角叙事（'你走进房间''你看着她'）。\n"
            "2. 林夕是女主角，是这段故事的核心情感对象。\n"
            "3. narrative字段是完整的章节正文，必须至少2000字。\n"
            "4. 正文中要自然穿插对话（用引号标记），有环境描写、心理描写、动作细节、表情描写。\n"
            "5. 对话要符合角色性格，林夕的对话要有个性（按照人设说话）。\n"
            "6. 男主角的行为和反应要符合[男主画像]中的性格特征，不能突然变成另一个人。\n"
            "7. 写作风格：细腻、有画面感、情感张力强，像优质网络言情小说。\n"
            f"{choice_instructions}\n"
            "9. 叙事必须尊重[記憶上下文]和[剧本蓝图]中的已知事实，不得与之矛盾。\n"
            "10. 如果[剧本蓝图]中有剧情节点，叙事应自然推进到最近的未触发节点。\n\n"
            f"【情感状态】tension={tension:.2f} trust={trust:.2f} progress={progress:.2f}\n"
            "  - tension影响场景氛围紧张度\n"
            "  - trust影响林夕对你的态度开放程度\n"
            "  - progress影响关系推进阶段\n\n"
            f"[角色设定]\n{character_text}\n\n"
            f"{protagonist_section}"
            f"{blueprint_section}"
            f"{memory_section}"
            f"[一致性约束]\n{constraints_text}\n\n"
            "[输出JSON格式]\n"
            '{"narrative": "至少2000字的正文，包含描写和对话", '
            '"choices": [{"id": "c1", "title": "选项标题", "description": "简述该走向", "route_hint": "纯爱|支配|决裂|恐惧|救赎|探索"},...共5个], '
            '"state_delta": {"tension": float, "trust": float, "progress": float}}\n\n'
            "重要：narrative必须足够长且内容丰富！choices必须恰好5个且带 route_hint！"
        )

    @staticmethod
    def _build_choice_instructions(progress: float, blueprint: dict[str, Any] | None) -> str:
        """根据当前进度和蓝图，动态调整选项生成指令。"""
        base = (
            "8. 每段正文结束后生成恰好5个选项，每个选项必须包含 route_hint 字段，标记这个选择倾向的路线方向。\n"
            "   选项不是随意的——它们代表截然不同的策略方向：\n"
        )

        if progress < 0.2:
            # Early game: choices about establishing relationship
            base += (
                '   ▸ 当前处于【开局期】，选项应围绕"如何建立第一印象"：\n'
                "     - 至少1个'主动靠近'方向（纯爱/救赎倾向）\n"
                "     - 至少1个'保持距离观察'方向（理性/探索倾向）\n"
                "     - 至少1个'制造冲突或紧张'方向（支配/决裂倾向）\n"
                "     - 选项间差异要大，体现不同的人际策略\n"
            )
        elif progress < 0.5:
            # Mid game: choices about deepening or changing direction
            base += (
                '   ▸ 当前处于【发展期】，选项应围绕"关系向哪个方向深入"：\n'
                "     - 纯爱方向：温柔、坦诚、给予安全感\n"
                "     - 支配方向：掌控、试探、设定边界\n"
                "     - 决裂方向：对抗、质疑、打破现状\n"
                "     - 恐惧方向：揭露秘密、施压、制造不安全感\n"
                "     - 每个选项应有明确的后果预期\n"
            )
        elif progress < 0.8:
            # Late game: high-stakes choices
            base += (
                "   ▸ 当前处于【高潮期】，选项代表不可逆的重大决定：\n"
                "     - 每个选项都应引向不同的结局方向\n"
                "     - 选项的后果描述要更具体，暗示可能的结局走向\n"
                "     - 允许出现风险极高的'赌注型'选项\n"
            )
        else:
            # Endgame
            base += (
                "   ▸ 当前处于【收束期】，选项决定最终结局：\n"
                "     - 选项应直接对应不同的结局路线\n"
                "     - 每个选项的 description 明确暗示将导致的结局类型\n"
            )

        if blueprint and blueprint.get("story_nodes"):
            base += "   ▸ 参考[剧本蓝图]中的节点分支设计生成选项\n"

        return base
