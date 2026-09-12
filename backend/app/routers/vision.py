"""Vision endpoint — first half of the describe-then-chat pipeline.

POST /vision/describe (multipart: user_id, message?, f=image file)
  → vision model reads the image → returns {description} as text.
The caller then sends POST /chat {message, image_context: description} and
the text brain (gpt-oss-20b) reasons over it. Stateless: nothing stored.
"""
import base64

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..config import settings
from ..security import check_rate, client_ip
from ..services import ai_core

router = APIRouter(prefix="/vision", tags=["vision"])

ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@router.post("/describe")
async def describe(request: Request, user_id: str = Form(...),
                   message: str = Form(""), f: UploadFile = File(...)):
    check_rate(f"chat:{client_ip(request)}:{user_id.strip()}", settings.chat_rate_limit_per_minute)
    mime = (f.content_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise HTTPException(400, f"unsupported image type (need PNG/JPEG/WEBP/GIF, got {mime or 'unknown'})")
    content = await f.read()
    if len(content) > settings.vision_max_mb * 1_000_000:
        raise HTTPException(400, f"image too large (>{settings.vision_max_mb}MB)")
    if not content:
        raise HTTPException(400, "empty image")
    b64 = base64.b64encode(content).decode()
    desc = await ai_core.describe_image(b64, mime, message)
    if not desc:
        from ..services.ai_core import active_provider  # noqa: E402
        if not active_provider().get("vision_configured"):
            raise HTTPException(502, "vision link not configured — "
                                     "set VISION_BASE_URL + VISION_API_KEY + VISION_MODEL (.env.example).")
        raise HTTPException(502, "vision model hiccup (free shared quota blips often) — "
                                 "your image is kept, wait a bit and send again.")
    return {"description": desc, "bytes": len(content), "mime": mime}
