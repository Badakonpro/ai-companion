from __future__ import annotations

from typing import Any, Optional
import asyncio
import json
import time as _time_mod
from functools import partial
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
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
    from persona import SYSTEM_PROMPT  # type: ignore
    from config import STORAGE_DIR  # type: ignore
    from logger import logger  # type: ignore

app = FastAPI(title="AI Persona MVP")
event_store = EventStore(SQLITE_DB_PATH)
story_orchestrator = StoryOrchestrator(OLLAMA_CHAT_URL, OLLAMA_MODEL, REQUEST_TIMEOUT_SECONDS)
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
                    "model": OLLAMA_MODEL,
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

            current_state, existing_facts, memory_ctx, linxi_emotion = await asyncio.gather(
                state_fut, facts_fut, memory_fut, emotion_fut,
            )

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

            # Parallel: episode extraction + emotion update (independent)
            await asyncio.gather(
                asyncio.to_thread(
                    memory_manager.extract_episodes_from_narrative,
                    request.session_id, turn_count, generated_text,
                ),
                asyncio.to_thread(
                    emotion_engine.update_emotion,
                    request.session_id, turn_count, generated_text, request.user_input,
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
            current_state, existing_facts, memory_ctx, linxi_emotion = await asyncio.gather(
                asyncio.to_thread(event_store.get_session_state, request.session_id),
                asyncio.to_thread(event_store.get_session_facts, request.session_id),
                asyncio.to_thread(memory_manager.assemble_memory_context, request.session_id),
                asyncio.to_thread(emotion_engine.get_emotion, request.session_id, 'linxi'),
            )

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

            _, updated_emotion = await asyncio.gather(
                asyncio.to_thread(
                    memory_manager.extract_episodes_from_narrative,
                    request.session_id, turn_count, generated_text,
                ),
                asyncio.to_thread(
                    emotion_engine.update_emotion,
                    request.session_id, turn_count, generated_text, request.user_input,
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

            # Fire-and-forget: log turn + touch session
            await asyncio.to_thread(
                event_store.log_story_turn,
                session_id=request.session_id,
                user_input=request.user_input,
                narrative=turn_meta.get("narrative", ""),
                protagonist_action=turn_meta.get("protagonist_action", ""),
                choices=turn_meta.get("choices", []),
                state_delta=turn_meta.get("state_delta", {}),
                model_used=turn_meta.get("model", ""),
                metadata={
                    "source": "/api/story/turn/stream",
                    "selected_choice_id": request.selected_choice_id,
                    "facts_count": len(merged_facts),
                },
            )
            event_store.touch_session(request.session_id)

            # Vector indexing + periodic summarization (best-effort)
            await asyncio.to_thread(
                arc_manager.index_turn, request.session_id, turn_count,
                generated_text, active_arc_number,
            )
            if memory_manager.should_summarize(request.session_id):
                await _try_summarize(request.session_id, turn_count, active_arc_number)

            perf.record("stream_turn_total", (_time_mod.perf_counter() - t_stream_start) * 1000)

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


@app.get("/api/story/tags")
async def list_theme_tags():
    return {"tags": _THEME_TAGS}


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
        "温柔治愈、傲娇大小姐、社恐内向、腹黑毒舌等。在 world_seed 中明确描述她的性格和说话风格。\n"
    ) if request.randomize_personality else ""

    system_prompt = (
        "你是一位言情互动小说的世界观架构师。"
        f"请根据以下要求，输出 {count} 个风格各异的剧本种子，格式为严格 JSON 数组，不要输出任何额外文字。\n\n"
        f"【NSFW档位】{level} — {nsfw_desc}\n"
        '【核心类型】言情 / 情感驱动 / Galgame风格。玩家是男主角（第二人称"你"），"林夕"是女主角。\n'
        f"{personality_line}"
        f"【题材要求】{tags_line}\n"
        f"{hint_line}"
        "世界观应融入情感暗线、人物张力和暧昧或情欲元素（根据档位决定尺度）。\n"
        f"每个种子的风格、场景、时代背景应尽量不同，给用户多样化选择。\n\n"
        "JSON 输出格式（数组，每个元素）:\n"
        '[{"title": string, "description": string(50字以内剧情提要), '
        '"world_seed": string(100-200字世界观描述，包含场景、人物关系初始状态和情感张力起点), '
        '"personality": string(15字以内的林夕性格标签，例如"外冷内热""活泼开朗")}]\n'
        f"请输出恰好 {count} 个种子。确保 world_seed 足够具体，能够直接用于开局叙事。"
    )

    payload = {
        "model": OLLAMA_MODEL,
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

        # Try to extract JSON array
        import re as _re_mod
        import time as _time_mod
        seeds: list[dict] = []

        # Strip <think>...</think> blocks (common in reasoning models)
        stripped = _re_mod.sub(r"<think>[\s\S]*?</think>", "", content).strip()

        # Strip code fences
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                stripped = "\n".join(lines[1:-1]).strip()

        parsed = None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            # Find JSON array
            start = stripped.find("[")
            end = stripped.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(stripped[start : end + 1])
                except json.JSONDecodeError:
                    pass

        if parsed is None:
            # Fallback: find any JSON object with world_seed
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
            # Last resort: try single object via orchestrator helper
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
                "world_seed": str(item.get("world_seed", "")).strip(),
                "personality": str(item.get("personality", "外冷内热")).strip(),
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


# ── Serve frontend static build (must come AFTER all /api/* routes) ──
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=BACKEND_HOST, port=BACKEND_PORT, reload=BACKEND_RELOAD)
