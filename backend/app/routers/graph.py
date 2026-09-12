"""Phase 6 — Graph + Goals + Learning API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from .. import models, schemas
from ..services import knowledge_graph, consolidation

router = APIRouter(tags=["graph"])


def _user(db: Session, user_id: str):
    u = db.query(models.User).filter_by(id=user_id).first()
    if not u:
        u = models.User(id=user_id, email=f"{user_id}@local")
        db.add(u)
        db.commit()
    return u


# --- knowledge graph ---
@router.get("/graph/{user_id}")
def get_graph(user_id: str):
    return knowledge_graph.backend_for(user_id).all()


@router.post("/graph/{user_id}/rebuild")
def rebuild_graph(user_id: str, db: Session = Depends(get_db)):
    _user(db, user_id)
    return knowledge_graph.rebuild_from_db(db, user_id)


# --- goals ---
class GoalCreate(BaseModel):
    title: str
    milestones: list[str] = []


class GoalProgress(BaseModel):
    progress: Optional[int] = None
    status: Optional[str] = None  # active | done | paused
    milestone: Optional[str] = None  # milestone title to toggle done


@router.get("/goals/{user_id}", response_model=list[schemas.GoalOut])
def list_goals(user_id: str, db: Session = Depends(get_db)):
    return (db.query(models.Goal).filter_by(user_id=user_id)
              .order_by(models.Goal.created_at.desc()).all())


@router.post("/goals/{user_id}", response_model=schemas.GoalOut)
def create_goal(user_id: str, g: GoalCreate, db: Session = Depends(get_db)):
    _user(db, user_id)
    goal = models.Goal(user_id=user_id, title=g.title.strip()[:200],
                       milestones=[{"title": m, "done": False} for m in g.milestones[:20]])
    db.add(goal)
    db.commit()
    db.refresh(goal)
    knowledge_graph.backend_for(user_id).upsert("Goal", goal.title)
    return goal


@router.patch("/goals/{goal_id}", response_model=schemas.GoalOut)
def update_goal(goal_id: str, u: GoalProgress, db: Session = Depends(get_db)):
    g = db.query(models.Goal).filter_by(id=goal_id).first()
    if not g:
        raise HTTPException(404, "goal not found")
    if u.progress is not None:
        g.progress = max(0, min(100, u.progress))
        if g.progress >= 100:
            g.status = "done"
    if u.status in ("active", "done", "paused"):
        g.status = u.status
        if u.status == "done":
            g.progress = 100
    if u.milestone:
        ms = list(g.milestones or [])
        for m in ms:
            if m.get("title", "").lower() == u.milestone.lower():
                m["done"] = not m.get("done")
        g.milestones = ms
        done_n = sum(1 for m in ms if m.get("done"))
        if ms:
            g.progress = int(100 * done_n / len(ms))
    db.commit()
    db.refresh(g)
    return g


@router.get("/goals/{user_id}/dashboard")
def goals_dashboard(user_id: str, db: Session = Depends(get_db)):
    goals = db.query(models.Goal).filter_by(user_id=user_id).all()
    active = [g for g in goals if g.status == "active"]
    avg = int(sum(g.progress or 0 for g in active) / len(active)) if active else 0
    return {"active": len(active), "done": sum(1 for g in goals if g.status == "done"),
            "paused": sum(1 for g in goals if g.status == "paused"),
            "avg_progress": avg,
            "goals": [{"id": g.id, "title": g.title, "status": g.status,
                       "progress": g.progress, "milestones": g.milestones} for g in goals]}


# --- long-term learning ---
@router.post("/learn/consolidate")
def run_consolidation(user_id: str, days: int = 7, db: Session = Depends(get_db)):
    _user(db, user_id)
    return consolidation.consolidate(db, user_id, days=days)


@router.get("/learn/digest")
def get_digest(user_id: str, db: Session = Depends(get_db)):
    d = consolidation.latest_digest(db, user_id)
    return d or {"digest": None, "hint": "No digest yet, master — POST /learn/consolidate first."}
