"""Phase 5 — Learning System API.

Conversation → Feedback → Evaluation → Training Dataset → Fine Tuning.
- POST /feedback — rate a reply (feeds future training)
- GET /model/dataset — corpus stats preview
- POST /model/train — train from scratch on Ashtra data
- POST /model/fine-tune — continue training (feedback-weighted)
- GET /model/status — checkpoint, perplexity, config
- POST /model/generate — direct local-inference test
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from .. import models
from ..services.local_model import trainer, inference
from ..services.local_model.corpus import build_texts
from ..services.local_model.tokenizer import Tokenizer
from ..config import settings

router = APIRouter(tags=["model"])


class FeedbackIn(BaseModel):
    user_id: str
    rating: str  # "up" | "down" | "1".."5"
    message_id: str = ""
    note: str = ""


@router.post("/feedback")
def give_feedback(f: FeedbackIn, db: Session = Depends(get_db)):
    text = ""
    if f.message_id:
        m = db.query(models.Message).filter_by(id=f.message_id).first()
        if m:
            text = m.content
    else:  # attach to latest assistant message in user's conversations
        conv_ids = [c.id for c in db.query(models.Conversation).filter_by(user_id=f.user_id).all()]
        if conv_ids:
            m = (db.query(models.Message)
                   .filter(models.Message.conversation_id.in_(conv_ids),
                           models.Message.role == "assistant")
                   .order_by(models.Message.created_at.desc()).first())
            if m:
                text, f.message_id = m.content, m.id
    fb = models.Feedback(user_id=f.user_id, message_id=f.message_id,
                         rating=f.rating, text=text, note=f.note)
    db.add(fb)
    db.commit()
    return {"ok": True, "id": fb.id, "training_signal": str(f.rating).lower() in ("up", "5", "4")}


class TrainIn(BaseModel):
    user_id: Optional[str] = None
    steps: int = 300
    lr: float = 0.01


@router.get("/model/dataset")
def dataset_preview(user_id: Optional[str] = None, db: Session = Depends(get_db)):
    texts, info = build_texts(db, user_id)
    tok = Tokenizer.build(texts)
    lens = sorted(len(tok.encode(t)) for t in texts)
    return {"texts": len(texts), "vocab": tok.vocab_size,
            "median_tokens": lens[len(lens) // 2] if lens else 0,
            "corpus_info": info,
            "sample": texts[:3]}


@router.post("/model/train")
def train_model(t: TrainIn, db: Session = Depends(get_db)):
    meta = trainer.train(db, user_id=t.user_id, steps=max(10, min(t.steps, 2000)),
                         lr=t.lr, base=settings.local_model_dir, fine_tune=False)
    return meta


@router.post("/model/fine-tune")
def fine_tune_model(t: TrainIn, db: Session = Depends(get_db)):
    meta = trainer.train(db, user_id=t.user_id, steps=max(10, min(t.steps, 2000)),
                         lr=min(t.lr, 0.003), base=settings.local_model_dir, fine_tune=True)
    return meta


@router.get("/model/status")
def model_status():
    return trainer.status(base=settings.local_model_dir)


class GenIn(BaseModel):
    prompt: str
    max_new: int = 30
    temperature: float = 0.8


@router.post("/model/generate")
def model_generate(g: GenIn):
    text = inference.generate(g.prompt, max_new=max(1, min(g.max_new, 100)),
                              temperature=g.temperature, base=settings.local_model_dir)
    if text is None:
        return {"ok": False, "hint": "No checkpoint yet, master — POST /model/train first."}
    return {"ok": True, "text": text}
