"""Study mode (/study <topic>): simple explainer + gated visuals + flashcards + quiz.

The explainer LLM writes FLASHCARDS:/QUIZ: sections (parsed here for notes and
by the frontend for rich cards). Visuals are decided per question: explicit
visual words skip the gate; otherwise a tiny YES/NO classifier call decides.
"""
import json
import logging
import re

_log = logging.getLogger("ashtra.study")

STUDY_SYS = (
    "Study mode: teach simply. Start with an ELI5 explanation, then key "
    "points, then one concrete worked example. If a calculator result is "
    "provided in context, trust it for all arithmetic. Do NOT include "
    "flashcards or quiz sections — the student requests those separately."
)

FC_SYS = (
    "Create 2-4 flashcards for this topic. Reply with ONLY a FLASHCARDS: "
    "section, one per line in exactly this format:\n"
    "Q: <short question> | A: <short answer>\n"
    "No intro, no outro, no other text."
)

QUIZ_SYS = (
    "Create 3 quiz questions for this topic, easy to hard. Reply with ONLY "
    "a QUIZ: section, numbered 1-3. No intro, no outro, no answers, no "
    "other text."
)

VISUALS_SYS = (
    "Reply with ONLY two things, in this order: 1-2 intro sentences, then a "
    "```json fence with EXACTLY this shape and nothing after it:\n"
    '{"title": "<short title>", "flow": true, '
    '"items": [{"icon": "<one emoji>", "label": "<3-6 words>", '
    '"text": "<one clear sentence>"}, ...]}\n'
    "Rules: 3 to 5 items that tell the story in order (flow true) or the "
    "key parts (flow false); each label short, each text one sentence a "
    "student gets instantly; a fitting emoji per item. The fenced JSON is "
    "mandatory — a prose-only answer is a failure."
)

SVG_RETRY_SYS = (
    "You MUST output the visual as JSON inside a ```json fence with exactly "
    "this shape (nothing else): "
    '{"title": "<short title>", "flow": true, '
    '"items": [{"icon": "<one emoji>", "label": "<3-6 words>", '
    '"text": "<one clear sentence>"}, ...]} '
    "3 to 5 items. No prose, no explanations — ONLY the fenced JSON."
)


def extract_cards(reply: str) -> tuple[str, dict]:
    """Split a /visuals reply into (body_text, cards_dict). Empty dict when
    the model didn't produce parseable JSON."""
    text = reply or ""
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.I)
    if not m:
        m = re.search(r"(\{[\s\S]*\"items\"\s*:[\s\S]*\})", text, re.I)
    if not m:
        return text, {}
    try:
        cards = json.loads(m.group(1))
    except Exception:
        return text, {}
    items = cards.get("items") if isinstance(cards, dict) else None
    if not isinstance(items, list) or not (2 <= len(items) <= 8):
        return text, {}
    clean_items = []
    for it in items[:6]:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "").strip()[:60]
        desc = str(it.get("text") or "").strip()[:220]
        if not label or not desc:
            continue
        clean_items.append({"icon": str(it.get("icon") or "✨")[:8],
                            "label": label, "text": desc})
    if len(clean_items) < 2:
        return text, {}
    body = (text[:m.start()] + text[m.end():]).strip()
    body = re.sub(r"```[\s\S]*?```", "", body).strip()
    return body, {"title": str(cards.get("title") or "")[:100],
                  "flow": bool(cards.get("flow", True)),
                  "items": clean_items}


_CARD_ICONS = ["💡", "📌", "🔬", "🌍", "⚙️", "🧠"]


def cards_from_prose(reply: str, topic: str) -> dict:
    """Deterministic last resort: turn a prose answer into cards so /visuals
    never comes back empty. Splits long sentences; first words become labels."""
    text = re.sub(r"```[\s\S]*?```", " ", reply or "")
    text = re.sub(r"\s+", " ", text).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text)
                 if len(s.strip()) > 30]
    items = []
    for i, s in enumerate(sentences[:5]):
        words = s.split()
        label = " ".join(words[:6]).rstrip(",;:")[:60]
        items.append({"icon": _CARD_ICONS[i % len(_CARD_ICONS)],
                      "label": label or (topic or "Key point")[:60],
                      "text": s[:220]})
    if len(items) < 2:
        return {}
    title = (topic or "Overview").strip()[:100]
    return {"title": title, "flow": False, "items": items}


def extract_svg(reply: str) -> tuple[str, str]:
    """Split a /visuals reply into (body_text, svg_block). Empty svg when
    the model didn't produce one."""
    text = reply or ""
    m = re.search(r"```(?:svg|html)?\s*(<svg[\s\S]*?</svg>)\s*```", text, re.I)
    if not m:
        m = re.search(r"(<svg[\s\S]*?</svg>)", text, re.I)
    if not m:
        return text, ""
    svg = m.group(1).strip()
    body = (text[:m.start()] + text[m.end():]).strip()
    body = re.sub(r"```[\s\S]*?```", "", body).strip()
    return body, svg[:60000]

GATE_SYS = (
    "Answer ONLY with YES or NO: would pictures, diagrams, or visual examples "
    "genuinely help a student understand this topic, beyond what text alone "
    "can do?"
)

# Explicit visual intent — skip the classifier call entirely.
VISUAL_HINTS = ("show", "picture", "image", "photo", "pic of", "diagram",
                "visual", "draw", "demonstrat", "example", "look like",
                "what does", "step by step", "how it works", "how does")


def resolve_topic(db, conv_id: str, slash_arg: str) -> str:
    """Topic for /flashcards /quiz /visuals: explicit arg wins, else the last
    /study topic in this conversation, else the most recent plain message."""
    import re as _re
    if (slash_arg or "").strip():
        return slash_arg.strip()
    try:
        from .. import models as _models
        rows = (db.query(_models.Message)
                .filter_by(conversation_id=conv_id, role="user")
                .order_by(_models.Message.created_at.desc()).limit(20).all())
        fallback = ""
        for m in rows:
            c = (m.content or "").strip()
            sm = _re.match(r"^/study\s+(.+)$", c, _re.S)
            if sm:
                return sm.group(1).strip()[:500]
            if c and not c.startswith("/") and not fallback:
                fallback = c[:500]
        return fallback
    except Exception:
        return ""


async def visual_needed(text: str) -> bool:
    """Per-question gate: True when the answer deserves visuals."""
    t = (text or "").lower()
    if any(k in t for k in VISUAL_HINTS):
        return True
    if re.search(r"\d\s*[\+\-\*/%^]\s*\d|\d+\s*%|\b(calculate|compute|solve)\b", t):
        return False  # calculator territory — exact text needs no pictures
    try:
        from . import ai_core
        v = await ai_core.generate_reply(f"{GATE_SYS}\n\nTopic: {(text or '')[:500]}")
        return (v or "").strip().upper().startswith("Y")
    except Exception as e:
        _log.info("visual gate failed (%s) — defaulting to no visuals", type(e).__name__)
        return False


def _clean_line(s: str) -> str:
    return re.sub(r"^[\d.)\-\*\s>]+", "", s).strip().strip("`").strip().strip("*").strip()


def split_study(reply: str, expect: str = "") -> tuple[str, list[dict], list[str]]:
    """Split a study reply into (body, flashcards[{q,a}], quiz[str]). Tolerant —
    returns ([], []) when the model skipped the sections. expect="flashcards"
    also accepts bare Q/A lines; expect="quiz" accepts bare numbered lines
    (models often skip the header on format-only replies)."""
    text = reply or ""
    flashcards: list[dict] = []
    quiz: list[str] = []
    fm = re.search(r"(?im)^\s*\**\s*FLASHCARDS?\s*\**:?\s*$", text)
    qm = re.search(r"(?im)^\s*\**\s*QUIZ(ZES)?\s*\**:?\s*$", text)
    fc_text, qz_text = "", ""
    if fm and qm and qm.start() > fm.start():
        fc_text, qz_text = text[fm.end():qm.start()], text[qm.end():]
        body = text[:fm.start()].strip()
    elif fm:
        fc_text, body = text[fm.end():], text[:fm.start()].strip()
    elif qm:
        qz_text, body = text[qm.end():], text[:qm.start()].strip()
    elif expect == "flashcards":
        bare = []
        for line in text.split("\n"):
            t = line.strip()
            if len(t) < 5 or "|" not in t:
                continue
            q, _, a = t.partition("|")
            q = _clean_line(re.sub(r"(?i)^\s*Q\s*:", "", q))
            a = _clean_line(re.sub(r"(?i)^\s*A\s*:", "", a))
            if len(q) > 2 and a:
                bare.append({"q": q[:200], "a": a[:300]})
        if bare:
            return "", bare[:6], []
        return text, [], []
    elif expect == "quiz":
        bare = []
        for line in text.split("\n"):
            t = _clean_line(line)
            if len(t) >= 4 and re.match(r"^[\d]+[.)]\s+\S", line.strip()):
                bare.append(t[:250])
            if len(bare) >= 5:
                break
        if bare:
            return "", [], bare[:5]
        return text, [], []
    else:
        return text, [], []
    for line in fc_text.split("\n"):
        line = line.strip()
        if not line or len(line) < 3:
            continue
        if "|" in line:
            q, _, a = line.partition("|")
        else:
            # Q: ... / A: ... on adjacent lines or "Q...A..." in one line
            parts = re.split(r"(?i)\bA\s*:", line, maxsplit=1)
            if len(parts) == 2:
                q, a = parts
            else:
                continue
        q = _clean_line(re.sub(r"(?i)^\s*Q\s*:", "", q))
        a = _clean_line(re.sub(r"(?i)^\s*A\s*:", "", a))
        if q and a:
            flashcards.append({"q": q[:200], "a": a[:300]})
    for line in qz_text.split("\n"):
        line = _clean_line(line)
        if len(line) >= 4 and not line.upper().startswith(("QUIZ", "FLASHCARD")):
            quiz.append(line[:250])
        if len(quiz) >= 5:
            break
    return body, flashcards[:6], quiz[:5]
