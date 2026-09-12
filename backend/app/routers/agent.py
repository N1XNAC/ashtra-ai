"""Phase 4 — Agent + tool REST API.

- POST /agent/run — full pipeline: plan → execute → synthesized reply
- Notes / calendar / files / search direct endpoints (also reachable via chat intents)
"""
import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..services import planner, agent as agent_exec, tools
from ..services.tools import _user_dir, _safe_path

router = APIRouter(tags=["agent"])

def _user(db: Session, user_id: str):
    u = db.query(models.User).filter_by(id=user_id).first()
    if not u:
        u = models.User(id=user_id, email=f"{user_id}@local")
        db.add(u)
        db.commit()
    return u

@router.post("/agent/run", response_model=schemas.AgentResponse)
def agent_run(req: schemas.AgentRequest, db: Session = Depends(get_db)):
    _user(db, req.user_id)
    steps = planner.plan(req.message)
    if not steps:
        return {"reply": "No tool needed, master — that's plain chat. Try 'calculate 12*8', 'take a note …', 'remind me …', 'search for …', or upload a file then 'analyze file x.py'.",
                "steps": [], "tool_calls": []}
    calls, _ = agent_exec.execute_plan(db, req.user_id, steps)
    bits = []
    for c in calls:
        if c["tool"] == "calculator":
            bits.append(f"🧮 {c['args']['expression']} = {c['result']}" if c["ok"] else f"calc failed: {c['result']}")
        elif c["tool"] == "notes_create":
            bits.append(f"📝 note saved: {c['result'].get('title')}" if c["ok"] else "note failed")
        elif c["tool"] == "notes_list":
            ns = c["result"] if isinstance(c["result"], list) else []
            bits.append(f"📝 {len(ns)} notes: " + "; ".join(n["title"] for n in ns[:5]) if ns else "📝 no notes yet")
        elif c["tool"] == "calendar_create":
            bits.append(f"⏰ reminder set: {c['result'].get('title')}" + (f" (due {c['result'].get('due')})" if c["result"].get("due") else ""))
        elif c["tool"] == "calendar_list":
            evs = c["result"] if isinstance(c["result"], list) else []
            bits.append(f"⏰ {len(evs)} events: " + "; ".join(e["title"] for e in evs[:5]) if evs else "⏰ nothing scheduled")
        elif c["tool"] == "web_search":
            r = c["result"] if isinstance(c["result"], dict) else {}
            bits.append(f"🔍 {r.get('answer') or 'no instant answer'}" + (f" ({r.get('source')})" if r.get("source") else ""))
        elif c["tool"] == "file_list":
            fs = c["result"] if isinstance(c["result"], list) else []
            bits.append(f"📁 {len(fs)} files: " + ", ".join(fs[:10]) if fs else "📁 no uploads yet")
        elif c["tool"] == "file_read":
            bits.append(f"📄 {(c['result'][:300] + '…') if isinstance(c['result'], str) and len(c['result']) > 300 else c['result']}")
        elif c["tool"] == "code_analyze":
            r = c["result"] if isinstance(c["result"], dict) else {}
            bits.append(f"💻 {r.get('lines')} lines, funcs={r.get('functions', [])}, classes={r.get('classes', [])}")
        elif c["tool"] == "goal_create":
            bits.append(f"🎯 goal tracked: {c['result'].get('title')}" if c["ok"] else "goal failed")
        elif c["tool"] == "goal_list":
            gs = c["result"] if isinstance(c["result"], list) else []
            act = [g for g in gs if g["status"] == "active"]
            bits.append(f"🎯 {len(act)} active: " + "; ".join(f"{g['title']} ({g['progress']}%)" for g in act[:5]) if act else "🎯 no active goals")
        elif c["tool"] == "goal_done":
            bits.append(f"🏆 goal complete: {c['result'].get('title')}" if c["ok"] else str(c["result"]))
        elif c["tool"] == "goal_progress":
            bits.append(f"📈 {c['result'].get('title')}: {c['result'].get('progress')}%" if c["ok"] else str(c["result"]))
    return {"reply": "Yes, master. " + " ".join(bits),
            "steps": [f"{s['tool']} — {s['reason']}" for s in steps],
            "tool_calls": calls}

# --- notes ---
@router.get("/notes/{user_id}", response_model=list[schemas.NoteOut])
def list_notes(user_id: str, db: Session = Depends(get_db)):
    return db.query(models.Note).filter_by(user_id=user_id).order_by(models.Note.created_at.desc()).all()

@router.delete("/notes/{note_id}")
def delete_note(note_id: str, db: Session = Depends(get_db)):
    n = db.query(models.Note).filter_by(id=note_id).first()
    if not n:
        raise HTTPException(404, "not found")
    db.delete(n)
    db.commit()
    return {"ok": True}

# --- calendar ---
@router.get("/events/{user_id}", response_model=list[schemas.EventOut])
def list_events(user_id: str, db: Session = Depends(get_db)):
    return db.query(models.CalendarEvent).filter_by(user_id=user_id).order_by(models.CalendarEvent.created_at.desc()).all()

@router.post("/events/{user_id}", response_model=schemas.EventOut)
def create_event(user_id: str, e: schemas.EventCreate, db: Session = Depends(get_db)):
    _user(db, user_id)
    ev = models.CalendarEvent(user_id=user_id, title=e.title, due=e.due)
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev

@router.post("/events/{event_id}/done")
def event_done(event_id: str, db: Session = Depends(get_db)):
    e = db.query(models.CalendarEvent).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(404, "not found")
    e.done = True
    db.commit()
    return {"ok": True}

# --- files ---
@router.post("/files/{user_id}/upload")
async def upload_file(user_id: str, f: UploadFile = File(...), db: Session = Depends(get_db)):
    _user(db, user_id)
    dest = _safe_path(user_id, f.filename or "upload.bin")
    content = await f.read()
    if len(content) > 5_000_000:
        raise HTTPException(400, "file too large (>5MB)")
    with open(dest, "wb") as out:
        out.write(content)
    db.add(models.Memory(user_id=user_id, kind="fact", content=f"Uploaded file: {os.path.basename(dest)} ({len(content)} bytes)",
                          importance="low", memory_type="long_term"))
    db.commit()
    return {"ok": True, "name": os.path.basename(dest), "bytes": len(content)}

@router.get("/files/{user_id}")
def files(user_id: str):
    return tools.tool_list_files(user_id)

@router.post("/search")
def search(q: schemas.MemorySearchRequest):
    # NOTE: distinct from /memories/search (which is semantic memory search).
    # This is web search passthrough.
    return tools.tool_web_search(q.query)
