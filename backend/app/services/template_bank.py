"""Build & Run — template bank: few-shot style retrieval.

The nine reference templates are distilled into small JSON "style cards"
(palette, type, layout, motion, voice) under `app/data/templates/`. At build
time we embed the prompt plus every card's search text, take the best cosine
match, and hand that card to the build model as a design reference — the model
then writes its own markup in that style.

No weights are trained: this is how a hosted model gets taught by example
(Groq exposes no fine-tuning API). Reuses the existing embeddings service, so
OpenAI -> sentence-transformers -> hashed fallback all keep working.

Card schema (only "name" is required, everything else is optional):
    id, name, category, description, tags[], palette{}, typography{},
    layout[], motion[], components[], voice, avoid[]
"""
import json
import re
from pathlib import Path

from .embeddings import embed

_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"
_WORD = re.compile(r"[a-z0-9]+")

_cards: list | None = None


def _search_text(c: dict) -> str:
    """What we embed: name + category + description + tags + section order."""
    parts = [c.get("name", ""), c.get("category", ""), c.get("description", "")]
    parts += list(c.get("tags", []))
    parts += list(c.get("layout", []))
    return " ".join(str(p) for p in parts if p)


def load() -> list:
    """All style cards, lazy + cached. Empty list when none are installed."""
    global _cards
    if _cards is None:
        try:
            paths = sorted(_DIR.glob("*.json"))
        except Exception:
            paths = []
        found = []
        for p in paths:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue  # one bad card must never break every build
            if isinstance(data, dict) and data.get("name"):
                data.setdefault("id", p.stem)
                found.append(data)
        _cards = found
    return _cards


def reset() -> None:
    """Drop the cache — call after installing new cards."""
    global _cards
    _cards = None


def _cos(a, b) -> float:
    # embeddings are normalized, so the dot product IS the cosine
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _tag_hit(prompt: str, c: dict) -> float:
    """Keyword co-signal. Keeps matching sensible under the hashed-embedding
    fallback, which is bag-of-words rather than true semantics."""
    words = set(_WORD.findall(prompt.lower()))
    tags = set()
    for t in c.get("tags", []):
        tags.update(_WORD.findall(str(t).lower()))
    if not words or not tags:
        return 0.0
    return len(words & tags) / len(tags)


def best(prompt: str, top: int = 2) -> list:
    """Top-`top` style cards for this prompt, most relevant first."""
    cards = load()
    if not cards or not prompt.strip():
        return []
    try:
        prompt_vec = embed([prompt])[0]
        card_vecs = embed([_search_text(c) for c in cards])
    except Exception:
        prompt_vec, card_vecs = None, []
    scored = []
    for i, c in enumerate(cards):
        vec = card_vecs[i] if i < len(card_vecs) else None
        scored.append((_cos(prompt_vec, vec) + 0.25 * _tag_hit(prompt, c), c))
    scored.sort(key=lambda s: -s[0])
    return [c for _, c in scored[: max(1, top)]]


def render(c: dict) -> str:
    """Few-shot block handed to the build model as a design reference."""
    if not c:
        return ""
    ref = c.get("name", "reference")
    cat = f" ({c['category']})" if c.get("category") else ""
    lines = [
        f'DESIGN REFERENCE — match the style of "{ref}"{cat}. Apply this design '
        "system but write your own markup and copy for the requested site; "
        "do not mention the reference."
    ]
    if c.get("description"):
        lines.append(f"Vibe: {c['description']}")
    pal = c.get("palette") or {}
    if pal:
        lines.append("Palette: " + ", ".join(f"{k} {v}" for k, v in pal.items()))
    typ = c.get("typography") or {}
    if typ:
        lines.append("Typography: " + "; ".join(f"{k}: {v}" for k, v in typ.items()))
    for key, label in (("layout", "Sections in order"),
                       ("motion", "Motion"),
                       ("components", "Signature components")):
        vals = [str(v) for v in (c.get(key) or [])]
        if vals:
            lines.append(f"{label}: " + " -> ".join(vals))
    if c.get("voice"):
        lines.append(f"Voice: {c['voice']}")
    if c.get("avoid"):
        lines.append("Avoid: " + "; ".join(str(v) for v in c["avoid"]))
    return "\n".join(lines)
