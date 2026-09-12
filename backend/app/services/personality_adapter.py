"""Phase 3 — Personality adapter (Prd §3).

Turns the user model into concrete response directives.
Used for: system prompt (real LLM) + echo fallback + UI indicator.
"""

def directives(profile) -> str:
    if not profile:
        return ""
    depth_map = {
        "concise": "Be concise: short answers, no fluff.",
        "detailed": "Be thorough: full explanations with nuance.",
        "balanced": "Balance brevity and detail.",
    }
    format_map = {
        "bullets": "Default to bullet lists.",
        "tutorial": "Default to step-by-step tutorial format.",
        "code-first": "Lead with code, then brief explanation.",
        "chat": "Natural conversational flow.",
    }
    teach_map = {
        "examples": "Teach with practical examples first.",
        "theory": "Teach concepts/theory first, then examples.",
        "hands-on": "Teach hands-on: guide the user to do it.",
    }
    exp_map = {
        "beginner": "Assume beginner: avoid jargon, explain terms.",
        "intermediate": "Assume intermediate: practical depth, some jargon ok.",
        "advanced": "Assume advanced: dense, technical, trade-offs included.",
    }
    return (
        f"[Adaptation] depth={getattr(profile, 'explanation_depth', 'balanced')}: "
        f"{depth_map.get(getattr(profile, 'explanation_depth', 'balanced'), '')} "
        f"Tone={getattr(profile, 'tone', 'casual')}. "
        f"Format={getattr(profile, 'communication_format', 'chat')}: "
        f"{format_map.get(getattr(profile, 'communication_format', 'chat'), '')} "
        f"Teaching={getattr(profile, 'teaching_style', 'examples')}: "
        f"{teach_map.get(getattr(profile, 'teaching_style', 'examples'), '')} "
        f"Expertise={getattr(profile, 'expertise_level', 'intermediate')}: "
        f"{exp_map.get(getattr(profile, 'expertise_level', 'intermediate'), '')}"
    )

def summary_for_api(profile) -> dict:
    bs = getattr(profile, "behaviour_signals", None) or {}
    total = sum(int(v) for v in bs.values() if isinstance(v, int))
    return {
        "explanation_depth": getattr(profile, "explanation_depth", "balanced"),
        "tone": getattr(profile, "tone", "casual"),
        "communication_format": getattr(profile, "communication_format", "chat"),
        "teaching_style": getattr(profile, "teaching_style", "examples"),
        "expertise_level": getattr(profile, "expertise_level", "intermediate"),
        "observations": total,
        "confidence": "high" if total >= 10 else "medium" if total >= 4 else "low",
    }
