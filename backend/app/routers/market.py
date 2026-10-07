"""Web Market — showcase the fixed template catalog with live AI editing.

GET  /market/list               → card info from each template's metadata.json
GET  /market/file/{id}/{path}   → serve the template's own files (preview)
POST /market/edit               → AI applies a change to the template HTML
"""
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .build import _llm_html, _clean

router = APIRouter(prefix="/market", tags=["market"])

# Templates ship with the repo (backend/templates) so the market works on any
# host; TEMPLATES_DIR overrides for local dev.
_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "templates"
TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR") or _DEFAULT_DIR)
_ID_RE = re.compile(r"^template-\d+$")
# template-8 is a duplicate of template-7 — keep it on disk, hide it from the market
_HIDDEN = {"template-3", "template-8", "template-9"}  # removed/retired from store


def _dir(tid: str) -> Path:
    if not _ID_RE.match(tid):
        raise HTTPException(status_code=400, detail="Bad template id.")
    base = (TEMPLATES_DIR / tid).resolve()
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="Template not found.")
    return base


@router.get("/list")
def market_list():
    if not TEMPLATES_DIR.is_dir():
        return []
    out = []
    for d in sorted(TEMPLATES_DIR.glob("template-*")):
        if not d.is_dir() or not _ID_RE.match(d.name):
            continue
        if d.name in _HIDDEN:  # duplicate / retired entries
            continue
        name, desc = d.name, ""
        meta = d / "metadata.json"
        if meta.is_file():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                name = m.get("name") or d.name
                desc = m.get("description") or ""
            except Exception:
                pass
        if not (d / "index.html").is_file():
            continue
        out.append({"id": d.name, "name": name, "description": desc})
    return out


@router.get("/file/{tid}/{rel:path}")
def market_file(tid: str, rel: str):
    base = _dir(tid)
    # React/Vite templates (t3 Jack, t9) ship a built dist/ — serve it instead
    # of the source index.html that points at unbundled /src/main.tsx
    root = base
    dist = base / "dist"
    rel = rel or "index.html"
    if rel == "index.html" and (dist / "index.html").is_file():
        root = dist
    if rel.startswith("assets/") and (dist / rel).is_file():
        root = dist
    target = (root / rel).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(target)


class MarketEdit(BaseModel):
    template_id: str
    html: str
    instruction: str


_EDIT_SYS = (
    "You are a senior front-end developer editing a single-file HTML template "
    "(inline CSS/JS). Apply ONLY the requested change, keeping everything else — "
    "structure, styles, scripts, content not mentioned — exactly as it was. "
    "Rules: output the COMPLETE edited HTML file, no markdown fences, no "
    "explanations; never leave placeholders/TODOs; keep it working and "
    "responsive; content must stay visible without JavaScript."
)


@router.post("/edit")
async def market_edit(req: MarketEdit):
    _dir(req.template_id)
    instruction = req.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="No instruction given.")
    if not req.html.strip():
        raise HTTPException(status_code=400, detail="No HTML to edit.")
    out = await _llm_html([
        {"role": "system", "content": _EDIT_SYS},
        {"role": "user", "content": (
            f"CHANGE REQUEST: {instruction}\n\nTEMPLATE HTML:\n{req.html}")},
    ], "openai/gpt-oss-120b", 14000, "medium")
    html = _clean(out)
    if "</html>" not in html.lower():
        raise HTTPException(status_code=502,
                            detail="Edit came back incomplete — try a shorter instruction.")
    return {"html": html}
