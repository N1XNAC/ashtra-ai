from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..config import settings
from ..security import check_rate, client_ip
from ..services import ai_core, memory_engine, behavior_analyzer, personality_adapter, planner, knowledge_graph
from ..services import agent as agent_exec
from ..services.tools import due_reminders

router = APIRouter(prefix="/chat", tags=["chat"])

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
    if req.conversation_id:
        conv = db.query(models.Conversation).filter_by(id=req.conversation_id, user_id=user.id).first()
    if not conv:
        conv = models.Conversation(user_id=user.id, title=req.message[:40])
        db.add(conv)
        db.commit()

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

    # --- Phase 4: planner → tools (runs before reply; results join the context) ---
    tool_steps = planner.plan(req.message)
    tool_calls, tool_context = agent_exec.execute_plan(db, user.id, tool_steps) if tool_steps else ([], "")

    # --- Phase 4 (automation lite): surface pending reminders ---
    pending = due_reminders(db, user.id)
    reminder_context = ("Pending reminders: " + "; ".join(pending)) if pending and len(req.message.split()) < 20 else ""

    # --- Phase 6: observe entities into knowledge graph + surface active goals ---
    graph_ents = knowledge_graph.observe(user.id, req.message)
    goals_active = db.query(models.Goal).filter_by(user_id=user.id, status="active").all()
    goal_context = ""
    if goals_active and len(req.message.split()) < 25:
        goal_context = "Active goals: " + "; ".join(
            f"{g.title} ({g.progress or 0}%)" for g in goals_active[:5])

    # --- Phase 3: behaviour analysis → gradual adaptation (before reply so it takes effect now) ---
    signals = behavior_analyzer.analyze(req.message)
    adaptations_made = behavior_analyzer.apply_to_profile(profile, signals)
    db.commit()

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
    reply = await ai_core.generate_reply(req.message, ctx, history, memory_context, adaptation_text)
    if tool_calls:
        reply = reply.rstrip() + " 🔧[" + ", ".join(c["tool"] for c in tool_calls) + "]"

    db.add(models.Message(conversation_id=conv.id, role="assistant", content=reply))

    # --- Phase 2+3: extract → score → store (SQL + Qdrant), incl. behaviour patterns ---
    cands = memory_engine.extract_candidate_memories(req.message)
    beh = behavior_analyzer.behaviour_memory_candidate(req.message, signals)
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
        memory_engine.index_memory(mem.id, user.id, mem.content, mem.kind, mem.memory_type, mem.importance)

    hints = _keyword_profile_hints(req.message)
    if hints:
        _apply_profile_hints(profile, hints)
        db.commit()

    db.commit()
    db.refresh(profile)
    return {
        "conversation_id": conv.id,
        "reply": reply,
        "sources": [schemas.MemoryHit(**h) for h in relevant],
        "adaptation": personality_adapter.summary_for_api(profile),
        "adaptations_made": adaptations_made,
        "tool_calls": tool_calls,
    }


# --- Sidebar: conversation history (ChatGPT-style UI) ---

@router.get("/conversations/{user_id}", response_model=list[schemas.ConversationOut])
def list_conversations(user_id: str, db: Session = Depends(get_db)):
    convs = (
        db.query(models.Conversation).filter_by(user_id=user_id)
        .order_by(models.Conversation.created_at.desc()).limit(100).all()
    )
    return convs


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
