from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..config import settings
from ..security import check_rate, client_ip
from ..services import ai_core, memory_engine, behavior_analyzer, personality_adapter, planner, knowledge_graph, web_images, study
from ..services import agent as agent_exec
from ..services import byok as byok_svc
from ..services.tools import due_reminders
import asyncio
import json
import logging
import re
import time

from fastapi.responses import StreamingResponse

log = logging.getLogger("ashtra.api")

router = APIRouter(prefix="/chat", tags=["chat"])

# Lightweight per-request stage timing (production-safe: one log line per chat).
_PERF = settings.chat_perf_log


class _Stage:
    """context manager: accumulate named stage durations for one request."""
    def __init__(self) -> None:
        self.marks: dict[str, float] = {}
        self._t0 = time.perf_counter()
        self._last = self._t0

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.marks[name] = self.marks.get(name, 0.0) + (now - self._last)
        self._last = now

    def total(self) -> float:
        return time.perf_counter() - self._t0

    def report(self) -> str:
        return " ".join(f"{k}={v * 1000:.0f}ms" for k, v in self.marks.items())

_TITLE_SYS = ("Generate a short title (max 6 words) for this conversation. "
              "Reply with the title only — no quotes, no punctuation at the end.")


async def _auto_title(message: str) -> str:
    """Useful sidebar title from the first message (falls back to a trim)."""
    fallback = re.sub(r"\s+", " ", message.strip())[:40].strip()
    try:
        import httpx
        from ..services.ai_core import _http
        r = await _http().post(
            f"{settings.groq_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={"model": settings.groq_model,
                  "messages": [{"role": "system", "content": _TITLE_SYS},
                               {"role": "user", "content": message[:500]}],
                  "max_tokens": 24, "temperature": 0.2,
                  "reasoning_effort": "low"},
            timeout=15,
        )
        r.raise_for_status()
        t = (r.json()["choices"][0]["message"]["content"] or "").strip().strip('"')
        return (t[:60] or fallback) if t else fallback
    except Exception:
        return fallback

def _keyword_profile_hints(text: str) -> dict:
    """Phase-2 interests/goals/skills signals (kept, now alongside behaviour analysis)."""
    t = text.lower()
    hints: dict = {}
    for kw, label in (("python", "Python"), ("ai", "AI"), ("coding", "coding"),
                      ("design", "design"), ("music", "music"), ("cricket", "cricket"),
                      ("fastapi", "FastAPI"), ("react", "React")):
        if kw in t and ("like" in t or "love" in t or "interest" in t or "learn" in t):
            hints.setdefault("interests", []).append(label)
    if "my goal" in t or "i want to" in t:
        hints.setdefault("goals", []).append(text.strip()[:120])
    if "i know" in t or "i use" in t or "my skill" in t:
        hints.setdefault("skills", []).append(text.strip()[:80])
    return hints

def _apply_profile_hints(profile, hints: dict):
    for field in ("interests", "goals", "skills"):
        for v in hints.get(field, []):
            cur = list(getattr(profile, field) or [])
            if v not in cur:
                cur.append(v)
            setattr(profile, field, cur)

@router.post("", response_model=schemas.ChatResponse)
async def chat(req: schemas.ChatRequest, request: Request, db: Session = Depends(get_db)):
    t = _Stage()
    log.info("[ASHRA API] request received user=%s len=%d", req.user_id, len(req.message or ""))
    # Per-user LLM spend cap (stricter than the global per-IP limit).
    check_rate(f"chat:{client_ip(request)}:{req.user_id}", settings.chat_rate_limit_per_minute)
    user = db.query(models.User).filter_by(id=req.user_id).first()
    if not user:
        user = models.User(id=req.user_id, email=f"{req.user_id}@local")
        db.add(user)
        db.commit()
    profile = db.query(models.UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = models.UserProfile(user_id=user.id)
        db.add(profile)
        db.commit()
    t.mark("db_user")
    own_creds = byok_svc.creds_for(db, user.id) or None

    conv = None
    new_conv = False
    if req.conversation_id:
        conv = db.query(models.Conversation).filter_by(id=req.conversation_id, user_id=user.id).first()
    if not conv:
        conv = models.Conversation(user_id=user.id, title=req.message[:40])
        db.add(conv)
        db.commit()
        new_conv = True

    db.add(models.Message(conversation_id=conv.id, role="user", content=req.message))
    db.commit()
    t.mark("db_conv")

    # --- Slash commands: /study <topic>, /flashcards, /quiz, /visuals, /websearch ---
    STUDY_CMDS = ("study", "flashcards", "quiz", "visuals")
    SLASH_HELP = ("Available: /study <topic>, /flashcards [topic], "
                  "/quiz [topic], /visuals [topic], /websearch <query>.")
    slash_cmd, slash_arg = "", ""
    _sm = re.match(r"^/(\w+)\s*(.*)$", (req.message or "").strip(), re.S)
    if _sm:
        slash_cmd, slash_arg = _sm.group(1).lower(), _sm.group(2).strip()
    study_mode = slash_cmd == "study" and bool(slash_arg)
    study_family = slash_cmd in STUDY_CMDS
    is_search = slash_cmd == "websearch"
    slash_err = ""
    if slash_cmd == "study" and not slash_arg:
        slash_err = ("Give me a topic: /study photosynthesis — explainer first, "
                     "then flashcards, quiz, visuals on tap.")
    elif slash_cmd == "websearch" and not slash_arg:
        slash_err = ("Give me something to search: /websearch fusion power "
                     "breakthroughs.")
    elif slash_cmd and slash_cmd not in STUDY_CMDS and not is_search:
        slash_err = f"Unknown command /{slash_cmd}. " + SLASH_HELP
    # Follow-up turns borrow the topic from conversation history.
    slash_topic = slash_arg
    if study_family and not slash_topic:
        slash_topic = study.resolve_topic(db, conv.id, "")
    if study_family and slash_cmd != "study" and not slash_topic:
        slash_err = ("No topic yet — ask /study <topic> first, or add one: "
                     "/quiz photosynthesis.")
    if slash_err:
        if req.stream:
            async def _err():
                yield "data: " + json.dumps(
                    {"type": "error", "detail": slash_err}, ensure_ascii=False) + "\n\n"
            return StreamingResponse(
                _err(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                         "X-Accel-Buffering": "no"})
        raise HTTPException(status_code=400, detail=slash_err)
    # Pipeline (planner, memory, generation) works on the resolved topic.
    gen_text = slash_topic if (study_family or is_search) else req.message

    # --- Phase 2: conversation retrieval + semantic memory retrieval ---
    history_rows = (
        db.query(models.Message).filter_by(conversation_id=conv.id)
        .order_by(models.Message.created_at.desc()).limit(10).all()
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(history_rows[:-1])]

    relevant = memory_engine.retrieve_relevant(user.id, gen_text, top_k=3)
    memory_context = "\n".join(f"- [{h['kind']}] {h['content']}" for h in relevant if h.get("content"))
    t.mark("db_history_mem")

    # --- Phase 4: planner → tools (non-critical: never block the reply) ---
    tool_calls, tool_context = [], ""
    try:
        tool_steps = planner.plan(gen_text)
        if tool_steps:
            tool_calls, tool_context = agent_exec.execute_plan(db, user.id, tool_steps)
    except Exception as e:
        log.warning("[ASHRA API] planner/tools failed: %s", type(e).__name__)

    # --- Phase 4 (automation lite): surface pending reminders ---
    reminder_context = ""
    try:
        pending = due_reminders(db, user.id)
        if pending and len(req.message.split()) < 20:
            reminder_context = "Pending reminders: " + "; ".join(pending)
    except Exception as e:
        log.warning("[ASHRA API] reminders failed: %s", type(e).__name__)

    # --- Phase 6: observe entities into knowledge graph + surface active goals ---
    graph_ents = None
    try:
        graph_ents = knowledge_graph.observe(user.id, gen_text)
    except Exception as e:
        log.warning("[ASHRA API] knowledge graph failed: %s", type(e).__name__)
    goals_active: list = []
    try:
        goals_active = db.query(models.Goal).filter_by(user_id=user.id, status="active").all()
    except Exception as e:
        log.warning("[ASHRA API] goals query failed: %s", type(e).__name__)
    goal_context = ""
    if goals_active and len(req.message.split()) < 25:
        goal_context = "Active goals: " + "; ".join(
            f"{g.title} ({g.progress or 0}%)" for g in goals_active[:5])

    # --- Phase 3: behaviour analysis → gradual adaptation (non-critical) ---
    signals: dict = {}
    adaptations_made: list = []
    try:
        signals = behavior_analyzer.analyze(gen_text)
        adaptations_made = behavior_analyzer.apply_to_profile(profile, signals)
        db.commit()
    except Exception as e:
        log.warning("[ASHRA API] behaviour analysis failed: %s", type(e).__name__)
        try:
            db.rollback()
        except Exception:
            pass

    adaptation_text = personality_adapter.directives(profile)
    ctx = ai_core.build_profile_context(profile)
    if tool_context:
        ctx += f"\nTool results:\n{tool_context}"
    if req.image_context:
        # Vision pipeline: the vision model saw the image; the text brain reasons over this.
        ctx += f"\nImage context (described by vision model, the user attached this image):\n{req.image_context}"
    if reminder_context:
        ctx += f"\n{reminder_context}"
    if goal_context:
        ctx += f"\n{goal_context}"
    if slash_cmd == "study":
        ctx += "\n" + study.STUDY_SYS
    elif slash_cmd == "flashcards":
        ctx += "\n" + study.FC_SYS
    elif slash_cmd == "quiz":
        ctx += "\n" + study.QUIZ_SYS
    elif slash_cmd == "visuals":
        ctx += "\n" + study.VISUALS_SYS
    # /websearch retrieves BEFORE generation — the model needs results in ctx.
    from ..services import answer_engine
    web_ctx, web_sources = "", []
    if is_search:
        try:
            web_ctx, web_sources = await answer_engine.web_search_answer(gen_text)
        except Exception as e:
            log.warning("[ASHRA API] websearch retrieve failed: %s", type(e).__name__)
        if web_sources:
            ctx += "\n" + answer_engine.SEARCH_SYS + "\n" + web_ctx
    # Generation budget per command (study/websearch: roomy but low-reasoning).
    if is_search:
        cmd_budget, cmd_reason = 600, "low"
    elif study_family:
        cmd_budget, cmd_reason = 800, "low"
    else:
        cmd_budget, cmd_reason = None, None
    t.mark("prep")  # planner + reminders + graph + goals + behaviour + prompt build

    # --- MCP: live tool servers feed the model's tool loop (non-critical) ---
    mcp_schemas: list = []
    mcp_exec = None
    try:
        from ..services import mcp_client
        mcp_schemas, mcp_exec = await mcp_client.tools_for_chat(db, user.id)
    except Exception as e:
        log.warning("[ASHRA API] mcp tools unavailable: %s", type(e).__name__)
        mcp_schemas, mcp_exec = [], None

    # Auto-title only needs the user message — run it ALONGSIDE generation
    # instead of adding a second full LLM round-trip to the response path.
    title_task = asyncio.create_task(_auto_title(req.message)) if new_conv else None
    # Visual gate runs ALONGSIDE generation so it's usually decided by done-time.
    visual_task = asyncio.create_task(study.visual_needed(req.message)) if study_mode else None
    # Web images for the stream path also start ALONGSIDE generation; results
    # are harvested with zero awaits (task.done() is sync) so nothing can
    # strand the client between the last token and done.
    _img_task = None
    if not study_family:
        try:
            _img_q0 = web_images.wants_images(gen_text)
        except Exception:
            _img_q0 = ""
        if _img_q0:
            _img_task = asyncio.create_task(asyncio.to_thread(web_images.search, _img_q0))

    async def _finalize(reply: str) -> dict:
        """Post-generation work shared by the JSON and streaming paths."""
        if not (reply or "").strip():
            log.warning("[ASHRA API] empty reply, using fallback")
            reply = "Sorry, I couldn't generate a response. Please try again."
        if tool_calls:
            reply = reply.rstrip() + " 🔧[" + ", ".join(c["tool"] for c in tool_calls) + "]"
        if web_sources:
            reply = reply.rstrip() + "\n\nSources:\n" + "\n".join(
                f"{s['n']}. [{s['title']}]({s['url']})" for s in web_sources[:5])
        # --- Study mode: flashcards → notes (deduped), staged visuals, actions ---
        study_blocks: dict = {}
        study_images: list = []
        visual_available = False
        if slash_cmd == "study" and visual_task is not None:
            # Staged reveal: record the verdict only — images fetch on /visuals tap.
            try:
                visual_available = bool(await visual_task)
            except Exception:
                visual_available = False
        if slash_cmd == "visuals":
            visual_available = True
            cards: dict = {}
            try:
                reply, cards = study.extract_cards(reply)
            except Exception as e:
                log.warning("[ASHRA API] study cards parse failed: %s", type(e).__name__)
            if not cards:
                # Model answered in prose — one stern retry for the visual.
                try:
                    retry = await ai_core.generate_reply(
                        f"{study.SVG_RETRY_SYS}\n\nTopic: {slash_topic or slash_arg}",
                        ctx, history, memory_context, adaptation_text,
                        reasoning="low", max_tokens=800, own_creds=own_creds)
                    _, cards = study.extract_cards(retry)
                    if cards:
                        log.info("[ASHRA API] study cards recovered on retry (%d items)",
                                 len(cards["items"]))
                except Exception as e:
                    log.warning("[ASHRA API] study cards retry failed: %s", type(e).__name__)
            if not cards:
                # Last resort: accept a free-drawn SVG if the model made one.
                try:
                    _, svg = study.extract_svg(reply)
                except Exception:
                    svg = ""
                if svg:
                    study_blocks["visual_svg"] = svg
            if not cards and not study_blocks.get("visual_svg"):
                # Deterministic fallback: build cards from the prose itself.
                try:
                    cards = study.cards_from_prose(reply, slash_topic or slash_arg)
                    if cards:
                        log.info("[ASHRA API] study cards synthesized from prose (%d items)",
                                 len(cards["items"]))
                except Exception as e:
                    log.warning("[ASHRA API] study cards synthesize failed: %s",
                                type(e).__name__)
                    cards = {}
            if cards:
                study_blocks["visual_cards"] = cards
        if study_family:
            expect = "quiz" if slash_cmd == "quiz" else "flashcards"
            study_blocks["expect"] = expect
            try:
                _, fcs, _ = study.split_study(reply, expect=expect)
                added = 0
                for fc in fcs:
                    content = f"Q: {fc['q']}\nA: {fc['a']}"
                    exists = db.query(models.Note).filter_by(
                        user_id=user.id, content=content).first()
                    if exists:
                        continue
                    db.add(models.Note(user_id=user.id, title=f"Study: {fc['q']}"[:120],
                                       content=content))
                    added += 1
                if added:
                    db.commit()
                if fcs:
                    study_blocks["flashcards"] = fcs
            except Exception as e:
                log.warning("[ASHRA API] study notes failed: %s", type(e).__name__)
                try:
                    db.rollback()
                except Exception:
                    pass
            if study_images:
                study_blocks["visual"] = study_images
            if slash_cmd == "study":
                study_blocks["actions"] = (["flashcards", "quiz"]
                                           + (["visuals"] if visual_available else []))
                study_blocks["visual_available"] = visual_available
        # --- Web images: "what does a banana look like" → inline photos ---
        # Network call — keep it off the event loop so it can't stall others.
        _imgs: list = []
        try:
            _img_q = web_images.wants_images(gen_text)
            if _img_q and not study_images and not (study_family and req.stream):
                _imgs = await asyncio.to_thread(web_images.search, _img_q)
        except Exception as e:
            log.warning("[ASHRA API] web images failed: %s", type(e).__name__)
        if _imgs:
            reply = reply.rstrip() + "\n\n" + "\n".join(
                f"![{i['title']}]({i['thumb']})" for i in _imgs)
        t.mark("web_images")

        db.add(models.Message(conversation_id=conv.id, role="assistant", content=reply))

        # --- Phase 2+3: extract → score → store (SQL + Qdrant), incl. behaviour patterns ---
        cands = memory_engine.extract_candidate_memories(gen_text)
        beh = behavior_analyzer.behaviour_memory_candidate(gen_text, signals)
        if beh:
            cands.append(beh)
        saved: list[str] = []
        for cand in cands:
            exists = db.query(models.Memory).filter_by(
                user_id=user.id, content=cand["content"], is_active=True).first()
            if exists:
                continue
            mem = models.Memory(user_id=user.id, **cand)
            db.add(mem)
            db.commit()
            db.refresh(mem)
            saved.append(mem.content)
            memory_engine.index_memory(mem.id, user.id, mem.content, mem.kind, mem.memory_type, mem.importance)

        hints = _keyword_profile_hints(gen_text)
        if hints:
            _apply_profile_hints(profile, hints)
            db.commit()

        db.commit()
        db.refresh(profile)
        t.mark("db_post")
        # Title task started before generation — by now it's usually done.
        if title_task is not None:
            try:
                conv.title = await title_task
                db.commit()
            except Exception:
                log.warning("[ASHRA API] auto-title failed", exc_info=True)
        t.mark("auto_title")
        if _PERF:
            log.info("[PERF /chat] %s total=%.0fms", t.report(), t.total() * 1000)
        return {
            "conversation_id": conv.id,
            "reply": reply,
            "sources": [schemas.MemoryHit(**h).model_dump() for h in relevant],
            "adaptation": personality_adapter.summary_for_api(profile),
            "adaptations_made": adaptations_made,
            "tool_calls": tool_calls,
            "saved": saved[:3],
            "study": study_blocks,
            "web_sources": web_sources,
        }

    # --- Streaming path: first token goes out immediately, post-work after ---
    # done is built with ZERO awaits after the last token (sync dict + dumps
    # + yield only) — slow disks, gated LLM calls and flaky proxies can never
    # strand the client mid-stream. Everything awaitable runs either
    # ALONGSIDE generation (harvested via task.done(), no suspension) or
    # AFTER done as fire-and-forget bookkeeping / follow-up events.
    if req.stream:
        async def _events():
            parts: list[str] = []
            first = True
            used_tools = False
            try:
                if mcp_schemas and mcp_exec is not None:
                    # MCP tool turns run non-stream; the final text is emitted
                    # as one delta through the same SSE channel.
                    try:
                        _text, _trace = await ai_core.generate_reply_with_tools(
                            gen_text, ctx, history, memory_context, adaptation_text,
                            deep_thinking=bool(getattr(req, "deep_thinking", False)),
                            max_tokens=cmd_budget, reasoning=cmd_reason,
                            own_creds=own_creds, tools=mcp_schemas, execute_tool=mcp_exec)
                    except Exception as _e:
                        log.warning("[ASHRA API] mcp tool loop failed: %s", type(_e).__name__)
                        _text, _trace = "", []
                    used_tools = True
                    if _trace:
                        tool_calls.extend(_trace)
                    if _text:
                        parts.append(_text)
                        t.mark("llm_ttft")
                        yield "data: " + json.dumps(
                            {"type": "delta", "text": _text}, ensure_ascii=False) + "\n\n"
                    else:
                        used_tools = False  # fall back to the normal stream
                if not used_tools:
                    async for chunk in ai_core.generate_reply_stream(
                            gen_text, ctx, history, memory_context, adaptation_text,
                            deep_thinking=bool(getattr(req, "deep_thinking", False)),
                            max_tokens=cmd_budget,
                            reasoning=cmd_reason, own_creds=own_creds):
                        if first:
                            t.mark("llm_ttft")  # time to first visible token
                            first = False
                        parts.append(chunk)
                        yield "data: " + json.dumps(
                            {"type": "delta", "text": chunk}, ensure_ascii=False) + "\n\n"
            except Exception as e:
                log.warning("[ASHRA API] stream failed after %d chunks: %s",
                            len(parts), type(e).__name__)
                if not parts:
                    for _tt in (title_task, visual_task, _img_task):
                        try:
                            if _tt is not None and not _tt.done():
                                _tt.cancel()
                        except Exception:
                            pass
                    yield "data: " + json.dumps(
                        {"type": "error", "detail": str(e) or "generation failed"}) + "\n\n"
                    return
                # partial text already on screen — save it rather than strand the user
            t.mark("llm")
            reply = "".join(parts)
            if not (reply or "").strip():
                log.warning("[ASHRA API] empty reply, using fallback")
                reply = "Sorry, I couldn't generate a response. Please try again."
            if tool_calls:
                reply = reply.rstrip() + " 🔧[" + ", ".join(c["tool"] for c in tool_calls) + "]"
            # Harvest concurrent image results WITHOUT awaiting.
            _harvested_imgs: list = []
            if _img_task is not None:
                try:
                    if _img_task.done() and not _img_task.cancelled():
                        _harvested_imgs = _img_task.result() or []
                except Exception:
                    _harvested_imgs = []
            if _harvested_imgs:
                try:
                    reply = reply.rstrip() + "\n\n" + "\n".join(
                        f"![{i['title']}]({i['thumb']})" for i in _harvested_imgs)
                except Exception:
                    pass
            if web_sources:
                try:
                    reply = reply.rstrip() + "\n\nSources:\n" + "\n".join(
                        f"{s['n']}. [{s['title']}]({s['url']})" for s in web_sources[:5])
                except Exception:
                    pass
            # Memory + notes + hints + assistant row: sync DB work, safe pre-done.
            saved: list[str] = []
            _to_index: list = []
            study_blocks: dict = {}
            try:
                cands = memory_engine.extract_candidate_memories(gen_text)
                beh = behavior_analyzer.behaviour_memory_candidate(gen_text, signals)
                if beh:
                    cands.append(beh)
                for cand in cands:
                    exists = db.query(models.Memory).filter_by(
                        user_id=user.id, content=cand["content"], is_active=True).first()
                    if exists:
                        continue
                    mem = models.Memory(user_id=user.id, **cand)
                    db.add(mem)
                    db.commit()
                    db.refresh(mem)
                    saved.append(mem.content)
                    _to_index.append(mem)
                hints = _keyword_profile_hints(gen_text)
                if hints:
                    _apply_profile_hints(profile, hints)
                    db.commit()
            except Exception as e:
                log.warning("[ASHRA API] stream persist (pre-done) failed: %s", type(e).__name__)
                try:
                    db.rollback()
                except Exception:
                    pass
            if study_family:
                expect = "quiz" if slash_cmd == "quiz" else "flashcards"
                study_blocks["expect"] = expect
                try:
                    _, fcs, _ = study.split_study(reply, expect=expect)
                    added = 0
                    for fc in fcs:
                        content = f"Q: {fc['q']}\nA: {fc['a']}"
                        exists = db.query(models.Note).filter_by(
                            user_id=user.id, content=content).first()
                        if exists:
                            continue
                        db.add(models.Note(user_id=user.id, title=f"Study: {fc['q']}"[:120],
                                           content=content))
                        added += 1
                    if added:
                        db.commit()
                    if fcs:
                        study_blocks["flashcards"] = fcs
                except Exception as e:
                    log.warning("[ASHRA API] study notes failed: %s", type(e).__name__)
                    try:
                        db.rollback()
                    except Exception:
                        pass
                if slash_cmd == "study" and visual_task is not None:
                    # Staged reveal: record the verdict only if already decided
                    # (no awaiting — images fetch on /visuals tap).
                    _va = False
                    try:
                        if visual_task.done() and not visual_task.cancelled():
                            _va = bool(visual_task.result())
                    except Exception:
                        pass
                    study_blocks["actions"] = (["flashcards", "quiz"]
                                               + (["visuals"] if _va else []))
                    study_blocks["visual_available"] = _va
                if slash_cmd == "visuals":
                    try:
                        reply, _cards = study.extract_cards(reply)
                        if _cards:
                            study_blocks["visual_cards"] = _cards
                        else:
                            _, _svg = study.extract_svg(reply)
                            if _svg:
                                study_blocks["visual_svg"] = _svg
                    except Exception as e:
                        log.warning("[ASHRA API] study visual parse failed: %s",
                                    type(e).__name__)
            try:
                db.add(models.Message(conversation_id=conv.id, role="assistant", content=reply))
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
            try:
                db.refresh(profile)
            except Exception:
                pass
            t.mark("db_post")
            try:
                _src = []
                for h in relevant:
                    try:
                        _src.append(schemas.MemoryHit(**h).model_dump())
                    except Exception:
                        continue
                payload = {
                    "conversation_id": conv.id,
                    "reply": reply,
                    "sources": _src,
                    "adaptation": personality_adapter.summary_for_api(profile),
                    "adaptations_made": adaptations_made,
                    "tool_calls": tool_calls,
                    "saved": saved[:3],
                    "study": study_blocks,
                    "web_sources": web_sources,
                }
            except Exception as e:
                log.warning("[ASHRA API] done payload failed: %s", type(e).__name__)
                payload = {"conversation_id": conv.id, "reply": reply, "sources": [],
                           "adaptation": {}, "adaptations_made": [], "tool_calls": [], "saved": []}
            try:
                done_event = "data: " + json.dumps(
                    {"type": "done", "payload": payload}, ensure_ascii=False, default=str) + "\n\n"
            except Exception as e:
                log.warning("[ASHRA API] done serialize failed: %s", type(e).__name__)
                done_event = "data: " + json.dumps(
                    {"type": "done", "payload": {"conversation_id": conv.id, "reply": reply}},
                    ensure_ascii=False) + "\n\n"
            yield done_event
            # ---- client is complete; slow/awaitable bookkeeping after done ----
            try:
                for _m in _to_index:
                    try:
                        memory_engine.index_memory(_m.id, user.id, _m.content, _m.kind,
                                                   _m.memory_type, _m.importance)
                    except Exception:
                        pass
                if title_task is not None:
                    try:
                        conv.title = await title_task
                        db.commit()
                    except Exception:
                        pass
                t.mark("auto_title")
                if _PERF:
                    log.info("[PERF /chat] %s total=%.0fms", t.report(), t.total() * 1000)
                # Late image results (weren't ready pre-done) as follow-up event.
                if _img_task is not None and not _img_task.done():
                    _late: list = []
                    try:
                        _late = await asyncio.wait_for(asyncio.shield(_img_task), timeout=20) or []
                    except Exception:
                        _late = []
                    if _late:
                        try:
                            _md = "\n\n" + "\n".join(
                                f"![{i['title']}]({i['thumb']})" for i in _late)
                        except Exception:
                            _md = ""
                        if _md:
                            yield "data: " + json.dumps(
                                {"type": "images", "markdown": _md}, ensure_ascii=False) + "\n\n"
                # Study visuals need gated LLM calls — always post-done events.
                if slash_cmd == "visuals" and not study_blocks.get("visual_cards") \
                        and not study_blocks.get("visual_svg"):
                    _got_visual = False
                    try:
                        _retry = await ai_core.generate_reply(
                            f"{study.SVG_RETRY_SYS}\n\nTopic: {slash_topic or slash_arg}",
                            ctx, history, memory_context, adaptation_text,
                            reasoning="low", max_tokens=800, own_creds=own_creds)
                        _, _cards2 = study.extract_cards(_retry)
                        if _cards2:
                            yield "data: " + json.dumps(
                                {"type": "visual_cards", "cards": _cards2},
                                ensure_ascii=False) + "\n\n"
                            _got_visual = True
                        else:
                            _, _svg2 = study.extract_svg(_retry)
                            if _svg2:
                                yield "data: " + json.dumps(
                                    {"type": "visual_svg", "svg": _svg2},
                                    ensure_ascii=False) + "\n\n"
                                _got_visual = True
                    except Exception as e:
                        log.warning("[ASHRA API] study visual retry failed: %s",
                                    type(e).__name__)
                    if not _got_visual:
                        # Deterministic fallback so visuals never come back empty.
                        try:
                            _cards3 = study.cards_from_prose(reply, slash_topic or slash_arg)
                        except Exception:
                            _cards3 = {}
                        if _cards3:
                            yield "data: " + json.dumps(
                                {"type": "visual_cards", "cards": _cards3},
                                ensure_ascii=False) + "\n\n"
            except Exception as e:
                log.warning("[ASHRA API] post-done bookkeeping failed: %s",
                            type(e).__name__)
                try:
                    db.rollback()
                except Exception:
                    pass

        return StreamingResponse(
            _events(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                     # don't let proxies buffer the stream (Render/CF/nginx)
                     "X-Accel-Buffering": "no"})

    # --- JSON path (original contract) ---
    if mcp_schemas and mcp_exec is not None:
        try:
            reply, _trace = await ai_core.generate_reply_with_tools(
                gen_text, ctx, history, memory_context, adaptation_text,
                deep_thinking=bool(getattr(req, "deep_thinking", False)),
                max_tokens=cmd_budget, reasoning=cmd_reason, own_creds=own_creds,
                tools=mcp_schemas, execute_tool=mcp_exec)
            tool_calls.extend(_trace)
        except Exception as e:
            log.warning("[ASHRA API] mcp tool loop failed: %s", type(e).__name__)
            reply = await ai_core.generate_reply(
                gen_text, ctx, history, memory_context, adaptation_text,
                deep_thinking=bool(getattr(req, "deep_thinking", False)),
                max_tokens=cmd_budget, reasoning=cmd_reason, own_creds=own_creds)
    else:
        reply = await ai_core.generate_reply(gen_text, ctx, history, memory_context, adaptation_text,
                                             deep_thinking=bool(getattr(req, "deep_thinking", False)),
                                             max_tokens=cmd_budget,
                                             reasoning=cmd_reason, own_creds=own_creds)
    t.mark("llm")
    log.info("[ASHRA API] generation completed user=%s reply_len=%d", user.id, len(reply or ""))
    return await _finalize(reply)


# --- Sidebar: conversation history (ChatGPT-style UI) ---

@router.get("/conversations/{user_id}", response_model=list[schemas.ConversationOut])
def list_conversations(user_id: str, db: Session = Depends(get_db)):
    convs = (
        db.query(models.Conversation).filter_by(user_id=user_id)
        .order_by(models.Conversation.created_at.desc()).limit(100).all()
    )
    # Conversations that contain a Build & Run project get the </> sidebar icon.
    build_ids = {row[0] for row in db.query(models.BuildSite.conversation_id).all()}
    out = [schemas.ConversationOut.model_validate(c) for c in convs]
    for item in out:
        item.is_build = item.id in build_ids
    return out


@router.get("/conversations/{user_id}/{conv_id}", response_model=schemas.ConversationDetail)
def get_conversation(user_id: str, conv_id: str, db: Session = Depends(get_db)):
    conv = db.query(models.Conversation).filter_by(id=conv_id, user_id=user_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    msgs = (
        db.query(models.Message).filter_by(conversation_id=conv.id)
        .order_by(models.Message.created_at.asc()).limit(500).all()
    )
    return {"id": conv.id, "title": conv.title,
            "messages": [{"role": m.role, "content": m.content,
                          "created_at": m.created_at} for m in msgs]}


@router.delete("/conversations/{user_id}/{conv_id}")
def delete_conversation(user_id: str, conv_id: str, db: Session = Depends(get_db)):
    conv = db.query(models.Conversation).filter_by(id=conv_id, user_id=user_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    db.delete(conv)
    db.commit()
    return {"ok": True}
