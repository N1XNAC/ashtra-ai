"""Build & Run v1 — AI website builder (websites only for now).

POST /build/website → LLM writes a single-file site from a template.
GET  /build/zip/{job_id} → download it as website.zip (index.html).
"""
import asyncio
import io
import logging
import re
import time
import uuid
import zipfile

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..database import get_db
from ..services import template_bank, web_images

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
        "(inline <style> and <script>, no external files except images via URL). "
        "No markdown fences, no explanations - just the HTML. "
        "When a DESIGN REFERENCE is given, adopt its palette, type, spacing and section "
        "rhythm, but write original markup and copy for the requested site. "
        "When IMAGE URLS are given, use only those for <img> and background-image so "
        "every image actually loads.")


def _pick_template(prompt: str) -> str:
    t = prompt.lower()
    for name in TEMPLATES:
        if name in t:
            return name
    return "landing"


class BuildRequest(BaseModel):
    user_id: str = "master-001"
    prompt: str
    template: str = ""


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


@router.post("/website")
async def build_website(req: BuildRequest, db: Session = Depends(get_db)):
    _ = db  # reserved for future persistence
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty.")
    template = req.template.strip().lower() or _pick_template(prompt)
    if template not in TEMPLATES:
        template = "landing"
    # Coder model for builds (heavier than the chat model, overridable via .env).
    model = (getattr(settings, "build_model", "") or settings.groq_model
             or "openai/gpt-oss-120b")
    # Few-shot style retrieval: match the prompt against the template bank and
    # hand the closest design system to the model. This is how a hosted model
    # learns from examples - Groq exposes no fine-tuning API.
    ref = None
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
        f"Build {TEMPLATES[template]}.\nSite idea: {prompt}\n"
        f"Template: {template}. Keep it polished and responsive.")
    if style_block:
        user_msg += "\n\n" + style_block
    urls = "\n".join(f"- {i.get('thumb')}" for i in imgs if i.get("thumb"))
    if urls:
        user_msg += "\n\nIMAGE URLS (use these, and only these, for imagery):\n" + urls
    messages = [
        {"role": "system", "content": _SYS},
        {"role": "user", "content": user_msg},
    ]

    payload = {"model": model, "messages": messages,
               "max_tokens": 8000, "temperature": 0.5,
               "reasoning_effort": "medium"}
    html = ""
    finish_reason = ""
    resp_status = 0
    resp_body = ""
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = None
            for attempt in (1, 2):
                r = await c.post(
                    f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    json=payload,
                )
                # Free tiers blip with 429s; a single retry costs nothing.
                if r.status_code in (429, 500, 502, 503) and attempt == 1:
                    _log.warning("build model %s -> %s, retrying once", model, r.status_code)
                    await asyncio.sleep(3)
                    continue
                break
            resp_status = r.status_code
            resp_body = r.text[:300]
            if r.status_code < 400:
                choice = ((r.json() or {}).get("choices") or [{}])[0]
                finish_reason = choice.get("finish_reason") or ""
                html = (choice.get("message") or {}).get("content") or ""
    except Exception as e:
        _log.warning("build model call failed: %s", type(e).__name__)
        raise HTTPException(status_code=502,
                            detail=f"Build model call failed: {type(e).__name__}")
    if resp_status >= 400 or not html:
        _log.warning("build model %s -> %s: %s", model, resp_status, resp_body)
        raise HTTPException(status_code=502,
                            detail=f"Build model call failed: HTTP {resp_status} ({resp_body[:120]})")
    html = re.sub(r"^```html\s*|\s*```$", "", html.strip())
    if "<html" not in html.lower():
        raise HTTPException(status_code=502, detail="Model did not return HTML. Try again.")
    # finish_reason == "length" means the token budget ran out mid-document,
    # which ships a page that is missing </html> and often its closing tags.
    truncated = finish_reason == "length" or "</html>" not in html.lower()
    if truncated:
        _log.warning("build output truncated (finish_reason=%s, %d chars)",
                     finish_reason, len(html))
    name = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "site"
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"name": name, "html": html, "ts": time.time()}
    # prune old jobs
    for k in [k for k, v in _JOBS.items() if time.time() - v["ts"] > 3600]:
        _JOBS.pop(k, None)
    # history: show builds in the usual chats menu, flagged 🔨
    try:
        user = db.query(models.User).filter_by(id=req.user_id).first()
        if not user:
            user = models.User(id=req.user_id, email=f"{req.user_id}@local")
            db.add(user)
            db.commit()
        conv = models.Conversation(user_id=user.id, title=f"🔨 {name}")
        db.add(conv)
        db.commit()
        db.add(models.Message(conversation_id=conv.id, role="user", content=prompt))
        db.add(models.Message(conversation_id=conv.id, role="assistant",
                              content=f"Built **{name}** ({template}, {(len(html) / 1024):.1f} KB). Open Build & Run to preview and download the zip."))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return {"job_id": job_id, "name": name, "template": template, "html": html,
            "style_ref": (ref or {}).get("name", ""), "truncated": truncated,
            "conversation_id": conv.id if "conv" in locals() else None}


@router.get("/zip/{job_id}")
def download_zip(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Build expired. Rebuild it.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", job["html"])
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename={job['name']}.zip"})
