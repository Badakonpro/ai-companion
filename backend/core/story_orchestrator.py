from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

import httpx


class StoryOrchestrator:
    def __init__(
        self,
        ollama_chat_url: str,
        model_name: str,
        timeout_seconds: float,
    ) -> None:
        self.ollama_chat_url = ollama_chat_url
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _extract_json_block(text: str) -> dict[str, Any] | None:
        # Strip <think> blocks
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

        # Strip code fences
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 3:
                cleaned = "\n".join(lines[1:-1]).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _fallback_output(user_input: str) -> dict[str, Any]:
        return {
            "narrative": (
                "窗外的雨不知道什么时候停了，空气里残留着潮湿泥土的气息。你站在走廊尽头，"
                "看着林夕的背影消失在转角。\n\n"
                "刚才的对话还在脑海里回荡。她说话的时候没有看你，指尖无意识地绕着一缕头发，"
                "那是她不安时才会有的小动作——你不知道从什么时候开始注意到这些细节的。\n\n"
                "走廊里很安静，只有远处偶尔传来的人声。你低头看了看手机，屏幕上还停留在她发来的那条消息。"
                "已读，但你一直没有回复。\n\n"
                "\"想什么呢？\"身后突然传来她的声音。你转过身，发现林夕不知道什么时候又折了回来，"
                "靠在墙上，双手抱臂，表情看不出情绪。\n\n"
                "\"没什么。\"你说。\n\n"
                "她挑了挑眉，那个表情你很熟悉——意思是'你在骗谁呢'。但她没有追问，只是偏了偏头，"
                "目光从你脸上滑过，落在你身后的窗户外面。\n\n"
                "\"雨停了。\"她说，语气很轻。\n\n"
                "几秒钟的沉默。风从半开的窗户灌进来，带着外面湿冷的空气，"
                f"你注意到她的手指微微缩了一下。{user_input}"
            ),
            "choices": [
                {"id": "c1", "title": "脱下外套披在她身上", "description": "用行动代替言语，拉近距离。", "route_hint": "纯爱"},
                {"id": "c2", "title": "假装没注意，聊点别的", "description": "维持现状，不打破微妙的平衡。", "route_hint": "探索"},
                {"id": "c3", "title": "直接问她为什么折回来", "description": "正面出击，逼她说出真实想法。", "route_hint": "支配"},
                {"id": "c4", "title": "走过去站在她旁边沉默", "description": "无声陪伴，让她自己选择是否开口。", "route_hint": "救赎"},
                {"id": "c5", "title": "提议一起去吃点东西", "description": "转移场景，创造更轻松的氛围。", "route_hint": "探索"},
            ],
            "state_delta": {"tension": 0.05, "trust": 0.0, "progress": 0.03},
        }

    async def generate_turn(
        self,
        session_id: str,
        user_input: str,
        prior_messages: list[dict[str, str]],
        session_state: dict[str, float] | None = None,
        memory_facts: list[dict[str, Any]] | None = None,
        consistency_constraints: list[str] | None = None,
        dynamic_system_prompt: str | None = None,
        active_character_ids: list[str] | None = None,
        world_seed: str | None = None,
        selected_choice_id: str | None = None,
    ) -> dict[str, Any]:
        seed = world_seed or "现代都市言情背景。"
        selected = selected_choice_id or "none"
        state = session_state or {"tension": 0.0, "trust": 0.0, "progress": 0.0}
        facts = memory_facts or []
        constraints = consistency_constraints or []

        system_prompt = dynamic_system_prompt or self._default_system_prompt()

        compact_history = prior_messages[-10:]
        user_prompt = json.dumps(
            {
                "session_id": session_id,
                "world_seed": seed,
                "selected_choice_id": selected,
                "session_state": state,
                "memory_facts": facts[:12],
                "history": compact_history,
                "user_input": user_input,
                "constraints": constraints,
            },
            ensure_ascii=False,
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt + "\n/no_think"},
            ],
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.ollama_chat_url, json=payload)
            response.raise_for_status()

        content = response.json().get("message", {}).get("content", "")
        parsed = self._extract_json_block(content)
        if not parsed:
            parsed = self._fallback_output(user_input)

        narrative = str(parsed.get("narrative", "")).strip()
        if not narrative or len(narrative) < 100:
            narrative = self._fallback_output(user_input)["narrative"]

        # Merge protagonist_action into narrative if still present (backward compat)
        protagonist_action = str(parsed.get("protagonist_action", "")).strip()
        if protagonist_action and protagonist_action not in narrative:
            narrative = narrative + "\n\n" + protagonist_action

        choices_raw = parsed.get("choices", [])
        state_delta_raw = parsed.get("state_delta", {})

        normalized_choices: list[dict[str, str]] = []
        if isinstance(choices_raw, list):
            for idx, choice in enumerate(choices_raw[:6]):
                if not isinstance(choice, dict):
                    continue
                title = str(choice.get("title", "")).strip()
                if not title:
                    continue
                normalized_choices.append(
                    {
                        "id": str(choice.get("id") or f"choice_{idx + 1}"),
                        "title": title,
                        "description": str(choice.get("description", "")).strip(),
                        "route_hint": str(choice.get("route_hint", "")).strip(),
                    }
                )

        if len(normalized_choices) < 3:
            normalized_choices = self._fallback_output(user_input)["choices"]

        state_delta = state_delta_raw if isinstance(state_delta_raw, dict) else {}

        return {
            "session_id": session_id,
            "narrative": narrative,
            "protagonist_action": "",
            "choices": normalized_choices,
            "state_delta": state_delta,
            "model": self.model_name,
        }

    async def generate_turn_stream(
        self,
        session_id: str,
        user_input: str,
        prior_messages: list[dict[str, str]],
        session_state: dict[str, float] | None = None,
        memory_facts: list[dict[str, Any]] | None = None,
        consistency_constraints: list[str] | None = None,
        dynamic_system_prompt: str | None = None,
        active_character_ids: list[str] | None = None,
        world_seed: str | None = None,
        selected_choice_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield SSE-friendly dicts: narrative_chunk / meta (choices, state_delta)."""
        seed = world_seed or "现代都市言情背景。"
        selected = selected_choice_id or "none"
        state = session_state or {"tension": 0.0, "trust": 0.0, "progress": 0.0}
        facts = memory_facts or []
        constraints = consistency_constraints or []

        system_prompt = dynamic_system_prompt or self._default_system_prompt()
        compact_history = prior_messages[-10:]
        user_prompt = json.dumps(
            {
                "session_id": session_id,
                "world_seed": seed,
                "selected_choice_id": selected,
                "session_state": state,
                "memory_facts": facts[:12],
                "history": compact_history,
                "user_input": user_input,
                "constraints": constraints,
            },
            ensure_ascii=False,
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt + "\n/no_think"},
            ],
            "stream": True,
        }

        full_content = ""
        # Track whether we're inside the "narrative" JSON value to stream only narrative tokens
        in_narrative = False
        narrative_started = False
        escape_next = False

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream("POST", self.ollama_chat_url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk_data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = chunk_data.get("message", {}).get("content", "")
                    if not token:
                        continue
                    full_content += token

                    # Detect entry into "narrative" value
                    if not narrative_started:
                        if '"narrative"' in full_content:
                            # Find the opening quote of the narrative value
                            idx = full_content.find('"narrative"')
                            rest = full_content[idx + len('"narrative"'):]
                            # Skip : and whitespace to find the opening "
                            stripped = rest.lstrip()
                            if stripped.startswith(':'):
                                after_colon = stripped[1:].lstrip()
                                if after_colon.startswith('"'):
                                    in_narrative = True
                                    narrative_started = True
                                    # Yield any text after the opening quote
                                    content_start = full_content.find('"narrative"')
                                    # Find the actual opening quote position
                                    search_from = content_start + len('"narrative"')
                                    colon_pos = full_content.index(':', search_from)
                                    quote_pos = full_content.index('"', colon_pos + 1)
                                    narrative_so_far = full_content[quote_pos + 1:]
                                    # Check if narrative already ended
                                    unescaped_end = self._find_unescaped_quote(narrative_so_far)
                                    if unescaped_end >= 0:
                                        narrative_text = narrative_so_far[:unescaped_end]
                                        if narrative_text:
                                            yield {"type": "token", "content": self._unescape_json_str(narrative_text)}
                                        in_narrative = False
                                    elif narrative_so_far:
                                        yield {"type": "token", "content": self._unescape_json_str(narrative_so_far)}
                        continue

                    if in_narrative:
                        # Check if the narrative value has ended (unescaped closing quote)
                        # We only care about the new token
                        test = self._find_unescaped_quote(token)
                        if test >= 0:
                            # Narrative ended in this token
                            before = token[:test]
                            if before:
                                yield {"type": "token", "content": self._unescape_json_str(before)}
                            in_narrative = False
                        else:
                            yield {"type": "token", "content": self._unescape_json_str(token)}

        # Parse completed content
        parsed = self._extract_json_block(full_content)
        if not parsed:
            parsed = self._fallback_output(user_input)

        narrative = str(parsed.get("narrative", "")).strip()
        if not narrative or len(narrative) < 100:
            parsed = self._fallback_output(user_input)
            narrative = parsed["narrative"]

        protagonist_action = str(parsed.get("protagonist_action", "")).strip()
        if protagonist_action and protagonist_action not in narrative:
            narrative = narrative + "\n\n" + protagonist_action

        choices_raw = parsed.get("choices", [])
        state_delta_raw = parsed.get("state_delta", {})

        normalized_choices: list[dict[str, str]] = []
        if isinstance(choices_raw, list):
            for idx, choice in enumerate(choices_raw[:6]):
                if not isinstance(choice, dict):
                    continue
                title = str(choice.get("title", "")).strip()
                if not title:
                    continue
                normalized_choices.append(
                    {
                        "id": str(choice.get("id") or f"choice_{idx + 1}"),
                        "title": title,
                        "description": str(choice.get("description", "")).strip(),
                        "route_hint": str(choice.get("route_hint", "")).strip(),
                    }
                )

        if len(normalized_choices) < 3:
            normalized_choices = self._fallback_output(user_input)["choices"]

        state_delta = state_delta_raw if isinstance(state_delta_raw, dict) else {}

        yield {
            "type": "meta",
            "session_id": session_id,
            "narrative": narrative,
            "protagonist_action": "",
            "choices": normalized_choices,
            "state_delta": state_delta,
            "model": self.model_name,
        }

    @staticmethod
    def _find_unescaped_quote(s: str) -> int:
        """Return index of first unescaped double-quote, or -1."""
        i = 0
        while i < len(s):
            if s[i] == '\\':
                i += 2  # skip escaped char
                continue
            if s[i] == '"':
                return i
            i += 1
        return -1

    @staticmethod
    def _unescape_json_str(s: str) -> str:
        """Unescape common JSON string escapes."""
        return (
            s.replace('\\n', '\n')
            .replace('\\t', '\t')
            .replace('\\"', '"')
            .replace('\\\\', '\\')
        )

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "你是一个Galgame风格的言情互动小说引擎。输出严格JSON，不输出JSON以外内容。\n"
            "【核心设定】\n"
            "- 玩家='你'，是男主角，第二人称视角叙事\n"
            "- 林夕是女主角\n"
            "- narrative字段包含完整章节正文（描写+对话混合），至少2000字\n"
            "- choices是5个走向选项，决定下一步剧情方向\n\n"
            "JSON schema:\n"
            '{"narrative": string, "choices": [{"id": string, "title": string, "description": string}], "state_delta": {"tension": number, "trust": number, "progress": number}}\n'
            "choices必须是5项，代表不同的剧情走向方向。"
        )
