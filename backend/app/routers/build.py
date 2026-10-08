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
from ..services import memory_engine, template_bank, web_images

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
        "Hard rules: every section fully implemented; never leave placeholder comments, "
        "TODOs, or empty script blocks — if a chart, animation, or function is required, "
        "write the real working code; responsive on mobile; visually polished. "
        "When a DESIGN REFERENCE is given, adopt its palette, type, spacing and section "
        "rhythm, but write original markup and copy for the requested site. "
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
must-have features, responsive notes — written as instructions to a
developer>"}
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


async def _understand(prompt: str, mem_ctx: str = "") -> dict:
    """Stage 1 — does the user actually want a site? If so, write the spec."""
    user_msg = prompt
    if mem_ctx:
        user_msg += (f"\n\nWHAT AZX ALREADY REMEMBERS ABOUT THIS USER "
                     f"(use it — business name, type, location, preferences):\n{mem_ctx}")
    data: dict = {}
    for _attempt in range(3):  # JSON sometimes gets truncated mid-spec — retry
        out = await _llm_html([
            {"role": "system", "content": _UNDERSTAND_SYS},
            {"role": "user", "content": user_msg},
        ], "openai/gpt-oss-20b", 1200, "low")
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


async def _llm_html(messages: list[dict], model: str, max_tokens: int, effort: str = "medium") -> str:
    """Groq call with backoff — big builds burn the free TPM quota, so a 429
    right after a build is normal and must be waited out, not failed."""
    import asyncio
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": 0.5,
               "reasoning_effort": effort}
    url = f"{settings.groq_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    async with httpx.AsyncClient(timeout=180) as c:
        for attempt in range(4):
            try:
                r = await c.post(url, headers=headers, json=payload)
                if r.status_code == 429:
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
    raise HTTPException(status_code=502,
                        detail="Model is rate-limited right now — try again in a few seconds.")


def _clean(html: str) -> str:
    return re.sub(r"^```html\s*|\s*```$", "", html.strip())


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
    "keep it working and responsive; content must stay visible without JavaScript."
)


@router.post("/website")
async def build_website(req: BuildRequest, request: Request, db: Session = Depends(get_db)):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty.")
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
        if custom:
            # absolute base so assets/ resolve in srcDoc preview AND /build/site/{slug}
            base = f'<base href="{str(request.base_url).rstrip("/")}/market/file/{tid}/">'
            if not re.search(r"<base\s", html, re.I):
                if re.search(r"<head[^>]*>", html, re.I):
                    html = re.sub(r"(<head[^>]*>)", r"\1" + base, html, count=1, flags=re.I)
                else:
                    html = base + html
            model = (getattr(settings, "build_model", "") or settings.groq_model
                     or "openai/gpt-oss-120b")
            try:
                out = await _llm_html([
                    {"role": "system", "content": _TPL_EDIT_SYS},
                    {"role": "user", "content": (
                        f"CHANGE REQUEST: {custom}\n\nTEMPLATE HTML:\n{html}")},
                ], model, 14000, "medium")
                cand = _clean(out)
                # only accept a complete rewrite of the same template; otherwise
                # fall back to the exact untouched template (still a valid site)
                if ("</html>" in cand.lower() and "<html" in cand.lower()
                        and not _PLACEHOLDER_RE.search(cand)):
                    html = cand
            except HTTPException:
                raise
            except Exception as e:
                _log.warning("template customize failed (%s) — serving exact template",
                             type(e).__name__)
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
    try:
        understanding = await _understand(prompt, mem_ctx)
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
    # Real image URLs (Serper/Brave/Pixabay/Pexels/...) so the generated page
    # never ships links that 404.
    try:
        imgs = web_images.search(prompt, limit=6)
    except Exception:
        imgs = []
    user_msg = (
        f"Build {TEMPLATES[template]}.\nBuild spec (keypoints from intake analysis): {spec}\n"
        f"Original user request: {prompt}\n"
        f"Template: {template}.{ref_line}\nKeep it polished and responsive.")
    if style_block:
        user_msg += "\n\n" + style_block
    urls = "\n".join(f"- {i.get('thumb')}" for i in imgs if i.get("thumb"))
    if urls:
        user_msg += "\n\nIMAGE URLS (use these, and only these, for imagery):\n" + urls
    messages = [
        {"role": "system", "content": _SYS},
        {"role": "user", "content": user_msg},
    ]

    html = ""
    try:
        html = _clean(await _llm_html(messages, model, 6000))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Build model call failed: {type(e).__name__}")
    # Repair pass: model left placeholders, dead image hosts, or got cut off.
    if ("<html" not in html.lower() or "</html>" not in html.lower()
            or _PLACEHOLDER_RE.search(html) or "via.placeholder.com" in html):
        try:
            fix = _clean(await _llm_html([
                {"role": "system", "content": _SYS},
                {"role": "user", "content": (
                    f"Build {TEMPLATES[template]}.\nSite idea: {prompt}\nTemplate: {template}.")},
                {"role": "assistant", "content": html},
                {"role": "user", "content": "Your file has placeholder/TODO comments, a dead "
                                            "image host (via.placeholder.com), invisible "
                                            "content that needs JS, or is truncated — return "
                                            "the FULL corrected single HTML file: all content "
                                            "visible without JS, only picsum.photos/placehold."
                                            "co/gradient images, every part implemented. HTML only."},
            ], model, 6000))
            if "</html>" in fix.lower():
                html = fix
        except Exception:
            pass
    if "<html" not in html.lower():
        raise HTTPException(status_code=502, detail="Model did not return HTML. Try again.")
    # Missing </html> means the token budget ran out mid-document; the repair
    # pass above already tried to fix it, so this is only a flag for the UI.
    truncated = "</html>" not in html.lower()
    if truncated:
        _log.warning("build output truncated (%d chars)", len(html))
    name = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "site"
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


_EDIT_SYS = (
    "You are a senior front-end developer editing a single-file HTML site "
    "(inline CSS/JS). Apply ONLY the requested change, keeping everything else — "
    "structure, styles, scripts, content not mentioned — exactly as it was. "
    "Rules: output the COMPLETE edited HTML file, no markdown fences, no "
    "explanations; never leave placeholders/TODOs; keep it working and "
    "responsive; content must stay visible without JavaScript."
)


@router.post("/edit")
async def build_edit(req: BuildEdit, db: Session = Depends(get_db)):
    """Chat-style edit of the current build: "change the hero to green"."""
    job = _load_job_html(req.job_id, db)
    instruction = req.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="No instruction given.")
    out = await _llm_html([
        {"role": "system", "content": _EDIT_SYS},
        {"role": "user", "content": (
            f"CHANGE REQUEST: {instruction}\n\nCURRENT HTML:\n{job['html']}")},
    ], "openai/gpt-oss-120b", 14000, "medium")
    html = _clean(out)
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
