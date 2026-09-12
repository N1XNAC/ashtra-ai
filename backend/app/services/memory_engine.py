"""Phase 2 MEMORY pipeline: extract → score → store → retrieve.

Pipeline per Memory_Architecture.txt:
Conversation → extraction → importance scoring → (optional approval) → storage → retrieval.

Storage is dual: SQL (source of truth, user-editable) + Qdrant (semantic index).
Retrieval: vector search with keyword fallback.
"""
from . import embeddings, vector_store

def importance_score(text: str, kind: str) -> str:
    t = text.lower()
    if any(k in t for k in ("my goal", "i want to", "always", "never")):
        return "high"
    if kind in ("preference", "fact") or len(text) > 80:
        return "medium"
    return "low"

def extract_candidate_memories(text: str) -> list[dict]:
    t = text.lower()
    out = []
    if any(k in t for k in ("i like", "i prefer", "i love", "i hate", "i usually", "i always")):
        out.append({"kind": "preference", "content": text.strip(), "memory_type": "long_term"})
    if any(k in t for k in ("my goal", "i want to", "i'm working on", "im working on", "my project")):
        out.append({"kind": "fact", "content": text.strip(), "memory_type": "long_term"})
    if any(k in t for k in ("i learned", "i finished", "i completed", "i struggled")):
        out.append({"kind": "experience", "content": text.strip(), "memory_type": "episodic"})
    # NOTE: behaviour patterns are handled by behavior_analyzer (signal-gated),
    # not here — avoids duplicate "Interaction pattern" memories.
    for c in out:
        c["importance"] = importance_score(text, c["kind"])
    # dedup within batch
    seen, uniq = set(), []
    for c in out:
        if c["content"] not in seen:
            seen.add(c["content"])
            uniq.append(c)
    return uniq

def index_memory(memory_id: str, user_id: str, content: str, kind: str, memory_type: str, importance: str):
    try:
        vec = embeddings.embed_one(f"{kind}: {content}")
        vector_store.upsert_memory(memory_id, user_id, vec, {
            "kind": kind, "content": content,
            "memory_type": memory_type, "importance": importance,
        })
    except Exception:
        pass  # SQL remains source of truth; vector is best-effort

def retrieve_relevant(user_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Semantic search over user's memories. Falls back to [] on any error."""
    try:
        vec = embeddings.embed_one(query)
        return vector_store.search_memories(user_id, vec, top_k=top_k)
    except Exception:
        return []

def remove_from_index(memory_id: str):
    try:
        vector_store.delete_memory(memory_id)
    except Exception:
        pass
