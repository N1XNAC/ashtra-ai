"""Phase 2 vector store — Qdrant.

Dev default: embedded local mode at ./qdrant_data (no server needed).
Prod: set QDRANT_URL (+ QDRANT_API_KEY) to use a server/cluster.

Collection: ashtra_memories
Payload: {memory_id, user_id, kind, content, memory_type, importance}
"""
from functools import lru_cache
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, Filter, FieldCondition, MatchValue, PointStruct
import uuid as uuidlib

COLLECTION = "ashtray_memories"

_client = None
_client_dim = None

def _settings():
    try:
        from ..config import settings
        return settings
    except Exception:
        return None

def get_client():
    global _client, _client_dim
    if _client is not None:
        return _client
    s = _settings()
    url = getattr(s, "qdrant_url", "") if s else ""
    api_key = getattr(s, "qdrant_api_key", "") if s else ""
    path = getattr(s, "qdrant_path", "./qdrant_data") if s else "./qdrant_data"
    if url:
        _client = QdrantClient(url=url, api_key=api_key or None)
    else:
        _client = QdrantClient(path=path)
    return _client

def ensure_collection(dim: int):
    c = get_client()
    try:
        info = c.get_collection(COLLECTION)
        if info.config.params.vectors.size != dim:
            c.recreate_collection(COLLECTION, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
    except Exception:
        c.recreate_collection(COLLECTION, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))

def upsert_memory(memory_id: str, user_id: str, vector: list[float], payload: dict):
    c = get_client()
    ensure_collection(len(vector))
    c.upsert(COLLECTION, points=[PointStruct(
        id=str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, memory_id)),
        vector=vector,
        payload={"memory_id": memory_id, "user_id": user_id, **payload},
    )])

def search_memories(user_id: str, vector: list[float], top_k: int = 5) -> list[dict]:
    c = get_client()
    ensure_collection(len(vector))
    try:
        hits = c.query_points(
            COLLECTION,
            query=vector,
            query_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]),
            limit=top_k,
        ).points
    except Exception:
        return []
    return [{"memory_id": h.payload.get("memory_id"), "content": h.payload.get("content", ""),
             "kind": h.payload.get("kind", ""), "score": float(h.score)} for h in hits]

def delete_memory(memory_id: str):
    c = get_client()
    try:
        c.delete(COLLECTION, points_selector=[str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, memory_id))])
    except Exception:
        pass
