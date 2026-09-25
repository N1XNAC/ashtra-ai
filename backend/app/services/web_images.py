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


def _brave(query: str, limit: int) -> list[dict]:
    """Brave image search (free BRAVE_KEY, no card)."""
    try:
        from ..config import settings
        key = (getattr(settings, "brave_key", "") or "").strip()
    except Exception:
        key = ""
    if not key:
        return []
    try:
        r = httpx.get("https://api.search.brave.com/res/v1/images/search",
                      headers={"X-Subscription-Token": key},
                      params={"q": query, "count": limit}, timeout=15)
        r.raise_for_status()
        out = []
        for h in (r.json().get("results") or [])[:limit]:
            thumb = ((h.get("thumbnail") or {}).get("src")) or h.get("src") or ""
            if thumb:
                out.append({"title": (h.get("title") or query)[:80],
                            "thumb": thumb, "page": h.get("url", "")})
        return out
    except Exception:
        return []


def _loremflickr(query: str, limit: int) -> list[dict]:
    """LoremFlickr keyword photos — keyless, practically unlimited."""
    import urllib.parse
    q = ",".join(query.lower().split()[:4])
    if not q:
        return []
    seed = abs(hash(query)) % 1000
    return [{"title": query[:80],
             "thumb": f"https://loremflickr.com/640/480/{urllib.parse.quote(q)}?lock={seed + i}",
             "page": "https://loremflickr.com"} for i in range(limit)]


def _pollinations(query: str, limit: int) -> list[dict]:
    """AI-generated image — keyless, unlimited, always available."""
    import urllib.parse
    if not query:
        return []
    seed = abs(hash(query)) % 1000
    return [{"title": f"{query} (AI image)".strip()[:80],
             "thumb": f"https://image.pollinations.ai/prompt/{urllib.parse.quote(query)}?w=640&h=480&seed={seed + i}&nologo=true",
             "page": ""} for i in range(min(limit, 1))]


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
    """Search web images. Serper → Brave → Pixabay → LoremFlickr → Pollinations → Commons."""
    if not query:
        return []
    for fn in (_serper, _brave, _pixabay, _loremflickr, _pollinations):
        hit = fn(query, limit)
        if hit:
            return hit
    return []
