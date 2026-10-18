"""Build & Run v1 — AI website builder (websites only for now).

POST /build/website → LLM writes a single-file site from a template.
GET  /build/zip/{job_id} → download it as website.zip (index.html).
"""
import asyncio
import io
import json
import logging
import os
import re
import time
import uuid
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..database import get_db
from ..services import memory_engine, prompt_engine, template_bank, web_images
from ..services import byok as byok_svc
from ..services import ai_core

router = APIRouter(prefix="/build", tags=["build"])
_log = logging.getLogger("ashtra.build")

_JOBS: dict[str, dict] = {}  # job_id → {name, html} (single worker, ephemeral)

TEMPLATES = {
    "landing": "a modern landing page with hero, features grid, and footer",
    "portfolio": "a personal portfolio with hero, projects grid, and contact section",
    "blog": "a minimal blog layout with header, post cards, and footer",
    "dashboard": "a clean admin dashboard with sidebar and stat cards",
    "restaurant": "a restaurant site with hero, menu section, and reservation form (front-end only)",
}

_SYS = ("You are a senior front-end developer. Output ONLY a complete single HTML file "
        "(inline <style> and <script>; external URLs only for images and real CDN "
        "<script src> tags — never left as comments). "
        "No markdown fences, no explanations — just the HTML. "
        "Follow the BUILD BRIEF exactly — palette, fonts, section plan, interactions — "
        "including its DESIGN CONTRACT and FUNCTIONALITY CONTRACT. "
        "Hard rules: every section fully implemented; never leave placeholder comments, "
        "TODOs, or empty script blocks — if a chart, animation, or function is required, "
        "write the real working code; visually polished. "
        "MOBILE-FIRST RULES (critical): design for a 390px-wide phone FIRST, then scale "
        "up — (a) <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> "
        "in <head>; (b) base CSS targets phones; enhance with min-width media queries at "
        "768px and 1024px; (c) navigation (top bar links / sidebar) MUST collapse to a "
        "burger menu below 768px; (d) never set fixed pixel widths over 320px on "
        "containers — use max-width + fluid padding; images: max-width:100%; "
        "height:auto; box-sizing:border-box everywhere; (e) all buttons and links need "
        ">=44px tap targets; (f) no horizontal overflow at 390px — nothing may stick out "
        "sideways (the whole page must fit the phone screen). "
        "BLANK-PAGE RULES (critical): (1) All main content must be fully visible WITHOUT "
        "JavaScript — never start sections at opacity:0 or off-screen transforms waiting "
        "for a script; JS may only enhance already-visible content. (2) Set an explicit "
        "background-color on html, body AND every section so nothing can render as a "
        "blank black or white void. (3) Images: when IMAGE URLS are given, use only those "
        "for <img> and background-image; otherwise use ONLY https://picsum.photos/... or "
        "https://placehold.co/... or CSS gradients / inline SVG data-URIs — NEVER "
        "via.placeholder.com or other unreliable hosts. (4) Check your own HTML: if an "
        "element could be invisible on load, fix it before answering. "
        "(5) Every external link (http/https) must have target=\"_blank\" "
        "rel=\"noopener\" so previews never navigate inside the frame; "
        "in-page anchors (#section) keep normal behavior.")

_PLACEHOLDER_RE = re.compile(
    r"(?://|/\*|<!--)[^\n]*(TODO|FIXME|write (the )?(code|chart|implementation)|"
    r"implementation (goes|here)|add (your|the) (code|content|chart) (here|below)|"
    r"placeholder (code|script|content)|inside this script block)",
    re.IGNORECASE)

# onclick="fn(" / onsubmit="fn(" → the function name that must exist in script.
_ONATTR_RE = re.compile(r"on(?:click|submit|change|input|mouseover)=\"\s*"
                        r"([A-Za-z_$][\w$]*)\s*\(")
_JS_KEYWORDS = frozenset({"this", "event", "window", "document", "alert",
                          "confirm", "prompt", "console", "return", "if"})


def _auto_fix(html: str) -> str:
    """Deterministic fixes that need no model: dead hrefs become a working
    scroll-to-top anchor (id=\"top\" injected once when missing)."""
    if not html:
        return html
    if re.search(r'href="#"', html) or re.search(r'href=""', html):
        html = re.sub(r'href="#"', 'href="#top"', html)
        html = re.sub(r'href=""', 'href="#top"', html)
        if 'id="top"' not in html and "id='top'" not in html:
            html = re.sub(r"(<body[^>]*>)", r'\1<span id="top"></span>',
                          html, count=1, flags=re.I)
    return html


def _lint_html(html: str, responsive: bool = True) -> list[str]:
    """Deterministic QA of a built page — no model cost. Returns the exact
    violations to hand the repair pass; [] means ship it.

    responsive=False skips the @media check only (immersive one-screen
    templates can legitimately have no breakpoints); viewport-meta and
    body min-width bugs are unambiguous and always checked."""
    text = html or ""
    low = text.lower()
    if "<html" not in low:
        return ["output is not an HTML document (no <html> root)"]
    problems: list[str] = []
    if "</html>" not in low:
        problems.append("document truncated — missing </html>")
    if _PLACEHOLDER_RE.search(text):
        problems.append("placeholder/TODO comments left in the code")
    if "via.placeholder.com" in low:
        problems.append("dead image host via.placeholder.com")
    # Fragment links must land on real sections; no dead hrefs.
    ids = set(re.findall(r'id="([^"]+)"', text))
    missing = sorted({h for h in re.findall(r'href="#([^"]+)"', text)
                      if h and h not in ids})
    if missing:
        problems.append("links to missing sections: " + ", ".join(missing[:6]))
    if re.search(r'href="#"', text) or re.search(r'href=""', text):
        problems.append('dead link (href="#" or empty href)')
    # Inline handlers must call functions the page actually defines.
    called = {m for m in _ONATTR_RE.findall(text)} - _JS_KEYWORDS
    if called:
        defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", text))
        defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
                                  r"(?:async\s+)?(?:function|\()", text))
        undef = sorted(called - defined)
        if undef:
            problems.append("calls undefined function(s): " + ", ".join(undef[:6]))
    # Plain buttons with no type/handler, and nothing in the page wires button
    # clicks at all → decorative-only buttons (the contract bans those).
    btn_tags = re.findall(r"<button\b[^>]*>", text, re.I)
    plain = [b for b in btn_tags
             if "onclick=" not in b.lower() and "type=" not in b.lower()]
    wired = (bool(re.search(r"addEventListener\(\s*['\"]click|\.onclick\s*=", text, re.I))
             or any("onclick=" in b.lower() for b in btn_tags))
    if plain and not wired:
        problems.append(f"{len(plain)} button(s) do nothing — no handler, no type")
    # Forms need a real submit path: onsubmit, a submit listener, or an action.
    for fm in re.finditer(r"<form\b[^>]*>", text, re.I):
        tag = fm.group(0).lower()
        if "onsubmit=" not in tag and "action=" not in tag \
                and not re.search(r"addEventListener\(\s*['\"]submit", text, re.I):
            problems.append("form has no submit handling")
            break
    # Mobile-first sanity — regex-only, zero model cost. Clean pages pay
    # nothing; violations feed the existing repair loop.
    if not re.search(r'name=["\']viewport["\']', low):
        problems.append('missing <meta name="viewport"> tag (mobile layout broken)')
    if responsive and "@media" not in low:
        problems.append("no responsive breakpoints (@media) — page will not adapt to phones")
    mw = re.search(r"\bbody\b[^{}]{0,80}\{[^}]*?min-width:\s*(\d{3,4})px", text, re.I)
    if mw and int(mw.group(1)) >= 480:
        problems.append(f"body min-width {mw.group(1)}px forces horizontal scroll on phones")
    return problems


def _pick_template(prompt: str) -> str:
    t = prompt.lower()
    for name in TEMPLATES:
        if name in t:
            return name
    return "landing"


# Design notes distilled from the fixed template catalog (/templates on disk).
# The intake picks one; the builder matches its mood/structure, not its content.
REFERENCES: dict[str, str] = {
    "meridian": ("premium fintech/product hero — single-screen 100dvh layout, near-black "
                 "background, glass panels, large display serif headline with tight tracking, "
                 "small mono labels, video hero with poster, pill-shaped CTAs, generous "
                 "whitespace, understated gold/white accents"),
    "orbit": ("immersive one-screen space product page — dark starfield, centered hero "
              "with oversized type, minimal top nav + burger, floating glass cards, "
              "cyan/violet glow accents, everything within the viewport (no long scroll)"),
    "jack": ("3D creator portfolio — bold condensed display font (Kanit), black background, "
             "marquee strips of looping media, sticky stacked project cards that cover each "
             "other while scrolling, magnetic hover buttons, big footer contact"),
    "vesper": ("dark AI-infrastructure SaaS — typewriter headline, glassmorphic nav and "
               "cards, subtle grid/blueprint background, gradient borders, stat/logo strip, "
               "feature grid with icons, video background hero, electric blue accents"),
    "evolve": ("modular AI platform landing — retro-pixel display font, near-black bg, "
               "glass card panels with thin borders, animated stat counters, logo trust row, "
               "modular feature blocks, green/teal terminal accents"),
    "planet": ("immersive space-portal experience — full-viewport scenes, preloader with "
               "percentage, custom cursor, planet travel transitions with video portal, "
               "side planet list with active marker, floating facts card, minimal chrome"),
    "wildlife": ("dark cinematic photography archive — splash intro film, muted earth tones "
                 "on black, serif captions, sphere/grid gallery exploration, film-grain "
                 "overlay, hidden-until-scroll grid button, editorial spacing"),
    "asme": ("liquid-glass editorial landing — frosted glass pills and cards floating over "
             "video sections, soft light-on-dark gradients, large modern sans headlines, "
             "philosophy/services editorial blocks, newsletter pill form, gentle motion"),
}


class BuildRequest(BaseModel):
    user_id: str = "master-001"
    prompt: str
    template: str = ""
    # Web Market template id (template-N). When set, the exact template HTML
    # is used as the base 1:1 and the LLM only applies the user's changes.
    template_id: str = ""


# --- Web Market templates (same catalog the /market router serves) ---
_DEFAULT_TPL_DIR = Path(__file__).resolve().parents[2] / "templates"
TPL_DIR = Path(os.environ.get("TEMPLATES_DIR") or _DEFAULT_TPL_DIR)
_TPL_ID_RE = re.compile(r"^template-\d+$")
_TPL_HIDDEN = {"template-3", "template-8", "template-9"}  # retired/duplicate


def _market_template_html(tid: str) -> tuple[str, str] | None:
    """Load the exact on-disk template → (html, display name) or None if unknown."""
    if not tid or not _TPL_ID_RE.match(tid) or tid in _TPL_HIDDEN:
        return None
    base = (TPL_DIR / tid).resolve()
    idx = base / "index.html"
    if not base.is_dir() or not idx.is_file():
        return None
    name = tid
    meta = base / "metadata.json"
    if meta.is_file():
        try:
            name = json.loads(meta.read_text(encoding="utf-8")).get("name") or tid
        except Exception:
            pass
    return idx.read_text(encoding="utf-8", errors="ignore"), name


_UNDERSTAND_SYS = """You are the intake designer for an AI website builder.
The user sends one message. Decide if they are DEMANDING a website/app build.

NOT a website demand (want=false): ONLY clearly non-website messages —
greetings ("hi", "hello"), random names/words, general questions, chat about
you/features, weather/status asks. Reply with a friendly one-or-two sentence
message gently asking them to describe the website they want.

IS a website demand (want=true): ANY message with site/app intent, including
vague ones like "make me a site", "portfolio please", "website for my shop" —
never ask clarifying questions when intent is present; instead extract the
KEYPOINTS (purpose, audience, must-have sections, style/mood keywords) from
the demand and write the spec yourself with sensible defaults.

Pick the closest design reference for the build:
meridian (premium fintech hero, dark glass, display serif, video hero),
orbit (immersive one-screen space product page),
jack (3D creator portfolio, bold kanit type, marquee, stacked cards),
vesper (dark AI-infrastructure SaaS, typewriter hero, glassy),
evolve (modular AI platform, pixel display font, stat counters),
planet (immersive space-portal experience, preloader, custom cursor),
wildlife (dark photography archive, cinematic splash, grid gallery),
asme (liquid-glass editorial landing, video sections),
none (no reference fits).

Output ONLY minified JSON, no fences:
{"want":true,"template":"landing|portfolio|blog|dashboard|restaurant|other",
"reference":"meridian|orbit|jack|vesper|evolve|planet|wildlife|asme|none",
"spec":"<detailed build prompt in English built from the user's keypoints:
purpose, audience, exact sections with content themes, style/color mood,
must-have features — written as instructions to a developer. The spec MUST
end with a RESPONSIVE section stating: which nav pattern collapses on
mobile and how, grid column counts per breakpoint (390px / 768px / 1280px),
touch-target and spacing notes>"}
or
{"want":false,"reply":"<your friendly message>"}"""

_PARSE_RE = re.compile(r"\{.*\}", re.DOTALL)


def _json_out(text: str) -> dict:
    m = _PARSE_RE.search(text or "")
    if not m:
        return {}
    try:
        import json
        return json.loads(m.group(0))
    except Exception:
        return {}


async def _understand(prompt: str, mem_ctx: str = "", own_creds=None,
                      extras: str = "") -> dict:
    """Stage 1 — does the user actually want a site? If so, write the spec."""
    user_msg = prompt
    if mem_ctx:
        user_msg += (f"\n\nWHAT AZX ALREADY REMEMBERS ABOUT THIS USER "
                     f"(use it — business name, type, location, preferences):\n{mem_ctx}")
    data: dict = {}
    for _attempt in range(3):  # JSON sometimes gets truncated mid-spec — retry
        out = await _llm_html([
            {"role": "system", "content": _UNDERSTAND_SYS + extras},
            {"role": "user", "content": user_msg},
        ], "openai/gpt-oss-20b", 1200, "low", own_creds=own_creds)
        data = _json_out(out)
        if data:
            break
    if not data:
        # last resort: build anyway — spec falls back to the raw prompt
        return {"want": True, "template": "", "reference": "none", "spec": prompt}
    if not data.get("want"):
        return {"want": False,
                "reply": data.get("reply") or
                "Tell me what website you'd like built — what's it for, who's it for, and any style you like."}
    tpl = str(data.get("template", "")).lower()
    ref = str(data.get("reference", "")).lower().strip()
    return {"want": True,
            "template": tpl if tpl in TEMPLATES else "",
            "reference": ref if ref in REFERENCES else "none",
            "spec": str(data.get("spec") or prompt).strip()[:3000]}


class _PayloadTooLarge(Exception):
    """Provider rejected the request body (HTTP 413) — use the big-context chain."""


# Groq's free org caps ~8k TPM (input + output), so anything past ~10KB of
# input (≈3k tokens) can never fit — skip straight to the big-context provider.
_GROQ_TPM_BUDGET = 7500  # estimated input tokens + max_tokens must fit here


# Big-context chain (OpenRouter free tier): full template edits are 50KB+.
_EDIT_FALLBACK_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-26b-a4b-it:free",
    "poolside/laguna-xs-2.1:free",
]


async def _groq_html(messages: list[dict], model: str, max_tokens: int, effort: str,
                     patient: bool = True, creds: tuple[str, str] | None = None) -> str:
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": 0.5,
               "reasoning_effort": effort}
    base_url, api_key = creds if creds else (settings.groq_base_url, settings.groq_api_key)
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=180) as c:
        for attempt in range(4):
            try:
                r = await c.post(url, headers=headers, json=payload)
                if r.status_code == 413:
                    raise _PayloadTooLarge(f"groq rejected payload ({len(str(payload))} chars)")
                if r.status_code == 429:
                    if not patient:
                        raise HTTPException(status_code=429, detail="groq busy")
                    try:
                        wait = int(r.headers.get("retry-after", "0") or 0)
                    except ValueError:
                        wait = 0
                    await asyncio.sleep(max(wait, 8 * (attempt + 1)))
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""
            except httpx.TransportError:
                await asyncio.sleep(5)
    raise HTTPException(status_code=429,
                        detail="Model is rate-limited right now — try again in a few seconds.")


async def _openrouter_html(messages: list[dict], max_tokens: int,
                           require_html: bool = False,
                           creds: tuple[str, str] | None = None) -> str:
    if creds:
        base, key = creds[0].rstrip("/"), creds[1]
    else:
        base = (settings.vision_base_url or "https://openrouter.ai/api/v1").rstrip("/")
        key = settings.vision_api_key
    if not key:
        raise HTTPException(status_code=502,
                            detail="Big-site model not configured (VISION_API_KEY missing).")
    deadline = time.monotonic() + 240
    errors: list[str] = []
    payload = {"messages": messages, "max_tokens": max_tokens, "temperature": 0.5}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as c:
        for m in _EDIT_FALLBACK_MODELS:
            left = deadline - time.monotonic()
            if left <= 5:
                break
            for retry in range(2):
                try:
                    r = await c.post(f"{base}/chat/completions", headers=headers,
                                     json={**payload, "model": m}, timeout=min(170, left))
                    break
                except httpx.TimeoutException:
                    errors.append(f"{m}→timeout")
                    r = None
                    break
                except httpx.TransportError as e:
                    errors.append(f"{m}→{type(e).__name__}")
                    r = None
                    break
                except Exception as e:
                    errors.append(f"{m}→{type(e).__name__}")
                    r = None
                    break
            if r is None:
                continue
            if r.status_code == 429 and retry == 0:
                # free-tier quota — one patient retry before moving on
                errors.append(f"{m}→HTTP 429")
                await asyncio.sleep(10)
                continue
            if r.status_code >= 400:
                errors.append(f"{m}→HTTP {r.status_code}")
                continue
            try:
                content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            except Exception:
                errors.append(f"{m}→badjson")
                continue
            if not content:
                errors.append(f"{m}→empty")
                continue
            if require_html and "</html>" not in content.lower():
                # free models silently truncate big files — try the next one
                errors.append(f"{m}→incomplete({len(content)}c)")
                continue
            _log.info("openrouter %s ok (%d chars)", m, len(content))
            return content
    raise HTTPException(status_code=502,
                        detail="Site model unavailable (" + ", ".join(errors[:4])
                               + ") — try again shortly.")


async def _llm_html(messages: list[dict], model: str, max_tokens: int, effort: str = "medium",
                    require_html: bool = False,
                    own_creds: list[tuple[str, str, str]] | None = None) -> str:
    """HTML LLM call with provider fallback.

    BYOK: the user's own provider keys go first (their model + quota). Then
    small prompts go to Groq (fast). Full template edits carry 50KB+ of HTML,
    which Groq rejects with 413 (free org TPM cap) — those go to the
    OpenRouter big-context chain instead of exploding into a 500.
    """
    # BYOK: user's own keys first — try each with its chosen model, fail-fast
    # (no patient retry) into the server chain on any error.
    for base_url, api_key, omodel in (own_creds or []):
        try:
            return await _groq_html(messages, omodel, max_tokens, effort,
                                    patient=False, creds=(base_url, api_key))
        except (_PayloadTooLarge, HTTPException):
            continue
    input_chars = sum(len(m.get("content") or "") for m in messages)
    est = input_chars / 3.4 + max_tokens
    # Groq free org ≈8k TPM shared with chat — only route when input+output fits
    # the budget. Exception: from-scratch generates (small input, big output)
    # ALWAYS try Groq first — it's 10-20x faster than the OpenRouter free
    # chain — and fail fast (no patient retry) into the chain on 429. Huge
    # edit payloads (>15KB input) still skip Groq: the free org 413s them.
    if est <= _GROQ_TPM_BUDGET or (max_tokens > 3000 and input_chars < 15000):
        try:
            return await _groq_html(messages, model, max_tokens, effort,
                                    patient=est <= _GROQ_TPM_BUDGET)
        except _PayloadTooLarge:
            pass  # bigger than it looked — big-context chain
        except HTTPException:
            pass  # rate-limited/unavailable — the chain may still work
    return await _openrouter_html(messages, max_tokens, require_html=require_html)


def _clean(html: str) -> str:
    return re.sub(r"^```html\s*|\s*```$", "", html.strip())


_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
                "X-Accel-Buffering": "no"}


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False, default=str) + "\n\n"


_DIFF_SYS = (
    "You are a senior front-end developer editing a single-file HTML site. "
    "Apply ONLY the requested change. Do NOT output the whole file — output "
    "ONLY search/replace blocks, one per change, in this exact format:\n"
    "<<<<<<< SEARCH\n"
    "(exact lines copied verbatim from the current file, whitespace included)\n"
    "=======\n"
    "(the replacement lines)\n"
    ">>>>>>> REPLACE\n"
    "Rules: SEARCH must match the file exactly or the edit is discarded; keep "
    "each block minimal but include enough surrounding lines to make it unique "
    "in the file; no markdown fences, no explanations; keep the layout "
    "mobile-first responsive — don't drop viewport meta, media queries or the "
    "burger nav; any new layout must stay fluid at 390px. "
    "If no change is needed, output NOTHING."
)


def _apply_diff_blocks(html: str, out: str) -> tuple[str | None, str]:
    """Apply SEARCH/REPLACE blocks. Returns (patched_html_or_None, reason).

    Pass 1 matches verbatim; pass 2 matches whitespace-insensitively so minor
    indentation drift doesn't force a slow full rewrite.
    """
    parts = out.split("<<<<<<< SEARCH")[1:]
    if not parts:
        return None, "no blocks"
    raw_blocks: list[tuple[str, str]] = []
    for p in parts:
        if "=======" not in p or ">>>>>>> REPLACE" not in p:
            return None, "malformed block"
        search, rest = p.split("=======", 1)
        repl, _ = rest.split(">>>>>>> REPLACE", 1)
        search = search.strip().strip("`").strip()
        repl = repl.strip().strip("`").strip()
        if not search:
            return None, "empty SEARCH"
        raw_blocks.append((search, repl))
    file_lines = html.split("\n")
    norm_file = [l.strip() for l in file_lines]
    for search, repl in raw_blocks:
        if html.count(search) == 1:
            html = html.replace(search, repl, 1)
            continue
        ns = [l.strip() for l in search.split("\n")]
        n = len(ns)
        hits = [i for i in range(len(norm_file) - n + 1)
                if norm_file[i:i + n] == ns]
        if len(hits) != 1:
            return None, f"block matches {len(hits)}x"
        orig_block = "\n".join(file_lines[hits[0]:hits[0] + n])
        if html.count(orig_block) != 1:
            return None, "ambiguous location"
        html = html.replace(orig_block, repl, 1)
        file_lines = html.split("\n")
        norm_file = [l.strip() for l in file_lines]
    return html, f"{len(raw_blocks)} blocks"


async def _edit_html(html: str, instruction: str, model: str, on_phase=None,
                     own_creds: list[tuple[str, str, str]] | None = None,
                     extras: str = "") -> str:
    """Diff-first edit with full-rewrite fallback. Returns complete HTML."""
    if on_phase:
        await on_phase("diff")
    try:
        dout = await _llm_html([
            {"role": "system", "content": _DIFF_SYS + extras},
            {"role": "user", "content": (
                f"CHANGE REQUEST: {instruction}\n\nCURRENT HTML:\n{html}")},
        ], model, 2000, own_creds=own_creds)
        patched, reason = _apply_diff_blocks(html, dout)
        if patched and "</html>" in patched.lower() and len(patched) > len(html) // 2:
            _log.info("diff edit ok (%s, %d chars)", reason, len(patched))
            return patched
        _log.info("diff edit rejected (%s) — falling back to full rewrite", reason)
    except Exception as e:
        _log.info("diff edit failed (%s) — falling back to full rewrite", type(e).__name__)
    if on_phase:
        await on_phase("rewrite")
    out = await _llm_html([
        {"role": "system", "content": _EDIT_SYS + extras},
        {"role": "user", "content": (
            f"CHANGE REQUEST: {instruction}\n\nCURRENT HTML:\n{html}")},
    ], model, 16000, "medium", require_html=True, own_creds=own_creds)
    return _clean(out)


def _extras_for(db: Session, user_id: str) -> str:
    """User's name + custom system prompt, appended to build prompts."""
    try:
        prof = db.query(models.UserProfile).filter_by(user_id=user_id).first()
        return ai_core.profile_extras(prof)
    except Exception:
        return ""


async def _stream_work(work_factory):
    """SSE wrapper: {type:start} → {type:tick|phase}* → {type:done,payload}|{type:error}.

    The factory receives an `emit(kind, value)` coroutine it can use to report
    progress phases (e.g. "diff" vs "rewrite"). Ticks every 4s keep slow
    big-context edits alive through Cloudflare's ~100s proxy timeout, which
    would 524 a silent JSON response.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=16)

    async def _ticker():
        while True:
            await asyncio.sleep(4)
            try:
                q.put_nowait(("tick", None))
            except asyncio.QueueFull:
                pass

    async def _emit(kind: str, value=None):
        await q.put((kind, value))

    async def _runner():
        try:
            result = await work_factory(_emit)
            await q.put(("done", result))
        except HTTPException as e:
            await q.put(("error", e.detail or f"HTTP {e.status_code}"))
        except Exception as e:
            _log.warning("build stream failed: %s", type(e).__name__, exc_info=True)
            await q.put(("error", f"{type(e).__name__}: {e}"[:300]))

    ticker = asyncio.create_task(_ticker())
    runner = asyncio.create_task(_runner())
    try:
        yield _sse({"type": "start"})
        while True:
            kind, val = await q.get()
            if kind == "tick":
                yield _sse({"type": "tick"})
            elif kind == "phase":
                yield _sse({"type": "phase", "label": val})
            elif kind == "done":
                yield _sse({"type": "done", "payload": val})
                break
            else:
                yield _sse({"type": "error", "detail": val})
                break
    finally:
        ticker.cancel()
        runner.cancel()


@router.get("/templates")
def list_templates():
    """Which style cards Build & Run can retrieve from (diagnostics + UI picker)."""
    try:
        cards = template_bank.load()
    except Exception as e:
        return {"dir": str(template_bank._DIR), "count": -1,
                "error": f"{type(e).__name__}: {e}", "cards": []}
    return {
        "dir": str(template_bank._DIR),
        "count": len(cards),
        "scoring": {
            "min_score": template_bank.MIN_SCORE,
            "min_margin": template_bank.MIN_MARGIN,
            "min_tag_coverage": template_bank.MIN_TAG_COVERAGE,
            "tag_weight": template_bank.TAG_WEIGHT,
        },
        "cards": [{"id": c.get("id"), "name": c.get("name"),
                   "category": c.get("category")} for c in cards],
    }


_TPL_EDIT_SYS = (
    "You are a senior front-end developer customizing a real, complete website "
    "template (single HTML file, inline CSS/JS, may reference its own assets/ "
    "folder). The template HTML below IS the site — keep it 1:1: same structure, "
    "sections, styles, scripts, fonts, images, animations, and overall design. "
    "Apply ONLY the user's requested changes on top of it (copy, colors, names, "
    "content, added/removed sections they explicitly ask for). Never redesign, "
    "simplify, rewrite from scratch, or invent a different layout. "
    "Rules: output the COMPLETE HTML file, no markdown fences, no explanations; "
    "keep the <base> tag exactly as provided; never leave placeholders/TODOs; "
    "keep it working; keep the existing mobile-first responsive behavior intact "
    "(viewport meta, media queries, burger nav); content must stay visible "
    "without JavaScript."
)


@router.post("/website")
async def build_website(req: BuildRequest, request: Request, db: Session = Depends(get_db)):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty.")
    own_creds = byok_svc.creds_for(db, req.user_id) or None
    extras = _extras_for(db, req.user_id)

    async def _work(emit) -> dict:
        # Web Market template flow: "Use template" seeds the prompt with the
        # template id — load that exact template and customize it 1:1 instead of
        # generating a new site from scratch.
        tid = (req.template_id or "").strip()
        if not tid:
            m = re.search(r"\btemplate-\d+\b", prompt)
            tid = m.group(0) if m else ""
        loaded = _market_template_html(tid) if tid else None
        if loaded:
            html, tname = loaded
            # strip the "Customize the … template (id: …) for my business:" seed
            custom = re.sub(
                r"(?is)^\s*customize the .*?template\s*\(id:\s*template-\d+\)\s*"
                r"for my business\s*:?\s*",
                "", prompt).strip()
            model = (getattr(settings, "build_model", "") or settings.groq_model
                     or "openai/gpt-oss-120b")
            if custom:
                # absolute base so assets/ resolve in srcDoc preview AND /build/site/{slug}
                base = f'<base href="{str(request.base_url).rstrip("/")}/market/file/{tid}/">'
                if not re.search(r"<base\s", html, re.I):
                    if re.search(r"<head[^>]*>", html, re.I):
                        html = re.sub(r"(<head[^>]*>)", r"\1" + base, html, count=1, flags=re.I)
                    else:
                        html = base + html
            try:
                cand = await _edit_html(html, custom, model,
                                        on_phase=lambda label: emit("phase", label),
                                        own_creds=own_creds, extras=extras)
                # only accept a complete rewrite of the same template; otherwise
                # fall back to the exact untouched template (still a valid site)
                if ("</html>" in cand.lower() and "<html" in cand.lower()
                        and not _PLACEHOLDER_RE.search(cand)):
                    html = cand
            except Exception as e:
                _log.warning("template customize failed (%s) — serving exact template",
                             type(e).__name__)
            # QA gate for the template path: fix exact violations when cheap;
            # never accept a fix that leaves more problems than it started with.
            html = _auto_fix(html)
            _tpl_v = _lint_html(html, responsive=False)
            if _tpl_v:
                await emit("phase", "lint")
                try:
                    _fix = _auto_fix(_clean(await _llm_html([
                        {"role": "system", "content": _SYS + extras},
                        {"role": "assistant", "content": html},
                        {"role": "user", "content": "QA found violations:\n- "
                         + "\n- ".join(_tpl_v) + "\nReturn the FULL corrected single HTML "
                                                 "file (no fences, no commentary). If you "
                                                 "cannot fix them all, return it unchanged."},
                    ], model, 6000, own_creds=own_creds)))
                    if ("</html>" in _fix.lower() and "<html" in _fix.lower()
                            and len(_lint_html(_fix, responsive=False)) < len(_tpl_v)):
                        html = _fix
                except Exception:
                    pass
                if _lint_html(html, responsive=False):
                    _log.warning("template lint residual: %s",
                                 _lint_html(html, responsive=False)[:5])
            name = re.sub(r"[^a-z0-9]+", "-", (custom or tname).lower()).strip("-")[:40] or "site"
            job_id = uuid.uuid4().hex[:12]
            _JOBS[job_id] = {"name": name, "html": html, "ts": time.time()}
            for k in [k for k, v in _JOBS.items() if time.time() - v["ts"] > 3600]:
                _JOBS.pop(k, None)
            try:
                user = db.query(models.User).filter_by(id=req.user_id).first()
                if not user:
                    user = models.User(id=req.user_id, email=f"{req.user_id}@local")
                    db.add(user)
                    db.commit()
                conv = models.Conversation(user_id=user.id, title=name)
                db.add(conv)
                db.commit()
                db.add(models.Message(conversation_id=conv.id, role="user", content=prompt))
                db.add(models.Message(conversation_id=conv.id, role="assistant",
                                      content=f"Started from the **{tname}** template "
                                              f"({tid}) — customized and ready. Open "
                                              f"Build & Run to preview and publish."))
                db.add(models.BuildSite(conversation_id=conv.id, job_id=job_id, html=html))
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
            return {"job_id": job_id, "name": name, "template": tid, "html": html,
                    "style_ref": tname, "truncated": False,
                    "conversation_id": conv.id if "conv" in locals() else None}
        # Stage 1 — understand the demand before writing any code.
        # What AZX already remembers about the user makes the spec personal
        # ("my bakery" → knows the name, city, tone without being told again).
        mem_ctx = ""
        try:
            relevant = memory_engine.retrieve_relevant(req.user_id, prompt, top_k=6)
            mem_ctx = "\n".join(f"- {h['content']}" for h in relevant if h.get("content"))
        except Exception:
            mem_ctx = ""
        await emit("phase", "understand")
        try:
            understanding = await _understand(prompt, mem_ctx, own_creds=own_creds,
                                              extras=extras)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Understand call failed: {type(e).__name__}")
        if not understanding["want"]:
            return {"status": "chat", "reply": understanding["reply"]}
        template = (req.template.strip().lower() or understanding.get("template")
                    or _pick_template(prompt))
        if template not in TEMPLATES:
            template = "landing"
        spec = understanding.get("spec") or prompt
        ref_key = understanding.get("reference", "none")
        ref_line = ""
        if ref_key in REFERENCES:
            ref_line = (f"\nDesign reference ({ref_key}): match its mood, layout rhythm and "
                        f"typography feel — {REFERENCES[ref_key]}. Do NOT copy its text or brand.")
        # Coder model for builds (heavier than the chat model, overridable via .env).
        model = (getattr(settings, "build_model", "") or settings.groq_model
                 or "openai/gpt-oss-120b")
        # Few-shot style retrieval: match the prompt against the template bank and
        # hand the closest design system to the model. This is how a hosted model
        # learns from examples - Groq exposes no fine-tuning API. Only used when the
        # intake didn't already pick a REFERENCES design above.
        await emit("phase", "style")
        ref = None
        if ref_key == "none":
            try:
                cards = template_bank.load()
                matches = template_bank.best(prompt, top=1)
                ref = matches[0] if matches else None
                if not ref:
                    _log.info("style retrieval: %d cards available, no match for %r",
                              len(cards), prompt[:60])
            except Exception as e:
                _log.warning("style retrieval failed: %s", type(e).__name__)
                ref = None
        style_block = template_bank.render(ref)
        # Prompt engine: raw words + keypoints + memory + style → BUILD BRIEF
        # (palette, fonts, section plan, interactions). The light call never
        # blocks a build — compose() falls back to neutral-premium internally.
        await emit("phase", "engine")
        brief = await prompt_engine.compose(
            prompt, spec=spec, mem_ctx=mem_ctx,
            style_block=style_block or ref_line,
            llm=lambda msgs: _llm_html(msgs, "openai/gpt-oss-20b", 1000, "low",
                                       own_creds=own_creds))
        brief_text = prompt_engine.render(brief)
        # Real image URLs so the generated page never ships links that 404 —
        # the brief's imagery query targets this site's actual subject.
        await emit("phase", "images")
        img_q = str(brief.get("imagery") or "").strip() or prompt
        try:
            imgs = web_images.search(img_q, limit=6)
            if not imgs and img_q != prompt:
                imgs = web_images.search(prompt, limit=6)
        except Exception:
            imgs = []
        user_msg = (
            f"Build {TEMPLATES[template]}.\n\n{brief_text}\n\n"
            f"Original user request: {prompt}\n"
            f"Template: {template}.\nKeep it polished and responsive.")
        if style_block:
            user_msg += "\n\n" + style_block
        if ref_line:
            user_msg += ref_line
        if style_block or ref_line:
            user_msg += ("\nMood only: the BUILD BRIEF above owns the palette, fonts "
                         "and section plan — the reference just sets character.")
        urls = "\n".join(f"- {i.get('thumb')}" for i in imgs if i.get("thumb"))
        if urls:
            user_msg += "\n\nIMAGE URLS (use these, and only these, for imagery):\n" + urls
        messages = [
            {"role": "system", "content": _SYS + extras},
            {"role": "user", "content": user_msg},
        ]

        html = ""
        await emit("phase", "generate")
        try:
            html = _auto_fix(_clean(await _llm_html(messages, model, 6000, own_creds=own_creds)))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Build model call failed: {type(e).__name__}")
        if "<html" not in html.lower():
            raise HTTPException(status_code=502, detail="Model did not return HTML. Try again.")
        # QA gate: deterministic lint → targeted repair (exact violations,
        # max 2 passes) — keep whichever attempt has the fewest problems.
        await emit("phase", "lint")
        best_html, best_v = html, _lint_html(html)
        for _pass in range(2):
            if not best_v:
                break
            n = len(best_v)
            await emit("phase",
                       (f"fixing {n} issue{'s' if n != 1 else ''}…" if _pass == 0
                        else "final quality pass…"))
            try:
                cand = _auto_fix(_clean(await _llm_html([
                    {"role": "system", "content": _SYS + extras},
                    {"role": "assistant", "content": best_html},
                    {"role": "user", "content": "QA found violations:\n- "
                     + "\n- ".join(best_v) + "\nReturn the FULL corrected single HTML "
                                             "file — no fences, no commentary. Fix every "
                                             "violation; keep everything else identical."},
                ], model, 6000, own_creds=own_creds)))
            except Exception:
                continue
            if "<html" not in cand.lower() or "</html>" not in cand.lower():
                continue
            cand_v = _lint_html(cand)
            if len(cand_v) < len(best_v):
                best_html, best_v = cand, cand_v
        html = best_html
        if best_v:
            _log.warning("build lint residual (%d): %s", len(best_v), best_v[:5])
        # Missing </html> means the token budget ran out mid-document and the
        # repair passes couldn't fix it — only a flag for the UI.
        truncated = "</html>" not in html.lower()
        if truncated:
            _log.warning("build output truncated (%d chars)", len(html))
        name = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "site"
        await emit("phase", "save")
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {"name": name, "html": html, "ts": time.time()}
        # prune old jobs
        for k in [k for k, v in _JOBS.items() if time.time() - v["ts"] > 3600]:
            _JOBS.pop(k, None)
        # history: show builds in the usual chats menu — the sidebar marks them
        # with the </> icon via BuildSite.is_build, no emoji prefix needed.
        try:
            user = db.query(models.User).filter_by(id=req.user_id).first()
            if not user:
                user = models.User(id=req.user_id, email=f"{req.user_id}@local")
                db.add(user)
                db.commit()
            conv = models.Conversation(user_id=user.id, title=name)
            db.add(conv)
            db.commit()
            db.add(models.Message(conversation_id=conv.id, role="user", content=prompt))
            db.add(models.Message(conversation_id=conv.id, role="assistant",
                                  content=f"Built **{name}** ({template}, {(len(html) / 1024):.1f} KB). Open Build & Run to preview and download the zip."))
            db.add(models.BuildSite(conversation_id=conv.id, job_id=job_id, html=html))
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        return {"job_id": job_id, "name": name, "template": template, "html": html,
                "style_ref": (ref_key if ref_key in REFERENCES
                              else (ref or {}).get("name", "")),
                "truncated": truncated,
                "conversation_id": conv.id if "conv" in locals() else None}

    return StreamingResponse(_stream_work(_work), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.get("/conversation/{conv_id}")
def build_for_conversation(conv_id: str, db: Session = Depends(get_db)):
    """Latest built site for a build chat — powers the Build & Run screen on reopen."""
    row = (db.query(models.BuildSite)
           .filter_by(conversation_id=conv_id)
           .order_by(models.BuildSite.created_at.desc())
           .first())
    if not row:
        raise HTTPException(status_code=404, detail="No build for this chat.")
    _JOBS.setdefault(row.job_id, {"name": row.job_id, "html": row.html, "ts": time.time()})
    conv = db.query(models.Conversation).filter_by(id=conv_id).first()
    pub = db.query(models.PublishedSite).filter_by(job_id=row.job_id).first()
    return {"job_id": row.job_id, "html": row.html,
            "name": (conv.title if conv else row.job_id),
            "pub": ({"slug": pub.slug, "name": pub.name,
                     "url": f"/build/site/{pub.slug}"} if pub else None)}


def _load_job_html(job_id: str, db: Session) -> dict:
    job = _JOBS.get(job_id)
    if job:
        return job
    row = db.query(models.BuildSite).filter_by(job_id=job_id).first()
    if row:
        return {"name": row.job_id, "html": row.html, "ts": time.time()}
    raise HTTPException(status_code=404, detail="Build not found.")


class BuildEdit(BaseModel):
    job_id: str
    instruction: str
    user_id: str = "master-001"


_EDIT_SYS = (
    "You are a senior front-end developer editing a single-file HTML site "
    "(inline CSS/JS). Apply ONLY the requested change, keeping everything else — "
    "structure, styles, scripts, content not mentioned — exactly as it was. "
    "Rules: output the COMPLETE edited HTML file, no markdown fences, no "
    "explanations; never leave placeholders/TODOs; keep it working; "
    "keep the existing mobile-first responsive behavior intact (viewport meta, "
    "media queries, burger nav) — any NEW layout must stay fluid at 390px; "
    "content must stay visible without JavaScript."
)


@router.post("/edit")
async def build_edit(req: BuildEdit, db: Session = Depends(get_db)):
    """Chat-style edit of the current build: "change the hero to green".

    Streams SSE (start → tick* → done/error) so big-template edits that take
    minutes on the fallback model survive proxy timeouts.
    """
    job = _load_job_html(req.job_id, db)
    instruction = req.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="No instruction given.")
    own_creds = byok_svc.creds_for(db, req.user_id) or None
    extras = _extras_for(db, req.user_id)

    async def _work(emit) -> dict:
        try:
            html = await _edit_html(job["html"], instruction, "openai/gpt-oss-120b",
                                    on_phase=lambda label: emit("phase", label),
                                    own_creds=own_creds, extras=extras)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502,
                                detail=f"Edit failed ({type(e).__name__}) — try again shortly.")
        if "</html>" not in html.lower():
            raise HTTPException(status_code=502,
                                detail="Edit came back incomplete — try a shorter instruction.")
        _JOBS[req.job_id] = {"name": job.get("name", req.job_id), "html": html, "ts": time.time()}
        try:
            row = db.query(models.BuildSite).filter_by(job_id=req.job_id).first()
            if row:
                row.html = html
                db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        # keep any published copy in sync so edits show on the live URL
        try:
            pub = db.query(models.PublishedSite).filter_by(job_id=req.job_id).first()
            if pub:
                pub.html = html
                db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        return {"job_id": req.job_id, "html": html}

    return StreamingResponse(_stream_work(_work), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


class BuildPublish(BaseModel):
    job_id: str
    user_id: str = "master-001"


@router.post("/publish")
def build_publish(req: BuildPublish, db: Session = Depends(get_db)):
    """Make the built site live at /site/{slug}. Re-publish updates in place."""
    job = _load_job_html(req.job_id, db)
    name = re.sub(r"[^a-z0-9]+", "-", str(job.get("name", "site")).lower()).strip("-") or "site"
    pub = db.query(models.PublishedSite).filter_by(job_id=req.job_id).first()
    if not pub:
        slug = f"{name}-{req.job_id[:6]}"
        pub = models.PublishedSite(job_id=req.job_id, slug=slug,
                                   name=str(job.get("name", name))[:80], html=job["html"])
        db.add(pub)
    else:
        pub.html = job["html"]
    db.commit()
    return {"slug": pub.slug, "name": pub.name,
            "url": f"/build/site/{pub.slug}", "updated": pub.updated_at or pub.created_at}


@router.get("/published/{job_id}")
def build_published(job_id: str, db: Session = Depends(get_db)):
    pub = db.query(models.PublishedSite).filter_by(job_id=job_id).first()
    if not pub:
        raise HTTPException(status_code=404, detail="Not published yet.")
    return {"slug": pub.slug, "name": pub.name, "url": f"/build/site/{pub.slug}"}


# Live hosting for published sites — single-file HTML, served straight.
@router.get("/site/{slug}", include_in_schema=False)
def serve_published(slug: str, db: Session = Depends(get_db)):
    pub = db.query(models.PublishedSite).filter_by(slug=slug).first()
    if not pub:
        raise HTTPException(status_code=404, detail="Site not found.")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(pub.html)


@router.get("/zip/{job_id}")
def download_zip(job_id: str, db: Session = Depends(get_db)):
    job = _JOBS.get(job_id)
    if not job:
        # jobs expire hourly — fall back to the persisted copy
        row = db.query(models.BuildSite).filter_by(job_id=job_id).first()
        if row:
            job = {"name": row.job_id, "html": row.html}
    if not job:
        raise HTTPException(status_code=404, detail="Build expired. Rebuild it.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", job["html"])
    buf.seek(0)
    name = re.sub(r"[^a-z0-9-]+", "-", str(job.get("name", "site")).lower()).strip("-") or "site"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename={name}.zip"})
