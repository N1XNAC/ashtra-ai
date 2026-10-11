"""Perplexity-lite Answer engine.

POST /answer/ask → SSE: start → phase* → sources → delta* → done | error
GET  /answer/threads → recent threads (id, question, focus, created_at)
GET  /answer/threads/{thread_id} → full thread (answer, sources, followups)
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..database import get_db
from ..security import check_rate, client_ip
from ..services import answer_engine

router = APIRouter(prefix="/answer", tags=["answer"])
_log = logging.getLogger("ashtra.answer")

_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
                "X-Accel-Buffering": "no"}


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False, default=str) + "\n\n"


class AskRequest(BaseModel):
    user_id: str = "master-001"
    question: str = ""
    focus: str = "all"  # web | academic | all


@router.post("/ask")
async def ask(req: AskRequest, request: Request, db: Session = Depends(get_db)):
    check_rate(f"answer:{client_ip(request)}:{req.user_id}", settings.chat_rate_limit_per_minute)
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question first.")
    if len(question) > 2000:
        raise HTTPException(status_code=400, detail="Question too long (max 2000 chars).")
    user = db.query(models.User).filter_by(id=req.user_id).first()
    if not user:
        user = models.User(id=req.user_id, email=f"{req.user_id}@local")
        db.add(user)
        db.commit()

    async def _events():
        q: asyncio.Queue = asyncio.Queue(maxsize=32)

        async def _ticker():
            while True:
                await asyncio.sleep(4)
                try:
                    q.put_nowait(("tick", None))
                except asyncio.QueueFull:
                    pass

        async def _runner():
            try:
                async for kind, val in answer_engine.run_answer(
                        question, req.focus, db, user.id):
                    await q.put((kind, val))
            except Exception as e:
                _log.warning("answer failed: %s", type(e).__name__, exc_info=True)
                await q.put(("error", f"{type(e).__name__}: {e}"[:300]))

        ticker = asyncio.create_task(_ticker())
        runner = asyncio.create_task(_runner())
        try:
            yield _sse({"type": "start"})
            while True:
                kind, val = await q.get()
                if kind == "tick":
                    yield _sse({"type": "tick"})
                elif kind == "phase":
                    yield _sse({"type": "phase", "label": val})
                elif kind == "sources":
                    yield _sse({"type": "sources", "sources": val})
                elif kind == "delta":
                    yield _sse({"type": "delta", "text": val})
                elif kind == "done":
                    yield _sse({"type": "done", "payload": val})
                    break
                else:
                    yield _sse({"type": "error", "detail": val})
                    break
        finally:
            ticker.cancel()
            runner.cancel()

    return StreamingResponse(_events(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.get("/threads")
def list_threads(user_id: str = "master-001", db: Session = Depends(get_db)):
    rows = (db.query(models.AnswerThread).filter_by(user_id=user_id)
            .order_by(models.AnswerThread.created_at.desc()).limit(30).all())
    return [{"id": r.id, "question": r.question, "focus": r.focus,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, db: Session = Depends(get_db)):
    r = db.query(models.AnswerThread).filter_by(id=thread_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Thread not found.")
    try:
        sources = json.loads(r.sources or "[]")
    except Exception:
        sources = []
    try:
        followups = json.loads(r.followups or "[]")
    except Exception:
        followups = []
    return {"id": r.id, "question": r.question, "answer": r.answer,
            "sources": sources, "followups": followups, "focus": r.focus}
