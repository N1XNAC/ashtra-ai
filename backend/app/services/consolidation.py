"""Phase 6 — Long-term learning: periodic consolidation.

Reviews a time window of conversations and distills it into:
- a digest (topics, activity, new signals) stored as episodic memory + indexed
- goal progress hints (finished/completed language near goal titles)
- stale-pattern note (counts only; deletion stays user-controlled per privacy)

Trigger: POST /learn/consolidate (master, cron, or scheduler). Read: /learn/digest.
"""
import re
from collections import Counter
from datetime import datetime, timedelta

STOP = set("the a an and or of to in on for with is are was were be been i you he she it we they "
           "this that these those my your his her our their as at by from about into over after "
           "me him her us them what when where which who how why do does did can could should would "
           "will just very really so no yes not get got hi hey hello thanks thank please master "
           "here there out up down all any more most other some such only own same than too".split())

WORD = re.compile(r"[a-z]{3,}")


def consolidate(db, user_id: str, days: int = 7) -> dict:
    from .. import models
    from . import memory_engine, knowledge_graph

    since = datetime.utcnow() - timedelta(days=max(1, days))
    conv_ids = [c.id for c in db.query(models.Conversation).filter_by(user_id=user_id).all()]
    msgs = []
    if conv_ids:
        msgs = (db.query(models.Message)
                  .filter(models.Message.conversation_id.in_(conv_ids),
                          models.Message.created_at >= since)
                  .order_by(models.Message.created_at).all())
    if not msgs and conv_ids:  # fresh install / old rows: fall back to all history
        msgs = (db.query(models.Message)
                  .filter(models.Message.conversation_id.in_(conv_ids))
                  .order_by(models.Message.created_at).all())

    user_texts = [m.content for m in msgs if m.role == "user"]
    words = Counter()
    for t in user_texts:
        words.update(w for w in WORD.findall(t.lower()) if w not in STOP)
    topics = [w for w, _ in words.most_common(8)]

    new_mems = db.query(models.Memory).filter_by(user_id=user_id).count()
    goals = db.query(models.Goal).filter_by(user_id=user_id).all()
    active = [g for g in goals if g.status == "active"]
    done = [g for g in goals if g.status == "done"]

    # Goal hints: "finished/done/completed X" near a goal title
    hints: list[str] = []
    for t in user_texts:
        if re.search(r"\b(finished|completed|done with|shipped|launched)\b", t, re.I):
            for g in active:
                kw = set(WORD.findall(g.title.lower())) - STOP
                if kw and any(k in t.lower() for k in kw):
                    hints.append(g.title)

    digest = (
        f"Learning digest ({since.date()} → {datetime.utcnow().date()}): "
        f"{len(msgs)} messages ({len(user_texts)} from master). "
        f"Top topics: {', '.join(topics) if topics else '—'}. "
        f"Memories held: {new_mems}. "
        f"Goals: {len(active)} active, {len(done)} done. " +
        (f"Possible completions: {'; '.join(sorted(set(hints)))}. " if hints else "") +
        "Adaptation continues; deletions remain master's call."
    )
    mem = models.Memory(user_id=user_id, kind="experience", content=digest,
                        importance="high", memory_type="episodic")
    db.add(mem)
    db.commit()
    db.refresh(mem)
    memory_engine.index_memory(mem.id, user_id, mem.content, mem.kind, mem.memory_type, mem.importance)

    # Fold digest topics into the graph as lightweight interest signals
    for t in topics[:5]:
        knowledge_graph.backend_for(user_id).upsert("Interest", t)

    return {"digest": digest, "messages": len(msgs), "topics": topics,
            "goals_active": len(active), "goals_done": len(done),
            "goal_hints": sorted(set(hints)), "memory_id": mem.id}


def latest_digest(db, user_id: str) -> dict | None:
    from .. import models
    m = (db.query(models.Memory)
           .filter_by(user_id=user_id, kind="experience", memory_type="episodic", is_active=True)
           .order_by(models.Memory.created_at.desc()).first())
    if not m or not m.content.startswith("Learning digest"):
        return None
    return {"digest": m.content, "at": m.created_at.isoformat()}
