"""Build & Run — prompt engine.

Turns the user's raw words (plus intake keypoints, memory and a style card)
into a structured BUILD BRIEF the coder model follows: palette, fonts, tone,
an ordered section plan with layouts, and an interaction plan. One lightweight
LLM call does the creative enrichment; this module owns the non-negotiable
contracts (deterministic, always merged) and the neutral-premium fallback, so
a failed or truncated call can never block a build.

Layering: the engine takes an injected `llm(messages) -> str` callable (the
router's provider chain) — services never import routers.
"""
import json
import re

ENGINE_SYS = """You are the prompt engine of a website builder.
Input: the user's raw request, intake keypoints (purpose/audience/sections/
style), optionally what we remember about them, and a design reference.
Output ONLY minified JSON (no fences, no commentary) — a BUILD BRIEF:
{"palette":{"primary":"#hex","accent":"#hex","bg":"#hex","surface":"#hex","text":"#hex","muted":"#hex"},
 "fonts":{"heading":"family + weight","body":"family + weight"},
 "tone":"one short sentence",
 "sections":[{"id":"hero","layout":"structure, e.g. full-bleed headline + CTA, 2-col desktop","goal":"what this section convinces of","cta":"the one action here"}, ...3-7 sections in order, ending with footer],
 "interactions":["nav smooth-scrolls to sections", "…what every button/link/form does"],
 "imagery":"a concise image search query for this site's photos"}
Rules: colors are real hex, one accent for CTAs; the section plan covers the
user's must-haves in a logical order; layout says columns/breakpoint behavior;
imagery matches the site's subject (not generic stock)."""

DESIGN_CONTRACT = """DESIGN CONTRACT (non-negotiable — applies even where the brief is silent):
- Type: fluid sizes with clamp() (h1 ≈ clamp(2rem,5vw,3.5rem)); max 2 font families; body 1rem-1.125rem / line-height 1.5; headings line-height 1.1-1.25.
- Spacing: 8px grid; section vertical padding clamp(64px,10vw,120px); content container max-width 1200px with 24px gutters; ONE border-radius scale (8px cards, 999px pills) — never mix radii.
- Palette: exactly the brief's colors; text always clearly readable on its background (AA); no gray-on-gray; the accent appears only on CTAs/highlights.
- Buttons: min-height 44px, one shared style, visible hover + active + focus-visible states.
- Breakpoints: mobile-first; 768 / 1024 / 1280; every grid collapses to a single column on mobile."""

FUNCTIONAL_CONTRACT = """FUNCTIONALITY CONTRACT (non-negotiable):
- Every href="#x" targets an existing section id="x" — never href="#" or an empty href.
- Every button does real work: scroll, toggle, open/close, submit, or navigate. No decorative-only buttons.
- Forms: proper input types + required; submit preventDefault + inline success message (front-end only).
- Anything referenced from HTML attributes (onclick etc.) must be defined in the inline script; nav links smooth-scroll.
- All content fully visible without JavaScript; JS only enhances already-visible content."""

_NEUTRAL = {
    "palette": {"primary": "#2563eb", "accent": "#0f172a", "bg": "#ffffff",
                "surface": "#f6f7f9", "text": "#111418", "muted": "#5b6470"},
    "fonts": {"heading": "Inter/system-ui, 700-800", "body": "Inter/system-ui, 400-500"},
    "tone": "modern, confident and clean — premium but approachable",
    "sections": [
        {"id": "hero", "layout": "headline + subline + primary CTA, supporting visual; stacks on mobile",
         "goal": "state what this site is in one glance", "cta": "main conversion action"},
        {"id": "features", "layout": "3-col grid of cards, 1-col mobile", "goal": "the core benefits or offerings", "cta": "learn more"},
        {"id": "story", "layout": "2-col text + image, reversed mobile", "goal": "build trust with the story/proof", "cta": "none"},
        {"id": "contact", "layout": "centered form + details", "goal": "capture the lead", "cta": "submit"},
    ],
    "interactions": ["nav smooth-scrolls to sections", "CTA scrolls to contact",
                     "form validates and shows an inline success state"],
    "imagery": "",
}

_JSON_RE = re.compile(r"\{[\s\S]*\}")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _fallback(prompt: str) -> dict:
    import copy
    brief = copy.deepcopy(_NEUTRAL)
    q = (prompt or "").strip()
    if q:
        brief["imagery"] = q[:80]
    return brief


def _validate(data: dict) -> dict | None:
    """Accept only a structurally sane brief; fill nothing here (compose does)."""
    if not isinstance(data, dict):
        return None
    pal = data.get("palette")
    if not isinstance(pal, dict) or not pal.get("primary"):
        return None
    for k, v in pal.items():
        if isinstance(v, str) and v.strip() and not _HEX_RE.match(v.strip()):
            return None  # one bad hex poisons the whole page
    secs = data.get("sections")
    if not isinstance(secs, list) or not (2 <= len(secs) <= 8):
        return None
    if not all(isinstance(s, dict) and s.get("id") and s.get("layout") for s in secs):
        return None
    return data


def _fill(brief: dict) -> dict:
    """Merge model output over the neutral default — brief is never partial."""
    out = _fallback(str(brief.get("tone") or ""))
    out.update({k: v for k, v in brief.items() if v})
    pal = dict(out.get("palette") or {})
    for k, v in _NEUTRAL["palette"].items():
        if not _HEX_RE.match(str(pal.get(k, ""))):
            pal[k] = v
    out["palette"] = pal
    if not isinstance(out.get("sections"), list) or len(out["sections"]) < 2:
        out["sections"] = _NEUTRAL["sections"]
    if not isinstance(out.get("interactions"), list) or not out["interactions"]:
        out["interactions"] = _NEUTRAL["interactions"]
    fonts = out.get("fonts") if isinstance(out.get("fonts"), dict) else {}
    out["fonts"] = {"heading": fonts.get("heading") or _NEUTRAL["fonts"]["heading"],
                    "body": fonts.get("body") or _NEUTRAL["fonts"]["body"]}
    return out


async def compose(prompt: str, spec: str = "", mem_ctx: str = "",
                  style_block: str = "", llm=None) -> dict:
    """One lightweight call → validated brief. Never raises, never blocks:
    model failure/truncation falls back to the neutral-premium brief."""
    if llm is None:
        return _fill(_fallback(prompt))
    parts = [f"User request: {prompt}"]
    if spec:
        parts.append(f"Intake keypoints: {spec}")
    if mem_ctx:
        parts.append(f"What we remember: {mem_ctx}")
    if style_block:
        parts.append(f"Design reference to honor: {style_block[:600]}")
    messages = [{"role": "system", "content": ENGINE_SYS},
                {"role": "user", "content": "\n\n".join(parts)}]
    for _attempt in range(2):
        try:
            out = await llm(messages)
        except Exception:
            break
        m = _JSON_RE.search(out or "")
        if not m:
            continue
        try:
            data = json.loads(m.group(0))
        except Exception:
            continue
        checked = _validate(data)
        if checked:
            return _fill(checked)
    return _fill(_fallback(prompt))


def render(brief: dict) -> str:
    """Structured BUILD BRIEF block for the coder — includes the contracts."""
    pal = brief.get("palette") or {}
    fonts = brief.get("fonts") or {}
    lines = ["BUILD BRIEF (follow exactly):"]
    lines.append("Palette: " + ", ".join(f"{k} {v}" for k, v in pal.items()))
    lines.append(f"Fonts: heading {fonts.get('heading', '')}; body {fonts.get('body', '')}")
    if brief.get("tone"):
        lines.append(f"Tone: {brief['tone']}")
    lines.append("Section plan (in order):")
    for s in brief.get("sections") or []:
        row = f'  - id="{s.get("id", "section")}" — {s.get("layout", "")}'
        if s.get("goal"):
            row += f' (goal: {s["goal"]})'
        if s.get("cta"):
            row += f' [CTA: {s["cta"]}]'
        lines.append(row)
    inter = brief.get("interactions") or []
    if inter:
        lines.append("Interactions: " + "; ".join(str(i) for i in inter))
    lines.append("")
    lines.append(DESIGN_CONTRACT)
    lines.append("")
    lines.append(FUNCTIONAL_CONTRACT)
    return "\n".join(lines)
