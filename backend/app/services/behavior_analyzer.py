"""Phase 3 — Behaviour analysis (Prd §3, DEVELOPMENT_PHASES P3).

Analyzes each user message for stable patterns, accumulates counters in
profile.behaviour_signals, and gradually adapts profile fields.
Adaptation is gradual (threshold >= 2 observations) and explainable.

Signals tracked:
- wants_brief / wants_detail (depth)
- wants_examples / wants_theory / wants_hands_on (teaching style)
- wants_bullets / wants_tutorial / wants_code (format)
- technical_q / beginner_signal / advanced_signal (expertise)
- formal / casual tone hints
"""
import re

TECH_JARGON = ("api", "async", "docker", "kubernetes", "sql", "embedding",
               "transformer", "qdrant", "postgres", "fastapi", "react",
               "decorator", "recursion", "complexity", "refactor")

def analyze(text: str) -> dict:
    t = text.lower()
    s: dict = {}
    if re.search(r"\b(brief|briefly|concise|short|tldr|in short)\b", t):
        s["wants_brief"] = True
    if re.search(r"\b(detail|detailed|in depth|in-depth|thorough|explain fully|deep dive)\b", t):
        s["wants_detail"] = True
    if re.search(r"\b(example|examples|show me|e\.g\.|for instance)\b", t):
        s["wants_examples"] = True
    if re.search(r"\b(theory|why does|how does.*work|fundamental|concept)\b", t):
        s["wants_theory"] = True
    if re.search(r"\b(hands.?on|exercise|practice|walk me through|step by step|tutorial)\b", t):
        s["wants_hands_on"] = True
    if re.search(r"\b(bullet|bullets|list|points|outline)\b", t):
        s["wants_bullets"] = True
    if re.search(r"\b(code|script|function|debug|error|stack trace)\b", t):
        s["wants_code"] = True
    if sum(1 for k in TECH_JARGON if k in t) >= 2 or re.search(r"\b(optimize|architecture|trade-?off)\b", t):
        s["advanced_signal"] = True
    if re.search(r"\b(explain simply|like i'm 5|eli5|beginner|just starting|new to)\b", t):
        s["beginner_signal"] = True
    if text.endswith("?") and any(k in t for k in ("how", "why", "what", "debug", "error", "implement")):
        s["technical_q"] = True
    if re.search(r"\b(dear sir|kindly|would you please|respectfully)\b", t):
        s["formal"] = True
    if re.search(r"\b(hey|yo|cool|gonna|wanna|lol)\b", t):
        s["casual"] = True
    return s

def _bump(signals: dict, key: str, n: int = 1):
    signals[key] = int(signals.get(key, 0)) + n

def apply_to_profile(profile, signals: dict) -> list[str]:
    """Mutate profile in place. Returns list of human-readable adaptations made."""
    bs = dict(getattr(profile, "behaviour_signals", None) or {})
    made: list[str] = []

    for k, v in signals.items():
        if v:
            _bump(bs, k)

    def reached(k: str, thresh: int = 2) -> bool:
        return int(bs.get(k, 0)) >= thresh

    # Depth — brief wins ties (explicit brevity is a strong signal)
    if reached("wants_brief") and profile.explanation_depth != "concise":
        profile.explanation_depth, profile.answer_style = "concise", "concise"
        made.append("Switched to concise answers (you asked for brief several times).")
    elif reached("wants_detail") and not reached("wants_brief") and profile.explanation_depth != "detailed":
        profile.explanation_depth, profile.answer_style = "detailed", "detailed"
        made.append("Switched to detailed answers (you asked for depth several times).")

    # Teaching style
    if reached("wants_examples", 3) and profile.teaching_style != "examples":
        profile.teaching_style = "examples"
        ls = dict(profile.learning_style or {}); ls["examples"] = True; profile.learning_style = ls
        made.append("Teaching with examples first (your pattern).")
    elif reached("wants_theory", 3) and profile.teaching_style != "theory":
        profile.teaching_style = "theory"
        made.append("Leading with theory/concepts (your pattern).")
    elif reached("wants_hands_on", 2) and profile.teaching_style != "hands-on":
        profile.teaching_style = "hands-on"
        made.append("Hands-on walkthrough style enabled (your pattern).")

    # Format
    if reached("wants_bullets", 2) and profile.communication_format != "bullets":
        profile.communication_format = "bullets"
        made.append("Using bullet lists by default (your pattern).")
    elif reached("wants_code", 3) and profile.communication_format != "code-first":
        profile.communication_format = "code-first"
        made.append("Code-first answers enabled (your pattern).")
    elif reached("wants_hands_on", 3) and profile.communication_format != "tutorial":
        profile.communication_format = "tutorial"
        made.append("Tutorial format enabled (your pattern).")

    # Expertise — needs 3 signals to move (avoid jumpiness)
    if reached("advanced_signal", 3) and profile.expertise_level != "advanced":
        profile.expertise_level = "advanced"
        made.append("Leveled you up to advanced (technical depth detected).")
    elif reached("beginner_signal", 2) and profile.expertise_level != "beginner":
        profile.expertise_level = "beginner"
        made.append("Simplified to beginner-friendly mode (your request).")

    # Tone — lighter touch, single observation can set casual/formal request
    if signals.get("formal") and profile.tone != "formal":
        profile.tone = "formal"
        made.append("Formal tone enabled.")
    elif signals.get("casual") and profile.tone == "formal":
        profile.tone = "casual"
        made.append("Casual tone enabled.")

    profile.behaviour_signals = bs
    return made

def behaviour_memory_candidate(text: str, signals: dict) -> dict | None:
    """Persist only stable, meaningful patterns — not every message."""
    strong = [k for k in ("wants_brief", "wants_detail", "wants_examples",
                          "wants_hands_on", "wants_code", "beginner_signal") if signals.get(k)]
    if not strong:
        return None
    return {"kind": "behaviour",
            "content": f"User pattern [{', '.join(strong)}]: {text.strip()[:200]}",
            "memory_type": "behaviour", "importance": "medium"}
