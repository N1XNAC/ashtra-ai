from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..services import memory_engine

router = APIRouter(prefix="/memories", tags=["memories"])

@router.post("", response_model=schemas.MemoryOut)
def create(m: schemas.MemoryCreate, db: Session = Depends(get_db)):
    mem = models.Memory(**m.model_dump())
    db.add(mem)
    db.commit()
    db.refresh(mem)
    memory_engine.index_memory(mem.id, mem.user_id, mem.content, mem.kind, mem.memory_type, mem.importance)
    return mem

@router.post("/search", response_model=list[schemas.MemoryHit])
def search(req: schemas.MemorySearchRequest):
    """Phase 2: semantic memory search (Qdrant, user-scoped)."""
    return memory_engine.retrieve_relevant(req.user_id, req.query, top_k=req.top_k)

@router.post("/{user_id}/reindex")
def reindex(user_id: str, db: Session = Depends(get_db)):
    """Rebuild vector index from SQL (source of truth)."""
    mems = db.query(models.Memory).filter_by(user_id=user_id, is_active=True).all()
    n = 0
    for m in mems:
        memory_engine.index_memory(m.id, user_id, m.content, m.kind, m.memory_type, m.importance)
        n += 1
    return {"ok": True, "indexed": n}

@router.get("/{user_id}", response_model=list[schemas.MemoryOut])
def list_memories(user_id: str, db: Session = Depends(get_db)):
    return db.query(models.Memory).filter_by(user_id=user_id, is_active=True).all()

@router.delete("/{memory_id}")
def delete(memory_id: str, db: Session = Depends(get_db)):
    mem = db.query(models.Memory).filter_by(id=memory_id).first()
    if not mem:
        raise HTTPException(404, "not found")
    mem.is_active = False  # soft delete — user-controlled per Prd §5
    db.commit()
    memory_engine.remove_from_index(memory_id)
    return {"ok": True}

@router.get("/{user_id}/export")
def export(user_id: str, db: Session = Depends(get_db)):
    # DATA_PRIVACY_RULES: export everything
    mems = db.query(models.Memory).filter_by(user_id=user_id).all()
    prof = db.query(models.UserProfile).filter_by(user_id=user_id).first()
    return {
        "memories": [{"kind": m.kind, "content": m.content, "type": m.memory_type} for m in mems],
        "profile": {
            "answer_style": prof.answer_style, "tone": prof.tone,
            "interests": prof.interests, "goals": prof.goals, "skills": prof.skills,
        } if prof else None,
    }
