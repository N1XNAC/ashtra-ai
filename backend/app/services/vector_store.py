"""Phase 2 vector store — replaced Qdrant with pure SQL/pgvector.

Collection: ashtray_memories → memories table (embedding column, user_id index).
Payload stored as JSON columns (kind, content, memory_type, importance).
Exact nearest-neighbor search via pgvector <-> operator.
"""
import uuid as uuidlib
from sqlalchemy import text

from ..database import SessionLocal, engine
from ..config import settings

TABLE = "memories"
VEC_DIM = 384

def _client():
    return SessionLocal()

def ensure_collection(dim: int = VEC_DIM):
    """Create the extension + embedding column if missing (idempotent)."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        if engine.dialect.name == "sqlite":
            existing = {r[1] for r in conn.execute(text(f"PRAGMA table_info({TABLE})"))}
            if "embedding" not in existing:
                conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN embedding TEXT"))
                conn.commit()
        conn.close()

def upsert_memory(memory_id: str, user_id: str, vector: list[float], payload: dict):
    ensure_collection(len(vector))
    db = _client()
    try:
        db.execute(text(
            f"INSERT INTO {TABLE} (id, user_id, kind, content, importance, memory_type, embedding) "
            f"VALUES (:id, :user_id, :kind, :content, :importance, :memory_type, :embedding) "
            f"ON CONFLICT (id) DO UPDATE SET kind=EXCLUDED.kind, content=EXCLUDED.content, "
            f"importance=EXCLUDED.importance, memory_type=EXCLUDED.memory_type, embedding=EXCLUDED.embedding"),
            {"id": str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, memory_id)),
             "user_id": user_id,
             "kind": payload.get("kind", ""), "content": payload.get("content", ""),
             "importance": payload.get("importance", "medium"),
             "memory_type": payload.get("memory_type", "long_term"),
             "embedding": vector})
        db.commit()
    finally:
        db.close()

def search_memories(user_id: str, vector: list[float], top_k: int = 5) -> list[dict]:
    db = _client()
    try:
        rows = db.execute(text(
            f"SELECT id, content, kind, embedding <=> :vec AS score "
            f"FROM {TABLE} "
            f"WHERE user_id = :user_id AND is_active = true AND embedding IS NOT NULL "
            f"ORDER BY embedding <=> :vec LIMIT :top_k"),
            {"user_id": user_id, "vec": vector, "top_k": top_k})
        return [{"memory_id": r[0], "content": r[1], "kind": r[2], "score": float(r[3])}
                for r in rows]
    except Exception:
        return []
    finally:
        db.close()

def delete_memory(memory_id: str):
    db = _client()
    try:
        db.execute(text(
            f"DELETE FROM {TABLE} WHERE id = :id"),
            {"id": str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, memory_id))})
        db.commit()
    finally:
        db.close()
