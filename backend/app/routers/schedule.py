"""Scheduled AI messages — ask now, delivered as chat replies at a set time."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db

router = APIRouter(prefix="/schedule", tags=["schedule"])


class ScheduleRequest(BaseModel):
    user_id: str = "master-001"
    prompt: str
    run_at: datetime  # ISO-8601


@router.post("")
def create(req: ScheduleRequest, db: Session = Depends(get_db)):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is empty.")
    if req.run_at <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="Time must be in the future.")
    user = db.query(models.User).filter_by(id=req.user_id).first()
    if not user:
        user = models.User(id=req.user_id, email=f"{req.user_id}@local")
        db.add(user)
        db.commit()
    item = models.ScheduledMsg(user_id=user.id, prompt=req.prompt.strip(), run_at=req.run_at)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "run_at": item.run_at.isoformat()}


@router.get("/{user_id}")
def list_items(user_id: str, db: Session = Depends(get_db)):
    items = (db.query(models.ScheduledMsg).filter_by(user_id=user_id)
             .order_by(models.ScheduledMsg.run_at.asc()).limit(50).all())
    return [{"id": i.id, "prompt": i.prompt, "run_at": i.run_at.isoformat(),
             "done": i.done} for i in items]


@router.delete("/{item_id}")
def cancel(item_id: str, db: Session = Depends(get_db)):
    item = db.query(models.ScheduledMsg).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Not found.")
    db.delete(item)
    db.commit()
    return {"ok": True}
