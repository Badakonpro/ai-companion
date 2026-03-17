from __future__ import annotations

from typing import Any, Optional
import asyncio
import json
import time as _time_mod
from functools import partial
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse

try:
    from .config import (
        ALLOW_ALL_ORIGINS,
        BACKEND_HOST,
        BACKEND_PORT,
        BACKEND_RELOAD,
        FRONTEND_ORIGINS,
        OLLAMA_CHAT_URL,
        OLLAMA_MODEL,
        REQUEST_TIMEOUT_SECONDS,
        SQLITE_DB_PATH,
        ollama_base_url,
        EMBEDDING_MODEL,
        EMBEDDING_DIM,
        DEPLOY_MODE,
        MAX_INPUT_LENGTH,
        RATE_LIMIT_PER_MINUTE,
        ALERT_P95_MS,
        ALERT_ERROR_RATE,
        APP_VERSION,
    )
    from .core.event_store import EventStore
    from .core.consistency_guard import ConsistencyGuard
    from .core.prompt_generator import PromptGenerator
    from .core.story_orchestrator import StoryOrchestrator
    from .core.memory_manager import MemoryManager
    from .core.emotion_engine import EmotionEngine
    from .core.context_budgeter import ContextBudgeter
    from .core.vector_store import VectorStore
    from .core.arc_manager import ArcManager
    from .core.perf_cache import l1_cache
    from .core.perf_metrics import perf
    from .core.rate_limiter import RateLimiter
    from .core.protagonist_profiler import ProtagonistProfiler
    from .persona import SYSTEM_PROMPT
    from .config import STORAGE_DIR
    from .logger import logger
except ImportError:
    from config import (  # type: ignore
        ALLOW_ALL_ORIGINS,
        BACKEND_HOST,
        BACKEND_PORT,
        BACKEND_RELOAD,
        FRONTEND_ORIGINS,
        OLLAMA_CHAT_URL,
        OLLAMA_MODEL,
        REQUEST_TIMEOUT_SECONDS,
        SQLITE_DB_PATH,
        ollama_base_url,
        EMBEDDING_MODEL,
        EMBEDDING_DIM,
        DEPLOY_MODE,
        MAX_INPUT_LENGTH,
        RATE_LIMIT_PER_MINUTE,
        ALERT_P95_MS,
        ALERT_ERROR_RATE,
        APP_VERSION,
    )
    from core.event_store import EventStore  # type: ignore
    from core.consistency_guard import ConsistencyGuard  # type: ignore
    from core.prompt_generator import PromptGenerator  # type: ignore
    from core.story_orchestrator import StoryOrchestrator  # type: ignore
    from core.memory_manager import MemoryManager  # type: ignore
    from core.emotion_engine import EmotionEngine  # type: ignore
    from core.context_budgeter import ContextBudgeter  # type: ignore
    from core.vector_store import VectorStore  # type: ignore
    from core.arc_manager import ArcManager  # type: ignore
    from core.perf_cache import l1_cache  # type: ignore
    from core.perf_metrics import perf  # type: ignore
    from core.rate_limiter import RateLimiter  # type: ignore
    from core.protagonist_profiler import ProtagonistProfiler  # type: ignore
    from persona import SYSTEM_PROMPT  # type: ignore
    from config import STORAGE_DIR  # type: ignore
    from logger import logger  # type: ignore

app = FastAPI(title="AI Persona MVP")
event_store = EventStore(SQLITE_DB_PATH)
story_orchestrator = StoryOrchestrator(OLLAMA_CHAT_URL, OLLAMA_MODEL, REQUEST_TIMEOUT_SECONDS)

# Runtime-mutable active model (starts from config default)
_active_model: str = OLLAMA_MODEL
consistency_guard = ConsistencyGuard()
prompt_generator = PromptGenerator(STORAGE_DIR / "character_config.json")
memory_manager = MemoryManager(event_store)
emotion_engine = EmotionEngine(event_store)
context_budgeter = ContextBudgeter()
vector_store = VectorStore(
    db_path=STORAGE_DIR / "vectors.db",
    embedding_model=EMBEDDING_MODEL,
    embedding_dim=EMBEDDING_DIM,
    ollama_base_url=ollama_base_url(),
)
arc_manager = ArcManager(event_store, vector_store)
protagonist_profiler = ProtagonistProfiler(event_store)
rate_limiter = RateLimiter(max_requests=RATE_LIMIT_PER_MINUTE, window_seconds=60.0)

logger.info("Backend starting", extra={
    "endpoint": "startup",
    "session_id": "-",
    "tokens": {"deploy_mode": DEPLOY_MODE, "model": OLLAMA_MODEL},
})


async def _try_summarize(session_id: str, turn_count: int, arc_number: int) -> None:
    """Attempt periodic auto-summarization. Fire-and-forget."""
    try:
        prompt = memory_manager.build_summary_prompt(session_id)
        if not prompt:
            return
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                OLLAMA_CHAT_URL,
                json={
                    "model": _active_model,
                    "messages": [
                        {"role": "system", "content": "你是一个叙事记忆压缩器。请严格输出JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
        parsed = story_orchestrator._extract_json_block(content)
        if parsed:
            memory_manager.apply_summary_result(session_id, parsed, turn_count)
            summary_text = str(parsed.get('summary', ''))
            if summary_text:
                arc_manager.index_summary(session_id, summary_text, turn_count, arc_number)
    except Exception:
        pass  # summarization failure is non-critical


def _load_story_seeds(seed_file: Path) -> list[dict[str, str]]:
    if not seed_file.exists():
        return []
    try:
        data = json.loads(seed_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    seeds: list[dict[str, str]] = []
    for item in data.get("seeds", []):
        sid = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        world_seed = str(item.get("world_seed", "")).strip()
        if sid and title and world_seed:
            seeds.append(
                {
                    "id": sid,
                    "title": title,
                    "description": str(item.get("description", "")).strip(),
                    "world_seed": world_seed,
                }
            )
    return seeds


STORY_SEEDS = _load_story_seeds(STORAGE_DIR / "story_seeds.json")
STORY_SEED_MAP = {seed["id"]: seed for seed in STORY_SEEDS}

allowed_origins = ["*"] if ALLOW_ALL_ORIGINS else FRONTEND_ORIGINS

# Allow CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]
    stream: bool = False


class SeedGenerateRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)
    nsfw_level: str = "mild"  # mild / moderate / explicit
    count: int = 3
    user_hint: str = ""
    randomize_personality: bool = False
    relationship: str = ""  # 初始关系类型


class BlueprintRequest(BaseModel):
    seed: dict[str, Any]
    nsfw_level: str = "mild"
    initial_affection: float = 0.3
    relationship: str = ""


class StoryTurnRequest(BaseModel):
    session_id: str = "default"
    user_input: str
    selected_choice_id: Optional[str] = None
    seed_id: Optional[str] = None
    world_seed: Optional[str] = None
    active_characters: list[str] = Field(default_factory=list)
    history: list[Message] = Field(default_factory=list)


class StoryChoice(BaseModel):
    id: str
    title: str
    description: str = ""


class StoryTurnResponse(BaseModel):
    session_id: str
    narrative: str
    protagonist_action: str
    choices: list[StoryChoice]
    state_delta: dict[str, Any]
    current_state: dict[str, float]
    active_characters: list[str]
    model: str
    arc_info: Optional[dict[str, Any]] = None


def _message_to_dict(message: Message) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump()
    return message.dict()

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    if not any(msg.role == "system" for msg in request.messages):
        request.messages.insert(0, Message(role="system", content=SYSTEM_PROMPT))

    # Input length validation (5.3)
    last_user_msg = next((m for m in reversed(request.messages) if m.role == "user"), None)
    if last_user_msg and len(last_user_msg.content) > MAX_INPUT_LENGTH:
        raise HTTPException(status_code=400, detail=f"Message exceeds {MAX_INPUT_LENGTH} characters")

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [_message_to_dict(msg) for msg in request.messages],
        "stream": False,
    }

    if request.stream:
        raise HTTPException(status_code=400, detail="stream mode is not implemented yet")

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(OLLAMA_CHAT_URL, json=payload)
            response.raise_for_status()

        response_data = response.json()
        ai_reply_content = response_data.get("message", {}).get("content", "")
        user_msg_content = request.messages[-1].content if request.messages else ""

        event_store.log_turn(
            user_message=user_msg_content,
            ai_response=ai_reply_content,
            model_used=OLLAMA_MODEL,
            metadata={
                "emotion_index": "稍微烦躁",
                "source": "/api/chat",
            },
        )
        return response_data
    except httpx.ConnectError:
        perf.record_error("connect_error")
        raise HTTPException(status_code=503, detail="cannot connect to model service")
    except httpx.TimeoutException:
        perf.record_error("timeout")
        raise HTTPException(status_code=504, detail="model service timeout")
    except httpx.HTTPStatusError as exc:
        perf.record_error("http_error")
        raise HTTPException(status_code=502, detail=f"model service error: {exc.response.text}")
    except Exception as exc:
        perf.record_error("internal_error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/story/turn", response_model=StoryTurnResponse)
async def story_turn_endpoint(request: StoryTurnRequest):
    if not request.user_input.strip():
        raise HTTPException(status_code=400, detail="user_input is required")
    if len(request.user_input) > MAX_INPUT_LENGTH:
        raise HTTPException(status_code=400, detail=f"user_input exceeds {MAX_INPUT_LENGTH} characters")
    if not rate_limiter.is_allowed(request.session_id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded, please wait")

    history_payload = [_message_to_dict(msg) for msg in request.history]

    try:
        t_start = _time_mod.perf_counter()
        selected_seed = STORY_SEED_MAP.get(request.seed_id or "")
        resolved_world_seed = request.world_seed or (selected_seed["world_seed"] if selected_seed else None)

        # ── Parallel pre-generation reads ───────────────────────────
        with perf.measure("pre_gen"):
            state_fut = asyncio.to_thread(event_store.get_session_state, request.session_id)
            facts_fut = asyncio.to_thread(event_store.get_session_facts, request.session_id)
            memory_fut = asyncio.to_thread(memory_manager.assemble_memory_context, request.session_id)
            emotion_fut = asyncio.to_thread(emotion_engine.get_emotion, request.session_id, 'linxi')
            profile_fut = asyncio.to_thread(protagonist_profiler.get_profile, request.session_id)
            blueprint_fut = asyncio.to_thread(event_store.get_blueprint, request.session_id)

            current_state, existing_facts, memory_ctx, linxi_emotion, mc_profile, blueprint = await asyncio.gather(
                state_fut, facts_fut, memory_fut, emotion_fut, profile_fut, blueprint_fut,
            )

        protagonist_desc = protagonist_profiler.describe_protagonist(mc_profile)

        # Arc awareness
        arc = await asyncio.to_thread(
            arc_manager.get_or_create_arc, request.session_id,
            {"title": (selected_seed or {}).get("title", "第一章"), "world_seed": resolved_world_seed} if resolved_world_seed else None,
        )
        active_arc_number = arc.get("arc_number", 1) if arc else 1

        # Vector recall
        if vector_store.is_available():
            vector_recalls = await asyncio.to_thread(
                arc_manager.recall_relevant, request.session_id, request.user_input, top_k=3,
            )
            if vector_recalls:
                memory_ctx['vector_recall'] = vector_recalls

        emotion_desc = emotion_engine.get_emotion_description(linxi_emotion)
        memory_ctx['emotion_description'] = emotion_desc
        memory_block = context_budgeter.build_memory_block(memory_ctx)

        episodes = memory_ctx.get('episodes', [])
        constraints = consistency_guard.build_constraints(
            current_state, existing_facts, emotion=linxi_emotion, episodes=episodes,
        )
        active_character_profiles = prompt_generator.resolve_active_characters(request.active_characters)
        active_character_ids = [str(item.get("id", "")) for item in active_character_profiles if item.get("id")]
        dynamic_system_prompt = prompt_generator.build_system_prompt(
            current_state,
            active_character_ids,
            constraints,
            memory_block=memory_block,
            protagonist_desc=protagonist_desc,
            blueprint=blueprint,
        )

        with perf.measure("llm_generate"):
            turn = await story_orchestrator.generate_turn(
                session_id=request.session_id,
                user_input=request.user_input,
                prior_messages=history_payload,
                session_state=current_state,
                memory_facts=existing_facts,
                consistency_constraints=constraints,
                dynamic_system_prompt=dynamic_system_prompt,
                active_character_ids=active_character_ids,
                world_seed=resolved_world_seed,
                selected_choice_id=request.selected_choice_id,
            )

        generated_text = f"{turn['narrative']}\n{turn['protagonist_action']}"
        new_facts = consistency_guard.extract_facts(generated_text)
        conflicts = consistency_guard.detect_conflicts(
            existing_facts, new_facts, episodes=episodes, emotion=linxi_emotion,
        )

        if conflicts:
            retry_constraints = consistency_guard.build_constraints(
                current_state,
                existing_facts,
                extra_constraints=["一致性修正: " + msg for msg in conflicts],
                emotion=linxi_emotion,
                episodes=episodes,
            )
            with perf.measure("llm_retry"):
                turn = await story_orchestrator.generate_turn(
                    session_id=request.session_id,
                    user_input=request.user_input,
                    prior_messages=history_payload,
                    session_state=current_state,
                    memory_facts=existing_facts,
                    consistency_constraints=retry_constraints,
                    dynamic_system_prompt=prompt_generator.build_system_prompt(
                        current_state,
                        active_character_ids,
                        retry_constraints,
                        memory_block=memory_block,
                        protagonist_desc=protagonist_desc,
                        blueprint=blueprint,
                    ),
                    active_character_ids=active_character_ids,
                    world_seed=resolved_world_seed,
                    selected_choice_id=request.selected_choice_id,
                )
            generated_text = f"{turn['narrative']}\n{turn['protagonist_action']}"
            new_facts = consistency_guard.extract_facts(generated_text)

        # ── Post-generation: update all memory layers (parallel where safe) ─
        with perf.measure("post_gen"):
            merged_facts = consistency_guard.merge_facts(existing_facts, new_facts)

            extra_semantic = memory_manager.extract_semantic_facts(generated_text)
            if extra_semantic:
                merged_facts = consistency_guard.merge_facts(merged_facts, extra_semantic)

            # Parallel: facts write + state delta (independent targets)
            await asyncio.gather(
                asyncio.to_thread(event_store.replace_session_facts, request.session_id, merged_facts),
                asyncio.to_thread(event_store.apply_state_delta, request.session_id, turn.get("state_delta")),
            )
            updated_state = event_store.get_session_state(request.session_id)

            turn_count = len(event_store.get_session_history(request.session_id)) + 1

            # Parallel: episode extraction + emotion update + protagonist profile (independent)
            await asyncio.gather(
                asyncio.to_thread(
                    memory_manager.extract_episodes_from_narrative,
                    request.session_id, turn_count, generated_text,
                ),
                asyncio.to_thread(
                    emotion_engine.update_emotion,
                    request.session_id, turn_count, generated_text, request.user_input,
                ),
                asyncio.to_thread(
                    protagonist_profiler.update_from_choice,
                    request.session_id, request.selected_choice_id or "", request.user_input,
                ),
            )

            # Vector index (best-effort)
            await asyncio.to_thread(
                arc_manager.index_turn, request.session_id, turn_count,
                generated_text, active_arc_number,
            )

        suggest_completion = arc_manager.should_suggest_completion(request.session_id)
        turn["current_state"] = updated_state
        turn["active_characters"] = active_character_ids
        turn["arc_info"] = {
            "arc_number": active_arc_number,
            "arc_title": arc.get("title", "") if arc else "",
            "arc_status": "active",
            "suggest_completion": suggest_completion,
        }

        # Log turn in background (non-blocking)
        await asyncio.to_thread(
            event_store.log_story_turn,
            session_id=turn["session_id"],
            user_input=request.user_input,
            narrative=turn["narrative"],
            protagonist_action=turn["protagonist_action"],
            choices=turn["choices"],
            state_delta=turn["state_delta"],
            model_used=turn["model"],
            metadata={
                "source": "/api/story/turn",
                "selected_choice_id": request.selected_choice_id,
                "facts_count": len(merged_facts),
                "consistency_retry": len(conflicts) > 0,
            },
        )
        event_store.touch_session(request.session_id)

        perf.record("turn_total", (_time_mod.perf_counter() - t_start) * 1000)
        logger.info("story_turn completed", extra={
            "session_id": request.session_id,
            "endpoint": "/api/story/turn",
            "duration_ms": round((_time_mod.perf_counter() - t_start) * 1000, 1),
        })
        return StoryTurnResponse(**turn)
    except httpx.ConnectError:
        perf.record_error("connect_error")
        logger.error("Model service unreachable", extra={"endpoint": "/api/story/turn", "error_type": "connect_error"})
        raise HTTPException(status_code=503, detail="cannot connect to model service")
    except httpx.TimeoutException:
        perf.record_error("timeout")
        logger.error("Model service timeout", extra={"endpoint": "/api/story/turn", "error_type": "timeout"})
        raise HTTPException(status_code=504, detail="model service timeout")
    except httpx.HTTPStatusError as exc:
        perf.record_error("http_error")
        logger.error("Model service HTTP error", extra={"endpoint": "/api/story/turn", "error_type": "http_error"})
        raise HTTPException(status_code=502, detail=f"model service error: {exc.response.text}")
    except Exception as exc:
        perf.record_error("internal_error")
        logger.exception("Unexpected error in story_turn", extra={"endpoint": "/api/story/turn", "error_type": "internal"})
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/story/turn/stream")
async def story_turn_stream_endpoint(request: StoryTurnRequest):
    if not request.user_input.strip():
        raise HTTPException(status_code=400, detail="user_input is required")
    if len(request.user_input) > MAX_INPUT_LENGTH:
        raise HTTPException(status_code=400, detail=f"user_input exceeds {MAX_INPUT_LENGTH} characters")
    if not rate_limiter.is_allowed(request.session_id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded, please wait")

    history_payload = [_message_to_dict(msg) for msg in request.history]

    try:
        selected_seed = STORY_SEED_MAP.get(request.seed_id or "")
        resolved_world_seed = request.world_seed or (selected_seed["world_seed"] if selected_seed else None)

        # ── Parallel pre-generation reads ───────────────────────────
        with perf.measure("stream_pre_gen"):
            current_state, existing_facts, memory_ctx, linxi_emotion, mc_profile, blueprint = await asyncio.gather(
                asyncio.to_thread(event_store.get_session_state, request.session_id),
                asyncio.to_thread(event_store.get_session_facts, request.session_id),
                asyncio.to_thread(memory_manager.assemble_memory_context, request.session_id),
                asyncio.to_thread(emotion_engine.get_emotion, request.session_id, 'linxi'),
                asyncio.to_thread(protagonist_profiler.get_profile, request.session_id),
                asyncio.to_thread(event_store.get_blueprint, request.session_id),
            )

        protagonist_desc = protagonist_profiler.describe_protagonist(mc_profile)

        # Arc awareness
        arc = await asyncio.to_thread(
            arc_manager.get_or_create_arc, request.session_id,
            {"title": (selected_seed or {}).get("title", "第一章"), "world_seed": resolved_world_seed} if resolved_world_seed else None,
        )
        active_arc_number = arc.get("arc_number", 1) if arc else 1

        # Vector recall
        if vector_store.is_available():
            vector_recalls = await asyncio.to_thread(
                arc_manager.recall_relevant, request.session_id, request.user_input, top_k=3,
            )
            if vector_recalls:
                memory_ctx['vector_recall'] = vector_recalls

        emotion_desc = emotion_engine.get_emotion_description(linxi_emotion)
        memory_ctx['emotion_description'] = emotion_desc
        memory_block = context_budgeter.build_memory_block(memory_ctx)

        episodes = memory_ctx.get('episodes', [])
        constraints = consistency_guard.build_constraints(
            current_state, existing_facts, emotion=linxi_emotion, episodes=episodes,
        )
        active_character_profiles = prompt_generator.resolve_active_characters(request.active_characters)
        active_character_ids = [str(item.get("id", "")) for item in active_character_profiles if item.get("id")]
        dynamic_system_prompt = prompt_generator.build_system_prompt(
            current_state,
            active_character_ids,
            constraints,
            memory_block=memory_block,
            protagonist_desc=protagonist_desc,
            blueprint=blueprint,
        )

        async def event_generator():
            t_stream_start = _time_mod.perf_counter()
            turn_meta: dict[str, Any] = {}
            first_token_sent = False

            async for chunk in story_orchestrator.generate_turn_stream(
                session_id=request.session_id,
                user_input=request.user_input,
                prior_messages=history_payload,
                session_state=current_state,
                memory_facts=existing_facts,
                consistency_constraints=constraints,
                dynamic_system_prompt=dynamic_system_prompt,
                active_character_ids=active_character_ids,
                world_seed=resolved_world_seed,
                selected_choice_id=request.selected_choice_id,
            ):
                if chunk["type"] == "token":
                    if not first_token_sent:
                        perf.record("stream_first_token", (_time_mod.perf_counter() - t_stream_start) * 1000)
                        first_token_sent = True
                    yield f"event: narrative_chunk\ndata: {json.dumps({'chunk': chunk['content']}, ensure_ascii=False)}\n\n"
                elif chunk["type"] == "meta":
                    turn_meta = chunk

            if not turn_meta:
                turn_meta = story_orchestrator._fallback_output(request.user_input)
                turn_meta["session_id"] = request.session_id
                turn_meta["protagonist_action"] = ""
                turn_meta["model"] = story_orchestrator.model_name

            # Emit structured events immediately so frontend is unblocked
            generated_text = turn_meta.get("narrative", "")
            new_facts = consistency_guard.extract_facts(generated_text)
            merged_facts = consistency_guard.merge_facts(existing_facts, new_facts)

            extra_semantic = memory_manager.extract_semantic_facts(generated_text)
            if extra_semantic:
                merged_facts = consistency_guard.merge_facts(merged_facts, extra_semantic)

            # Parallel: facts write + state delta
            await asyncio.gather(
                asyncio.to_thread(event_store.replace_session_facts, request.session_id, merged_facts),
                asyncio.to_thread(event_store.apply_state_delta, request.session_id, turn_meta.get("state_delta")),
            )
            updated_state = event_store.get_session_state(request.session_id)

            yield f"event: protagonist_action\ndata: {json.dumps({'content': turn_meta.get('protagonist_action', '')}, ensure_ascii=False)}\n\n"
            yield f"event: choices\ndata: {json.dumps({'choices': turn_meta.get('choices', [])}, ensure_ascii=False)}\n\n"
            yield f"event: state_delta\ndata: {json.dumps({'state_delta': turn_meta.get('state_delta', {})}, ensure_ascii=False)}\n\n"
            yield f"event: current_state\ndata: {json.dumps({'current_state': updated_state}, ensure_ascii=False)}\n\n"
            yield f"event: active_characters\ndata: {json.dumps({'active_characters': active_character_ids}, ensure_ascii=False)}\n\n"

            # ── Background-friendly post-gen: episode + emotion in parallel ─
            turn_count = len(event_store.get_session_history(request.session_id)) + 1

            _, updated_emotion, _ = await asyncio.gather(
                asyncio.to_thread(
                    memory_manager.extract_episodes_from_narrative,
                    request.session_id, turn_count, generated_text,
                ),
                asyncio.to_thread(
                    emotion_engine.update_emotion,
                    request.session_id, turn_count, generated_text, request.user_input,
                ),
                asyncio.to_thread(
                    protagonist_profiler.update_from_choice,
                    request.session_id, request.selected_choice_id or "", request.user_input,
                ),
            )

            yield f"event: emotion\ndata: {json.dumps({'emotion': updated_emotion}, ensure_ascii=False)}\n\n"

            # Arc info
            suggest_completion = arc_manager.should_suggest_completion(request.session_id)
            arc_info_payload = {
                'arc_number': active_arc_number,
                'arc_title': arc.get('title', '') if arc else '',
                'arc_status': 'active',
                'suggest_completion': suggest_completion,
            }
            yield f"event: arc_info\ndata: {json.dumps({'arc_info': arc_info_payload}, ensure_ascii=False)}\n\n"

            yield "event: done\ndata: {}\n\n"

            # Fire-and-forget: all post-done work runs in a background task so
            # the generator (and HTTP body) closes immediately after 'done'.
            _t_total = t_stream_start
            _snap = {
                "session_id": request.session_id,
                "user_input": request.user_input,
                "narrative": turn_meta.get("narrative", ""),
                "protagonist_action": turn_meta.get("protagonist_action", ""),
                "choices": turn_meta.get("choices", []),
                "state_delta": turn_meta.get("state_delta", {}),
                "model_used": turn_meta.get("model", ""),
                "selected_choice_id": request.selected_choice_id,
                "facts_count": len(merged_facts),
                "turn_count": turn_count,
                "arc_number": active_arc_number,
                "generated_text": generated_text,
            }

            async def _post_done_work(snap: dict, t0: float) -> None:
                try:
                    await asyncio.to_thread(
                        event_store.log_story_turn,
                        session_id=snap["session_id"],
                        user_input=snap["user_input"],
                        narrative=snap["narrative"],
                        protagonist_action=snap["protagonist_action"],
                        choices=snap["choices"],
                        state_delta=snap["state_delta"],
                        model_used=snap["model_used"],
                        metadata={
                            "source": "/api/story/turn/stream",
                            "selected_choice_id": snap["selected_choice_id"],
                            "facts_count": snap["facts_count"],
                        },
                    )
                    event_store.touch_session(snap["session_id"])
                    await asyncio.to_thread(
                        arc_manager.index_turn, snap["session_id"],
                        snap["turn_count"], snap["generated_text"], snap["arc_number"],
                    )
                    if memory_manager.should_summarize(snap["session_id"]):
                        await _try_summarize(snap["session_id"], snap["turn_count"], snap["arc_number"])
                    perf.record("stream_turn_total", (_time_mod.perf_counter() - t0) * 1000)
                except Exception:
                    pass

            asyncio.create_task(_post_done_work(_snap, _t_total))

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    except httpx.ConnectError:
        perf.record_error("connect_error")
        logger.error("Model service unreachable", extra={"endpoint": "/api/story/turn/stream", "error_type": "connect_error"})
        raise HTTPException(status_code=503, detail="cannot connect to model service")
    except httpx.TimeoutException:
        perf.record_error("timeout")
        logger.error("Model service timeout", extra={"endpoint": "/api/story/turn/stream", "error_type": "timeout"})
        raise HTTPException(status_code=504, detail="model service timeout")
    except httpx.HTTPStatusError as exc:
        perf.record_error("http_error")
        logger.error("Model service HTTP error", extra={"endpoint": "/api/story/turn/stream", "error_type": "http_error"})
        raise HTTPException(status_code=502, detail=f"model service error: {exc.response.text}")
    except Exception as exc:
        perf.record_error("internal_error")
        logger.exception("Unexpected error in stream", extra={"endpoint": "/api/story/turn/stream", "error_type": "internal"})
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/health")
async def health_check():
    db_health = event_store.health()

    model_health: dict[str, Any]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{ollama_base_url()}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            model_names = [item.get("name", "") for item in data.get("models", [])]
        model_health = {
            "reachable": True,
            "model_configured": OLLAMA_MODEL,
            "model_present": OLLAMA_MODEL in model_names,
        }
    except Exception as exc:
        model_health = {
            "reachable": False,
            "model_configured": OLLAMA_MODEL,
            "error": str(exc),
        }

    overall_ok = db_health.get("ready") and model_health.get("reachable")
    return {
        "status": "ok" if overall_ok else "degraded",
        "version": APP_VERSION,
        "database": db_health,
        "model": model_health,
        "allow_all_origins": ALLOW_ALL_ORIGINS,
        "allowed_origins": allowed_origins,
    }


@app.get("/api/metrics")
async def get_metrics():
    """Performance metrics snapshot with error rates, token stats, and alerts."""
    perf_data = perf.snapshot()
    errors = perf.error_snapshot()
    tokens = perf.token_snapshot()

    # Compute alert flags
    alerts: list[str] = []
    for name, stats in perf_data.items():
        if stats.get("p95_ms", 0) > ALERT_P95_MS:
            alerts.append(f"{name} p95 ({stats['p95_ms']}ms) exceeds threshold ({ALERT_P95_MS}ms)")

    total_requests = sum(stats.get("count", 0) for stats in perf_data.values())
    total_errors = sum(errors.values())
    error_rate = total_errors / max(total_requests, 1)
    if error_rate > ALERT_ERROR_RATE:
        alerts.append(f"Error rate {error_rate:.2%} exceeds threshold {ALERT_ERROR_RATE:.0%}")

    return {
        "perf": perf_data,
        "cache": l1_cache.stats(),
        "errors": errors,
        "tokens": tokens,
        "alerts": alerts,
        "deploy_mode": DEPLOY_MODE,
    }


@app.post("/api/metrics/reset")
async def reset_metrics():
    """Reset performance counters."""
    perf.reset()
    l1_cache.clear()
    return {"ok": True}


MODEL_PRESETS: list[dict] = [
    {"id": "sorc/qwen3.5-instruct-heretic", "label": "Qwen 3.5 Heretic（默认）"},
    {"id": "qwen2.5:7b",                   "label": "Qwen 2.5 7B"},
    {"id": "qwen2.5:14b",                  "label": "Qwen 2.5 14B"},
    {"id": "llama3.2:3b",                  "label": "Llama 3.2 3B"},
    {"id": "llama3.1:8b",                  "label": "Llama 3.1 8B"},
    {"id": "mistral:7b",                   "label": "Mistral 7B"},
    {"id": "gemma3:4b",                    "label": "Gemma 3 4B"},
]


class ModelConfigRequest(BaseModel):
    model: str = Field(..., min_length=1, max_length=200)


@app.get("/api/config/model")
async def get_model_config():
    """Return active model and preset list."""
    return {"active_model": _active_model, "presets": MODEL_PRESETS}


@app.put("/api/config/model")
async def set_model_config(req: ModelConfigRequest):
    """Switch the active LLM model at runtime."""
    global _active_model
    model = req.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model must not be empty")
    _active_model = model
    story_orchestrator.model_name = model
    logger.info("Model switched", extra={"endpoint": "/api/config/model", "session_id": "-", "tokens": {"model": model}})
    return {"active_model": _active_model}


@app.get("/api/sessions")
async def list_sessions():
    return {"sessions": event_store.list_sessions()}


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    sessions = event_store.list_sessions()
    meta = next((s for s in sessions if s["session_id"] == session_id), None)
    if not meta:
        raise HTTPException(status_code=404, detail="session not found")
    history = event_store.get_session_history(session_id)
    state = event_store.get_session_state(session_id)
    emotion = event_store.get_emotion_state(session_id)
    episodes = event_store.get_episodes(session_id)
    arcs = event_store.get_arcs(session_id)
    return {"meta": meta, "history": history, "state": state, "emotion": emotion, "episodes": episodes, "arcs": arcs}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    event_store.delete_session(session_id)
    vector_store.delete_session(session_id)
    return {"ok": True}


class SaveSessionRequest(BaseModel):
    session_id: str
    title: str
    seed: Optional[dict[str, Any]] = None
    nsfw_level: str = "mild"
    initial_affection: Optional[float] = None


@app.post("/api/sessions")
async def save_session(request: SaveSessionRequest):
    event_store.save_session_meta(
        session_id=request.session_id,
        title=request.title,
        seed=request.seed,
        nsfw_level=request.nsfw_level,
    )
    # Set initial emotion state if specified
    if request.initial_affection is not None:
        clamped = max(0.0, min(1.0, request.initial_affection))
        event_store.save_emotion_state(
            session_id=request.session_id,
            character_id='linxi',
            turn_number=0,
            emotion={
                'affection': clamped,
                'tension': 0.3,
                'trust': 0.4,
                'comfort': 0.5,
            },
        )
    return {"ok": True}


# ── Arc endpoints ──────────────────────────────────────────────────────────────


@app.get("/api/sessions/{session_id}/arcs")
async def list_arcs(session_id: str):
    return {"arcs": event_store.get_arcs(session_id)}


class NewArcRequest(BaseModel):
    user_hint: str = ""
    nsfw_level: str = "mild"


@app.post("/api/sessions/{session_id}/arcs/complete")
async def complete_arc_endpoint(session_id: str):
    """Complete the current arc with an AI-generated summary."""
    prompt = arc_manager.build_arc_summary_prompt(session_id)
    if not prompt:
        raise HTTPException(status_code=400, detail="No active arc to complete")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                OLLAMA_CHAT_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": "你是一个叙事归纳助手。请严格输出JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="cannot connect to model service")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="model service timeout")

    parsed = story_orchestrator._extract_json_block(content)
    summary = str(parsed.get("summary", "篇章已完结。")) if parsed else "篇章已完结。"

    completed = arc_manager.complete_current_arc(session_id, summary)
    if not completed:
        raise HTTPException(status_code=400, detail="No active arc to complete")

    return {"arc": completed}


@app.post("/api/sessions/{session_id}/arcs/new")
async def create_new_arc_endpoint(session_id: str, request: NewArcRequest):
    """Generate a new arc seed and start it."""
    active = event_store.get_active_arc(session_id)
    if active:
        raise HTTPException(status_code=400, detail="Current arc is still active. Complete it first.")

    prompt = arc_manager.build_new_arc_prompt(session_id, request.user_hint, request.nsfw_level)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                OLLAMA_CHAT_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": "你是一位言情互动小说的世界观架构师。请严格输出JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="cannot connect to model service")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="model service timeout")

    parsed = story_orchestrator._extract_json_block(content)
    if not parsed:
        raise HTTPException(status_code=502, detail="AI 未能生成有效的新篇章设定，请重试")

    title = str(parsed.get("title", f"第{len(event_store.get_arcs(session_id)) + 1}篇"))
    world_seed = str(parsed.get("world_seed", ""))
    seed = {"title": title, "world_seed": world_seed}

    arc = arc_manager.start_new_arc(session_id, title, seed)
    return {"arc": arc, "world_seed": world_seed}


# ── Snapshot endpoints (5.1) ───────────────────────────────────────────────────


class SnapshotRequest(BaseModel):
    label: str = ""


@app.post("/api/sessions/{session_id}/snapshots")
async def create_snapshot(session_id: str, request: SnapshotRequest):
    """Create a snapshot of the current session state."""
    snap = await asyncio.to_thread(event_store.create_snapshot, session_id, request.label)
    return snap


@app.get("/api/sessions/{session_id}/snapshots")
async def list_snapshots(session_id: str):
    """List all snapshots for a session."""
    snapshots = await asyncio.to_thread(event_store.list_snapshots, session_id)
    return {"snapshots": snapshots}


@app.post("/api/sessions/{session_id}/snapshots/{snapshot_id}/restore")
async def restore_snapshot(session_id: str, snapshot_id: int):
    """Restore session to a previous snapshot (branch rollback)."""
    result = await asyncio.to_thread(event_store.restore_snapshot, session_id, snapshot_id)
    if not result:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return result


@app.get("/api/story/characters")
async def list_story_characters():
    return {"characters": prompt_generator.list_characters()}


@app.get("/api/story/seeds")
async def list_story_seeds():
    return {"seeds": STORY_SEEDS}


@app.put("/api/sessions/{session_id}/blueprint")
async def save_session_blueprint(session_id: str, body: dict[str, Any] = Body(...)):
    bp = body.get("blueprint")
    if not bp:
        raise HTTPException(status_code=400, detail="blueprint is required")
    await asyncio.to_thread(event_store.save_blueprint, session_id, bp)
    return {"ok": True}


_NSFW_LEVEL_DESC = {
    "mild": "轻度暧昧：允许含蓄的暗示、肢体接触描写（牵手、拥抱、亲吻），情感张力暗涌但不直接描写性行为。",
    "moderate": "中度情欲：允许较为直白的亲密场景，包括暗示性的身体描写与情欲对话，可以有激烈的肢体交互但不做极端细节。",
    "explicit": "高度露骨：不做任何删减，完整描写性行为细节、身体反应与情感交织，文本不设自我审查。",
}

_THEME_TAGS = [
    {"id": "campus", "label": "校园", "icon": "🏫"},
    {"id": "workplace", "label": "职场", "icon": "💼"},
    {"id": "historical", "label": "古风", "icon": "🏯"},
    {"id": "urban", "label": "都市", "icon": "🌃"},
    {"id": "fantasy", "label": "奇幻", "icon": "✨"},
    {"id": "scifi", "label": "科幻", "icon": "🚀"},
    {"id": "suspense", "label": "悬疑", "icon": "🔍"},
    {"id": "reunion", "label": "久别重逢", "icon": "🌧️"},
    {"id": "rivals", "label": "欢喜冤家", "icon": "⚡"},
    {"id": "forbidden", "label": "禁忌之恋", "icon": "🔒"},
    {"id": "secret", "label": "暗恋", "icon": "💌"},
    {"id": "contract", "label": "契约关系", "icon": "📜"},
    {"id": "cohabitation", "label": "同居", "icon": "🏠"},
    {"id": "heal", "label": "治愈", "icon": "🌿"},
    {"id": "revenge", "label": "复仇", "icon": "🗡️"},
    {"id": "showbiz", "label": "娱乐圈", "icon": "🎬"},
]

_RELATIONSHIP_TYPES = [
    {"id": "strangers",    "label": "陌生人",   "icon": "👤", "desc": "毫无交集的两个人，一次偶然相遇"},
    {"id": "classmates",   "label": "同学",     "icon": "📚", "desc": "同班或同校，认识但不熟"},
    {"id": "colleagues",   "label": "同事",     "icon": "💼", "desc": "同一公司，工作中接触"},
    {"id": "neighbors",    "label": "邻居",     "icon": "🏘️", "desc": "住在隔壁或同一小区"},
    {"id": "childhood",    "label": "青梅竹马",  "icon": "🌸", "desc": "从小一起长大，互相了解"},
    {"id": "exlovers",     "label": "前任",     "icon": "💔", "desc": "曾经在一起，分手后重逢"},
    {"id": "boss_sub",     "label": "上下级",   "icon": "👔", "desc": "职场中的上司与下属关系"},
    {"id": "rivals",       "label": "对手",     "icon": "⚔️", "desc": "某种竞争关系中的对手"},
    {"id": "benefactor",   "label": "恩人/被救", "icon": "🤝", "desc": "一方曾帮助过另一方"},
    {"id": "contract",     "label": "契约关系",  "icon": "📜", "desc": "因某种约定或交易绑定"},
    {"id": "online_meet",  "label": "网友奔现",  "icon": "📱", "desc": "网络上认识后第一次线下见面"},
    {"id": "master_student","label": "师徒",    "icon": "🎓", "desc": "教学或指导关系"},
]


@app.get("/api/story/tags")
async def list_theme_tags():
    return {"tags": _THEME_TAGS, "relationships": _RELATIONSHIP_TYPES}


@app.post("/api/story/seeds/generate")
async def generate_story_seed(request: SeedGenerateRequest):
    if request.user_hint and len(request.user_hint) > MAX_INPUT_LENGTH:
        raise HTTPException(status_code=400, detail=f"user_hint exceeds {MAX_INPUT_LENGTH} characters")

    level = request.nsfw_level if request.nsfw_level in _NSFW_LEVEL_DESC else "mild"
    nsfw_desc = _NSFW_LEVEL_DESC[level]
    count = max(1, min(request.count, 5))

    tag_labels = []
    tag_id_set = {t["id"] for t in _THEME_TAGS}
    for t in request.tags[:6]:
        if t in tag_id_set:
            label = next((x["label"] for x in _THEME_TAGS if x["id"] == t), t)
            tag_labels.append(label)
    tags_line = f"用户选择的题材标签：{', '.join(tag_labels)}" if tag_labels else "用户未选择特定标签，请自由发挥题材。"
    hint_line = f"【用户额外要求】{request.user_hint.strip()}\n" if request.user_hint.strip() else ""
    personality_line = (
        "【性格随机化】请为林夕随机生成一个独特性格（不要总是外冷内热），可以是：活泼开朗、病娇偏执、天然呆、学霸高冷、"
        "温柔治愈、傲娇大小姐、社恐内向、腹黑毒舌等。在 personality 和 heroine_bio 中体现。\n"
    ) if request.randomize_personality else ""

    # Resolve relationship type
    rel_label = ""
    if request.relationship:
        rel_item = next((r for r in _RELATIONSHIP_TYPES if r["id"] == request.relationship), None)
        if rel_item:
            rel_label = f"【初始关系】{rel_item['label']}：{rel_item['desc']}\n"

    system_prompt = (
        "你是一位言情互动小说的世界观架构师与剧本策划。\n"
        f"请根据以下维度约束，输出 {count} 个风格各异的剧本种子。\n"
        "输出严格 JSON 数组，不要输出 JSON 以外的任何文字。\n\n"
        "=== 约束维度 ===\n"
        f"【NSFW档位】{level} — {nsfw_desc}\n"
        f"【题材】{tags_line}\n"
        f"{rel_label}"
        f"{personality_line}"
        f"{hint_line}"
        '【核心类型】言情 / 情感驱动 / Galgame风格。玩家是男主角（"你"，第二人称），林夕是女主角。\n\n'
        "=== 每个种子必须包含以下字段 ===\n"
        "1. title: 剧本名称（8字以内，有吸引力）\n"
        "2. description: 50字以内的剧情提要\n"
        '3. genre: 题材风格标签（如"校园暗恋""职场禁忌""都市悬疑"等）\n'
        "4. personality: 林夕在此剧本中的性格标签（15字以内）\n"
        '5. relationship: 男女主的初始关系（如"同班同学""前任重逢""甲方乙方"等，20字以内）\n'
        "6. protagonist_type: 男主角的初始设定（25字以内，例如：沉默寡言的转校生 / 新来的项目经理）\n"
        "7. heroine_bio: 林夕的具体人设（50字，包括身份、性格特点、说话风格、核心矛盾）\n"
        "8. world_seed: 150-250字的完整世界观描述，必须包含：\n"
        "   - 具体的时间地点和场景氛围\n"
        "   - 两人的初始关系状态和情感距离\n"
        "   - 隐藏的矛盾或悬念（作为情感张力的引擎）\n"
        "   - 第一幕的开场情境（让故事能直接启动）\n"
        "9. key_routes: 数组，4条可能的剧情路线，每条包含：\n"
        '   {"route": "路线名", "tone": "基调", "preview": "30字路线预览"}\n'
        '   路线示例：纯爱线/支配线/决裂线/救赎线/疯批线/暗黑线/治愈线\n\n'
        f"请输出恰好 {count} 个种子，每个的世界观、人设、初始关系尽量不同。"
    )

    payload = {
        "model": _active_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请生成 {count} 个全新的言情剧本种子。 /no_think"},
        ],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(OLLAMA_CHAT_URL, json=payload)
            response.raise_for_status()

        content = response.json().get("message", {}).get("content", "")

        import re as _re_mod
        import time as _time_mod
        seeds: list[dict] = []

        stripped = _re_mod.sub(r"<think>[\s\S]*?</think>", "", content).strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                stripped = "\n".join(lines[1:-1]).strip()

        parsed = None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("[")
            end = stripped.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(stripped[start : end + 1])
                except json.JSONDecodeError:
                    pass

        if parsed is None:
            for m in _re_mod.finditer(r'\{[^{}]*"world_seed"[^{}]*\}', stripped):
                try:
                    obj = json.loads(m.group())
                    if obj.get("world_seed"):
                        if parsed is None:
                            parsed = []
                        parsed.append(obj)
                except json.JSONDecodeError:
                    continue

        if parsed is None:
            single = story_orchestrator._extract_json_block(content)
            if single and single.get("world_seed"):
                parsed = [single]

        if not parsed:
            raise HTTPException(status_code=502, detail="AI 未能生成有效的剧本种子，请重试")

        if isinstance(parsed, dict):
            parsed = [parsed]

        for idx, item in enumerate(parsed[:count]):
            if not isinstance(item, dict) or not item.get("world_seed"):
                continue
            seeds.append({
                "id": f"ai_{int(_time_mod.time() * 1000)}_{idx}",
                "title": str(item.get("title", "AI生成剧本")).strip(),
                "description": str(item.get("description", "")).strip(),
                "genre": str(item.get("genre", "")).strip(),
                "personality": str(item.get("personality", "外冷内热")).strip(),
                "relationship": str(item.get("relationship", "")).strip(),
                "protagonist_type": str(item.get("protagonist_type", "")).strip(),
                "heroine_bio": str(item.get("heroine_bio", "")).strip(),
                "world_seed": str(item.get("world_seed", "")).strip(),
                "key_routes": item.get("key_routes", []),
                "nsfw_level": level,
                "ai_generated": True,
            })

        if not seeds:
            raise HTTPException(status_code=502, detail="AI 未能生成有效的剧本种子，请重试")

        return {"seeds": seeds}
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="cannot connect to model service")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="model service timeout")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"model service error: {exc.response.text}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/story/blueprint")
async def generate_blueprint(request: BlueprintRequest):
    """Phase 2: 玩家选择种子后，生成完整的剧本蓝图（角色详情+故事节点+路线分支）。"""
    seed = request.seed
    if not seed.get("world_seed"):
        raise HTTPException(status_code=400, detail="seed must contain world_seed")

    level = request.nsfw_level if request.nsfw_level in _NSFW_LEVEL_DESC else "mild"
    nsfw_desc = _NSFW_LEVEL_DESC[level]

    system_prompt = (
        "你是一位专业的互动小说剧本策划。\n"
        "根据下面提供的剧本种子，生成完整的剧本蓝图。\n"
        "输出严格 JSON，不要输出 JSON 以外的任何文字。\n\n"
        f"【NSFW档位】{level} — {nsfw_desc}\n\n"
        "=== 剧本种子 ===\n"
        f"标题：{seed.get('title', '')}\n"
        f"世界观：{seed.get('world_seed', '')}\n"
        f"林夕人设：{seed.get('heroine_bio', seed.get('personality', ''))}\n"
        f"男主类型：{seed.get('protagonist_type', '玩家自定义')}\n"
        f"初始关系：{seed.get('relationship', '未指定')}\n"
        f"初始好感度：{request.initial_affection:.0%}\n\n"
        "=== 输出结构 ===\n"
        "生成一个 JSON 对象包含以下字段：\n\n"
        "1. heroine_detail: 林夕的完整人设（200字），包含：\n"
        "   - 外在形象、穿着风格\n"
        "   - 说话风格与口头禅（举例3句典型台词）\n"
        "   - 内心世界（核心恐惧/渴望/矛盾）\n"
        "   - 对男主的初始态度\n\n"
        "2. protagonist_hooks: 数组，3个男主可能的性格方向：\n"
        '   [{"type": "温柔守护型", "effect_on_heroine": "会逐渐卸下防备"}, ...]\n\n'
        "3. story_nodes: 数组，6-8个关键剧情节点（按时间线排列），每个：\n"
        "   {\n"
        '     "node_id": 1~8的编号,\n'
        '     "title": "节点标题",\n'
        '     "description": "50字节点描述——这里会发生什么",\n'
        '     "trigger_condition": "什么情况下会触发此节点",\n'
        '     "emotional_stakes": "此处的情感风险/赌注是什么",\n'
        '     "branch_choices": [\n'
        '       {"direction": "纯爱", "action": "玩家应该怎么做", "consequence": "导致什么"},\n'
        '       {"direction": "支配", "action": "...", "consequence": "..."},\n'
        '       {"direction": "决裂", "action": "...", "consequence": "..."}\n'
        "     ]\n"
        "   }\n\n"
        "4. route_map: 4条完整路线的描述，每条：\n"
        "   {\n"
        '     "route_name": "纯爱线",\n'
        '     "tone": "温暖治愈",\n'
        '     "key_moments": "路线的3个关键转折点概述",\n'
        '     "ending_preview": "这条线的结局走向（30字）"\n'
        "   }\n\n"
        "5. opening_scene: 开场第一幕的详细设定（100字），描述第一个场景的具体画面，\n"
        "   包括时间、地点、天气、男主正在做什么、林夕是怎么出场的。\n\n"
        "要求：节点之间要有因果逻辑；不同路线的分歧应该由玩家的关键选择决定；\n"
        "每个节点的 branch_choices 要体现不同性格方向的选择差异。"
    )

    payload = {
        "model": _active_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请根据种子信息生成完整的剧本蓝图。 /no_think"},
        ],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(OLLAMA_CHAT_URL, json=payload)
            response.raise_for_status()

        content = response.json().get("message", {}).get("content", "")
        parsed = story_orchestrator._extract_json_block(content)
        if not parsed:
            raise HTTPException(status_code=502, detail="AI 未能生成有效的剧本蓝图，请重试")

        # Persist blueprint if session_id is available in seed
        blueprint = {
            "seed": seed,
            "heroine_detail": parsed.get("heroine_detail", ""),
            "protagonist_hooks": parsed.get("protagonist_hooks", []),
            "story_nodes": parsed.get("story_nodes", []),
            "route_map": parsed.get("route_map", []),
            "opening_scene": parsed.get("opening_scene", ""),
        }

        return {"blueprint": blueprint}
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="cannot connect to model service")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="model service timeout")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"model service error: {exc.response.text}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Serve frontend static build (must come AFTER all /api/* routes) ──
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=BACKEND_HOST, port=BACKEND_PORT, reload=BACKEND_RELOAD)
