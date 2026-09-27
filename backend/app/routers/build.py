"""Build & Run v1 — AI website builder (websites only for now).

POST /build/website → LLM writes a single-file site from a template.
GET  /build/zip/{job_id} → download it as website.zip (index.html).
"""
import io
import re
import time
import uuid
import zipfile

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db

router = APIRouter(prefix="/build", tags=["build"])

_JOBS: dict[str, dict] = {}  # job_id → {name, html} (single worker, ephemeral)

TEMPLATES = {
    "landing": "a modern landing page with hero, features grid, and footer",
    "portfolio": "a personal portfolio with hero, projects grid, and contact section",
    "blog": "a minimal blog layout with header, post cards, and footer",
    "dashboard": "a clean admin dashboard with sidebar and stat cards",
    "restaurant": "a restaurant site with hero, menu section, and reservation form (front-end only)",
}

_SYS = ("You are a front-end developer. Output ONLY a complete single HTML file "
        "(inline <style> and <script>, no external files except images via URL). "
        "No markdown fences, no explanations — just the HTML.")


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


@router.post("/website")
async def build_website(req: BuildRequest, db: Session = Depends(get_db)):
    _ = db  # reserved for future persistence
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty.")
    template = req.template.strip().lower() or _pick_template(prompt)
    if template not in TEMPLATES:
        template = "landing"
    # Powerful coder model for builds (falls back to chat model).
    model = "openai/gpt-oss-120b"
    messages = [
        {"role": "system", "content": _SYS},
        {"role": "user", "content": (
            f"Build {TEMPLATES[template]}.\nSite idea: {prompt}\n"
            f"Template: {template}. Keep it polished and responsive.")},
    ]
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={"model": model, "messages": messages,
                      "max_tokens": 4000, "temperature": 0.5,
                      "reasoning_effort": "medium"},
            )
            r.raise_for_status()
            html = r.json()["choices"][0]["message"]["content"] or ""
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Build model call failed: {type(e).__name__}")
    html = re.sub(r"^```html\s*|\s*```$", "", html.strip())
    if "<html" not in html.lower():
        raise HTTPException(status_code=502, detail="Model did not return HTML. Try again.")
    name = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "site"
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"name": name, "html": html, "ts": time.time()}
    # prune old jobs
    for k in [k for k, v in _JOBS.items() if time.time() - v["ts"] > 3600]:
        _JOBS.pop(k, None)
    return {"job_id": job_id, "name": name, "template": template, "html": html}


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
