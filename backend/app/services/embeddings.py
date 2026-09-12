"""Phase 2 embeddings — pluggable, zero-key dev default.

Priority:
1. OpenAI-compatible embeddings (if OPENAI_BASE_URL + KEY set) — real semantics
2. sentence-transformers all-MiniLM-L6-v2 (if installed) — local semantics, 384-dim
3. Hashed bag-of-words (always works, no download) — 384-dim, normalized

Master note: (1) and (2) give true semantic search. (3) is keyword-ish but
lets Phase 2 run everywhere with zero setup. Upgrade path is automatic.
"""
import hashlib
import math
import re

DIM = 384
_WORD = re.compile(r"[a-z0-9]+")

_st_model = None

def _hashed_embed(text: str, dim: int = DIM) -> list[float]:
    vec = [0.0] * dim
    for w in _WORD.findall(text.lower()):
        h = int(hashlib.md5(w.encode()).hexdigest(), 16) % dim
        vec[h] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]

def _try_st_embed(texts: list[str]) -> list[list[float]] | None:
    global _st_model
    try:
        from sentence_transformers import SentenceTransformer
        if _st_model is None:
            _st_model = SentenceTransformer("all-MiniLM-L6-v2")
        vecs = _st_model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]
    except Exception:
        return None

def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch. Returns list of normalized vectors."""
    if not texts:
        return []
    # 1. OpenAI-compatible (sync httpx, lazy import)
    try:
        from .config import settings
        if settings.openai_base_url and settings.openai_api_key:
            import httpx
            r = httpx.post(
                f"{settings.openai_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={"model": "text-embedding-3-small", "input": texts},
                timeout=30,
            )
            if r.status_code < 300:
                data = r.json()["data"]
                data.sort(key=lambda d: d["index"])
                out = []
                for d in data:
                    v = list(map(float, d["embedding"]))
                    n = math.sqrt(sum(x * x for x in v)) or 1.0
                    out.append([x / n for x in v])
                if out:
                    return out
    except Exception:
        pass
    # 2. local sentence-transformers
    st = _try_st_embed(texts)
    if st:
        return st
    # 3. hashed fallback
    return [_hashed_embed(t) for t in texts]

def embed_one(text: str) -> list[float]:
    return embed([text])[0]

def dim_probe() -> int:
    return len(embed_one("probe"))
