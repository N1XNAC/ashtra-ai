from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..services import personality_adapter

router = APIRouter(prefix="/profile", tags=["profile"])

# avatar payloads stay small (canvas-downscaled ~256px JPEG on the client)
_AVATAR_MAX_CHARS = 800_000
_NAME_MAX = 32
_SYSTEM_PROMPT_MAX = 4000

def _get_or_create(user_id: str, db: Session):
    p = db.query(models.UserProfile).filter_by(user_id=user_id).first()
    if not p:
        p = models.UserProfile(user_id=user_id)
        db.add(p)
        db.commit()
        db.refresh(p)
    # backfill Phase-3 defaults on old rows
    for f, d in (("explanation_depth", "balanced"), ("communication_format", "chat"),
                 ("teaching_style", "examples"), ("expertise_level", "intermediate")):
        if getattr(p, f, None) is None:
            setattr(p, f, d)
    if getattr(p, "behaviour_signals", None) is None:
        p.behaviour_signals = {}
    return p

@router.get("/{user_id}", response_model=schemas.ProfileOut)
def get_profile(user_id: str, db: Session = Depends(get_db)):
    return _get_or_create(user_id, db)

@router.patch("/{user_id}", response_model=schemas.ProfileOut)
def update_profile(user_id: str, u: schemas.ProfileUpdate, db: Session = Depends(get_db)):
    p = _get_or_create(user_id, db)
    data = u.model_dump(exclude_none=True)
    av = data.get("avatar_data")
    if av is not None:
        # photo must be an image data URL and small enough for the account row
        if not isinstance(av, str) or not av.startswith("data:image/"):
            raise HTTPException(status_code=400, detail="avatar_data must be an image data URL.")
        if len(av) > _AVATAR_MAX_CHARS:
            raise HTTPException(status_code=400, detail="avatar_data is too large (max ~600 KB).")
    dn = data.get("display_name")
    if dn is not None and len(dn) > _NAME_MAX:
        raise HTTPException(status_code=400, detail=f"display_name must be {_NAME_MAX} characters or fewer.")
    csp = data.get("custom_system_prompt")
    if csp is not None and len(csp) > _SYSTEM_PROMPT_MAX:
        raise HTTPException(status_code=400,
                            detail=f"custom_system_prompt must be {_SYSTEM_PROMPT_MAX} characters or fewer.")
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p

@router.get("/{user_id}/model")
def get_user_model(user_id: str, db: Session = Depends(get_db)):
    """Phase 3: explainable user model — traits + adaptation + confidence + signals."""
    p = _get_or_create(user_id, db)
    beh_mems = db.query(models.Memory).filter_by(
        user_id=user_id, kind="behaviour", is_active=True
    ).order_by(models.Memory.created_at.desc()).limit(10).all()
    return {
        "user_id": user_id,
        "traits": {
            "answer_style": p.answer_style, "tone": p.tone,
            "explanation_depth": p.explanation_depth,
            "communication_format": p.communication_format,
            "teaching_style": p.teaching_style,
            "expertise_level": p.expertise_level,
            "learning_style": p.learning_style,
            "interests": p.interests, "goals": p.goals, "skills": p.skills,
        },
        "adaptation": personality_adapter.summary_for_api(p),
        "directives": personality_adapter.directives(p),
        "behaviour_signals": p.behaviour_signals,
        "recent_behaviour_patterns": [m.content for m in beh_mems],
    }
