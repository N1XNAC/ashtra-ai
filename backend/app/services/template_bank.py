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

# Bag-of-words embeddings count stopwords, and every card is full of "the",
# "with", "and". That shared mass becomes a ~0.2 baseline that swamps real
# signal, so a plumber prompt could confidently match a photography archive.
# Strip them from BOTH the query and the card text before embedding.
_STOP = frozenset("""
a an the and or but if then else for to of in on at by with from as is are be
was were being it its this that these those you your we our they their i me my
he she his her them us what which who whom whose when where why how not no nor
so too very can will just about into over under again further only own same
than such s t don now d ll m o re ve y ar
site page web website online create build make need want using use one two
""".split())

# Tags are hand-written and far more precise than prose — weight them up.
TAG_WEIGHT = 0.40

# Abstain rather than inject a confidently-wrong design system.
# MIN_SCORE         : too weak overall (no card resembles this prompt)
# MIN_MARGIN        : top two too close (ambiguous — better no style than a coin flip)
# MIN_TAG_COVERAGE  : the winner's tags must explain at least this share of the
#                     prompt. Stops a single incidental tag ("agency" on a
#                     creative-portfolio card) from picking a whole style for
#                     an unrelated business.
# With prompt-normalised tag coverage a genuine match lands well above 0.4,
# while an unrelated request tops out around 0.15-0.19 on cosine alone.
MIN_SCORE = 0.20
MIN_MARGIN = 0.05
MIN_TAG_COVERAGE = 0.45

_cards: list | None = None


def _clean(text: str) -> str:
    """Lowercase + drop stopwords so only meaningful tokens reach the embedder."""
    return " ".join(w for w in _WORD.findall(text.lower()) if w not in _STOP)


def _search_text(c: dict) -> str:
    """What we embed: name + category + description + tags + section order."""
    parts = [c.get("name", ""), c.get("category", ""), c.get("description", "")]
    parts += list(c.get("tags", []))
    parts += list(c.get("layout", []))
    return _clean(" ".join(str(p) for p in parts if p))


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
    """Fraction of the prompt's meaningful words covered by this card's tags.

    Normalised by the PROMPT, not the tag list: a card with 15 tags should not
    be penalised when 3 of them describe the request exactly. Also the main
    defence against the hashed bag-of-words embedder, which is keyword-ish
    rather than semantic.
    """
    words = set(_clean(prompt).split())
    tags = set()
    for t in c.get("tags", []):
        tags.update(_clean(str(t)).split())
    if not words or not tags:
        return 0.0
    return len(words & tags) / len(words)


def best(prompt: str, top: int = 2) -> list:
    """Top-`top` style cards for this prompt, most relevant first.

    Returns [] when nothing scores confidently enough. An absent design
    reference just yields a generic site; a wrong one yields a wrong site,
    so abstaining beats guessing.
    """
    cards = load()
    if not cards or not prompt.strip():
        return []
    try:
        prompt_vec = embed([_clean(prompt)])[0]
        card_vecs = embed([_search_text(c) for c in cards])
    except Exception:
        prompt_vec, card_vecs = None, []
    scored = []
    for i, c in enumerate(cards):
        vec = card_vecs[i] if i < len(card_vecs) else None
        hit = _tag_hit(prompt, c)
        scored.append((_cos(prompt_vec, vec) + TAG_WEIGHT * hit, hit, c))
    scored.sort(key=lambda s: -s[0])
    top_score, top_hit = scored[0][0], scored[0][1]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if top_score < MIN_SCORE or (top_score - runner_up) < MIN_MARGIN:
        return []
    if top_hit < MIN_TAG_COVERAGE:
        return []
    return [c for _, _, c in scored[: max(1, top)]]


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
