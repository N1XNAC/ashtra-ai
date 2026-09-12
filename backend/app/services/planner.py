"""Phase 4 — Planner (AI_AGENT_SPECIFICATION.txt).

Pipeline: Request → Planner → Tool Selection → Execution → Response.

Phase 4 planner is an intent router (deterministic, explainable, no key).
Phase 5+/LLM path: when OPENAI_* set, model may call tools via system-prompt
convention — router still runs first as guardrail.
A message can trigger multiple tools (e.g. calculate + note the result).
"""
import re

CALC_WORDS = ("calculate", "compute", "solve", "what is", "what's", "evaluate")
CALC_EXPR = re.compile(r"([-+/*//%**()\d\s.,a-z_]+)", re.I)

def _calc_arg(text: str) -> str | None:
    t = text.strip()
    m = re.search(r"(?:calculate|compute|solve|evaluate)\s+(.+)", t, re.I)
    if m:
        return m.group(1).strip(" =?")
    m = re.search(r"^what is\s+([\d][\d\s+\-*/().%^!]*)\??$", t, re.I)
    if m:
        return m.group(1).strip()
    return None

def plan(text: str) -> list[dict]:
    """Return ordered tool plan: [{tool, args, reason}]. Empty = plain chat."""
    t = text.lower()
    steps: list[dict] = []

    expr = _calc_arg(text)
    if expr and re.search(r"[\d+\-*/()%^]", expr):
        steps.append({"tool": "calculator", "args": {"expression": expr},
                      "reason": "math expression detected"})

    if re.search(r"\b(take a note|note (this|down|it)|save (this |as )?(a )?note)\b", t):
        steps.append({"tool": "notes_create", "args": {"text": text},
                      "reason": "note request detected"})
    elif re.search(r"\b(my notes|list (my )?notes|show (my )?notes)\b", t):
        steps.append({"tool": "notes_list", "args": {}, "reason": "notes listing requested"})

    if re.search(r"\b(remind me|add (an? )?(event|reminder)|schedule)\b", t):
        steps.append({"tool": "calendar_create", "args": {"text": text},
                      "reason": "reminder/event request detected"})
    elif re.search(r"\b(my events|my reminders|my calendar|upcoming|what's due|what is due|on my plate|my plate|my tasks|my todos)\b", t):
        steps.append({"tool": "calendar_list", "args": {}, "reason": "calendar listing requested"})

    if re.search(r"\b(search( the web)?( for)?|look up|google)\b", t):
        q = re.sub(r"\b(please )?(search( the web)?( for)?|look up|google)\b:? ?", "", text, flags=re.I).strip()
        if q:
            steps.append({"tool": "web_search", "args": {"query": q}, "reason": "search requested"})

    m = re.search(r"\b(read|show|open) (the )?file\s+([\w.\-]+)", t)
    if m:
        steps.append({"tool": "file_read", "args": {"name": m.group(3)}, "reason": "file read requested"})
    elif re.search(r"\b(list (my |uploaded )?files|my files|my uploads)\b", t):
        steps.append({"tool": "file_list", "args": {}, "reason": "file listing requested"})
    m = re.search(r"\banalyze (the |my |this |uploaded )?(file|code)\s+([\w.\-]+)", t)
    if m:
        steps.append({"tool": "code_analyze", "args": {"name": m.group(3)}, "reason": "code analysis requested"})

    # --- Phase 6: goals ---
    m = re.search(r"\b(?:add(?: a)? goal|new goal|track(?: a)? goal|set a goal)[:]?\s*(.+)", t)
    if m:
        steps.append({"tool": "goal_create", "args": {"title": m.group(1).strip()},
                      "reason": "goal creation requested"})
    elif re.search(r"\b(my goals|list (my )?goals|show (my )?goals|goal dashboard)\b", t):
        steps.append({"tool": "goal_list", "args": {}, "reason": "goals listing requested"})
    m = re.search(r"\bmark (the )?goal\s+(.+?)\s+(done|complete|finished)\b", t)
    if m:
        steps.append({"tool": "goal_done", "args": {"title": m.group(2).strip()},
                      "reason": "goal completion requested"})
    m = re.search(r"\bset (the )?goal\s+(.+?)\s+to\s+(\d{1,3})\s*%?", t)
    if m:
        steps.append({"tool": "goal_progress", "args": {"title": m.group(2).strip(), "progress": int(m.group(3))},
                      "reason": "goal progress update requested"})

    return steps

def describe(steps: list[dict]) -> str:
    return "; ".join(f"{s['tool']}({', '.join(f'{k}={v}' for k, v in s['args'].items())})" for s in steps)
