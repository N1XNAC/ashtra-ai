from fastapi import APIRouter, Query

from ..services import web_images

router = APIRouter(prefix="/web", tags=["web"])


@router.get("/images")
def image_search(q: str = Query(..., min_length=2, max_length=120), limit: int = 3):
    """Free web image search (Wikimedia Commons, no key)."""
    return {"query": q, "images": web_images.search(q, limit=max(1, min(limit, 6)))}
