"""BYOK (Bring Your Own Key) API.

GET    /byok/{user_id}/providers   → list providers + masked status
PUT    /byok/keys                  → save + live-test a key for a user
DELETE /byok/keys/{provider}       → remove a user's saved key
POST   /byok/test                  → live-test a key without saving it
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import byok as byok_svc

router = APIRouter(prefix="/byok", tags=["byok"])


class KeyIn(BaseModel):
    user_id: str
    provider: str
    api_key: str
    model: str = ""


class TestIn(BaseModel):
    provider: str
    api_key: str


@router.get("/{user_id}/providers")
def list_providers(user_id: str, db: Session = Depends(get_db)):
    out = []
    for pid, info in byok_svc.PROVIDERS.items():
        row = byok_svc.get_row(db, user_id, pid)
        out.append({
            "id": pid,
            "label": info["label"],
            "docs": info["docs"],
            "models": info["models"],
            "default_model": info["default_model"](),
            "configured": bool(row and row.api_key),
            "masked_key": byok_svc.mask_key(row.api_key) if row else "",
            "model": (row.model if row and row.model else ""),
            "last_error": (row.last_error if row else None),
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        })
    return {"providers": out}


@router.put("/keys")
async def put_key(body: KeyIn, db: Session = Depends(get_db)):
    if body.provider not in byok_svc.PROVIDERS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported provider (choose: {', '.join(byok_svc.PROVIDERS)}).")
    key = (body.api_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key is empty.")
    # Live-test before saving — never persist a key we know is rejected.
    ok, msg = await byok_svc.test_key(body.provider, key)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    row = byok_svc.get_row(db, body.user_id, body.provider)
    if not row:
        row = models.UserApiKey(user_id=body.user_id, provider=body.provider)
        db.add(row)
    row.api_key = key
    row.model = (body.model or "").strip() or None
    row.last_error = None
    db.commit()
    db.refresh(row)
    return {"ok": True, "message": msg, "provider": body.provider,
            "masked_key": byok_svc.mask_key(row.api_key), "model": row.model or ""}


@router.delete("/keys/{provider}")
def delete_key(provider: str, user_id: str, db: Session = Depends(get_db)):
    row = byok_svc.get_row(db, user_id, provider)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True, "provider": provider}


@router.post("/test")
async def test_key(body: TestIn):
    ok, msg = await byok_svc.test_key(body.provider, (body.api_key or "").strip())
    return {"ok": ok, "message": msg}
