from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..config import settings
from ..security import check_rate, client_ip
from ..services import ai_core, memory_engine, behavior_analyzer, personality_adapter, planner, knowledge_graph, web_images
from ..services import agent as agent_exec
from ..services.tools import due_reminders
import logging
import re

log = logging.getLogger("ashtra.api")

router = APIRouter(prefix="/chat", tags=["chat"])

_TITLE_SYS = ("Generate a short title (max 6 words) for this conversation. "
              "Reply with the title only — no quotes, no punctuation at the end.")


async def _auto_title(message: str) -> str:
    """Useful sidebar title from the first message (falls back to a trim)."""
    fallback = re.sub(r"\s+", " ", message.strip())[:40].strip()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={"model": settings.groq_model,
                      "messages": [{"role": "system", "content": _TITLE_SYS},
                                   {"role": "user", "content": message[:500]}],
                      "max_tokens": 24, "temperature": 0.2,
                      "reasoning_effort": "low"},
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

    # --- Phase 2: conversation retrieval + semantic memory retrieval ---
    history_rows = (
        db.query(models.Message).filter_by(conversation_id=conv.id)
        .order_by(models.Message.created_at.desc()).limit(10).all()
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(history_rows[:-1])]

    relevant = memory_engine.retrieve_relevant(user.id, req.message, top_k=3)
    memory_context = "\n".join(f"- [{h['kind']}] {h['content']}" for h in relevant if h.get("content"))

    # --- Phase 4: planner → tools (non-critical: never block the reply) ---
    tool_calls, tool_context = [], ""
    try:
        tool_steps = planner.plan(req.message)
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
        graph_ents = knowledge_graph.observe(user.id, req.message)
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
        signals = behavior_analyzer.analyze(req.message)
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
    reply = await ai_core.generate_reply(req.message, ctx, history, memory_context, adaptation_text,
                                           deep_thinking=bool(getattr(req, "deep_thinking", False)))
    log.info("[ASHRA API] generation completed user=%s reply_len=%d", user.id, len(reply or ""))
    if not (reply or "").strip():
        log.warning("[ASHRA API] empty reply, using fallback")
        reply = "Sorry, I couldn't generate a response. Please try again."
    if tool_calls:
        reply = reply.rstrip() + " 🔧[" + ", ".join(c["tool"] for c in tool_calls) + "]"
    # --- Web images: "what does a banana look like" → inline photos ---
    _imgs: list = []
    try:
        _img_q = web_images.wants_images(req.message)
        _imgs = web_images.search(_img_q) if _img_q else []
    except Exception as e:
        log.warning("[ASHRA API] web images failed: %s", type(e).__name__)
    if _imgs:
        reply = reply.rstrip() + "\n\n" + "\n".join(
            f"![{i['title']}]({i['thumb']})" for i in _imgs)

    db.add(models.Message(conversation_id=conv.id, role="assistant", content=reply))

    # --- Phase 2+3: extract → score → store (SQL + Qdrant), incl. behaviour patterns ---
    cands = memory_engine.extract_candidate_memories(req.message)
    beh = behavior_analyzer.behaviour_memory_candidate(req.message, signals)
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

    hints = _keyword_profile_hints(req.message)
    if hints:
        _apply_profile_hints(profile, hints)
        db.commit()

    db.commit()
    db.refresh(profile)
    # Auto-title new conversations with something useful for the sidebar.
    if new_conv:
        try:
            conv.title = await _auto_title(req.message)
            db.commit()
        except Exception:
            log.warning("[ASHRA API] auto-title failed", exc_info=True)
    return {
        "conversation_id": conv.id,
        "reply": reply,
        "sources": [schemas.MemoryHit(**h) for h in relevant],
        "adaptation": personality_adapter.summary_for_api(profile),
        "adaptations_made": adaptations_made,
        "tool_calls": tool_calls,
        "saved": saved[:3],
    }


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
