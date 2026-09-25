"""Web image search — Wikimedia Commons (free, no key, stable).

Covers objects/animals/food/places well; weak for celebrities and
current events. Returns [] on any failure so chat still replies.
"""
import re

import httpx

API = "https://commons.wikimedia.org/w/api.php"
_UA = {"User-Agent": "AshtraAI/1.0 (personal assistant; contact: local)"}


def _serper(query: str, limit: int) -> list[dict]:
    """Serper.dev Google image results (free SERPER_KEY, no card)."""
    try:
        from ..config import settings
        key = (getattr(settings, "serper_key", "") or "").strip()
    except Exception:
        key = ""
    if not key:
        return []
    try:
        r = httpx.post("https://google.serper.dev/images",
                       headers={"X-API-KEY": key, "Content-Type": "application/json"},
                       json={"q": query, "num": limit}, timeout=15)
        r.raise_for_status()
        out = []
        for h in (r.json().get("images") or [])[:limit]:
            url = h.get("imageUrl") or ""
            if url:
                out.append({"title": (h.get("title") or query)[:80],
                            "thumb": url, "page": h.get("link", "")})
        return out
    except Exception:
        return []


def _pixabay(query: str, limit: int) -> list[dict]:
    """Pixabay free tier (needs PIXABAY_KEY env). None-safe, [] on failure."""
    try:
        from ..config import settings
        key = (getattr(settings, "pixabay_key", "") or "").strip()
    except Exception:
        key = ""
    if not key:
        return []
    try:
        r = httpx.get("https://pixabay.com/api/", params={
            "key": key, "q": query, "image_type": "photo",
            "per_page": limit, "safesearch": "true",
        }, timeout=15)
        r.raise_for_status()
        out = []
        for h in (r.json().get("hits") or [])[:limit]:
            url = h.get("webformatURL") or h.get("previewURL") or ""
            if url:
                out.append({"title": h.get("tags", query)[:80],
                            "thumb": url, "page": h.get("pageURL", "")})
        return out
    except Exception:
        return []


def _clean_query(text: str) -> str:
    t = text.lower().strip()
    # "what does X look like" / "what do Xs look like"
    m = re.search(r"what do(?:es)? (.+?) look like", t)
    if m:
        return m.group(1).strip(" ?!.")
    # "show me a picture/photo/image of X", "picture of X", "image of X"
    m = re.search(
        r"(?:show|send|give|find)(?: me)?(?: a| an)?(?: pic|picture|photo|image)?(?:s)? of (.+)", t)
    if m:
        return m.group(1).strip(" ?!.")
    m = re.search(r"(?:pic|picture|photo|image)s? of (.+)", t)
    if m:
        return m.group(1).strip(" ?!.")
    return ""


def wants_images(text: str) -> str:
    """Return the search query if the message asks to SEE something, else ''."""
    t = text.lower()
    triggers = ("look like", "picture of", "image of", "photo of", "pic of",
                "show me", "show a", "what does", "what do")
    if not any(k in t for k in triggers):
        return ""
    return _clean_query(text)


def search(query: str, limit: int = 3) -> list[dict]:
    """Search web images. Serper (Google results) → Pixabay → Commons."""
    if not query:
        return []
    for fn in (_serper, _pixabay):
        hit = fn(query, limit)
        if hit:
            return hit
    try:
        r = httpx.get(API, params={
            "action": "query", "format": "json",
            "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6, "gsrlimit": 10,
            "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiurlwidth": 640,
        }, headers=_UA, timeout=15)
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
        out = []
        for p in pages.values():
            info = (p.get("imageinfo") or [{}])[0]
            thumb = info.get("thumburl") or info.get("url") or ""
            if not thumb:
                continue
            if thumb.lower().endswith((".svg", ".tif", ".tiff")):
                continue
            out.append({"title": p.get("title", "").replace("File:", ""),
                        "thumb": thumb,
                        "page": info.get("descriptionurl", "")})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []
