"""Web image search — Wikimedia Commons (free, no key, stable).

Covers objects/animals/food/places well; weak for celebrities and
current events. Returns [] on any failure so chat still replies.
"""
import re

import httpx

API = "https://commons.wikimedia.org/w/api.php"
_UA = {"User-Agent": "AshtraAI/1.0 (https://github.com/N1XNAC/ashtra-ai; local) python-httpx"}


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


def _pexels(query: str, limit: int) -> list[dict]:
    """Pexels free key (instant signup, no card)."""
    try:
        from ..config import settings
        key = (getattr(settings, "pexels_key", "") or "").strip()
    except Exception:
        key = ""
    if not key:
        return []
    try:
        r = httpx.get("https://api.pexels.com/v1/search",
                      headers={"Authorization": key},
                      params={"query": query, "per_page": limit}, timeout=15)
        r.raise_for_status()
        out = []
        for h in (r.json().get("photos") or [])[:limit]:
            url = ((h.get("src") or {}).get("medium")) or ""
            if url:
                out.append({"title": (h.get("alt") or query)[:80],
                            "thumb": url, "page": h.get("url", "")})
        return out
    except Exception:
        return []


def _commons(query: str, limit: int) -> list[dict]:
    """Wikimedia Commons file search — stable CDN, hotlink-friendly, no key.
    (loremflickr used to sit here but now returns 500 for every request.)
    Commons search is AND-strict, so over-specific phrasing (&, commas,
    'website for …') gets normalized, then retried with fewer words."""
    # strip punctuation (&, commas, quotes, unicode hyphens) → word list
    words = re.sub(r"[^\w\s]+", " ", (query or "").lower()).split()
    out: list[dict] = []
    for n in (6, 3):
        q = " ".join(words[:n])
        if not q:
            break
        try:
            r = httpx.get(API, params={
                "action": "query", "generator": "search",
                "gsrsearch": f"filetype:bitmap {q}", "gsrnamespace": 6,
                "gsrlimit": limit, "prop": "imageinfo",
                "iiprop": "url|mime", "iiurlwidth": 1280, "format": "json",
            }, headers=_UA, timeout=15)
            r.raise_for_status()
            pages = ((r.json() or {}).get("query") or {}).get("pages") or []
            if isinstance(pages, dict):
                pages = list(pages.values())
            for p in pages:
                ii = (p.get("imageinfo") or [{}])[0]
                url = ii.get("thumburl") or ii.get("url") or ""
                mime = str(ii.get("mime") or "")
                if not url or not (mime.startswith("image/")
                                   or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))):
                    continue
                title = str(p.get("title") or query)
                if title.startswith("File:"):
                    title = title[5:]
                out.append({"title": title[:80], "thumb": url,
                            "page": ii.get("descriptionurl") or ""})
                if len(out) >= limit:
                    return out
        except Exception:
            return out or []
    return out


# Hosts we ship without probing (verified serving images). pollinations is
# deliberately NOT here — it now 402s anonymously, so it gets probed once.
_KNOWN_ALIVE = ("upload.wikimedia.org", "thumb.wikimedia.org", "images.pexels.com",
                "cdn.pixabay.com", "picsum.photos", "placehold.co")
_alive_cache: dict[str, bool] = {}


def _alive(url: str) -> bool:
    """Per-host liveness probe (cached): hotlink-protected hosts (Google/Bing
    thumbs etc.) return 403 to hotlinked <img> — skip them instead of shipping
    broken images into every build."""
    try:
        import urllib.parse
        host = urllib.parse.urlsplit(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    if any(host.endswith(h) for h in _KNOWN_ALIVE):
        return True
    if host in _alive_cache:
        return _alive_cache[host]
    ok = False
    try:
        r = httpx.get(url, follow_redirects=True, timeout=6,
                      headers={**_UA, "Range": "bytes=0-0"})
        ok = r.status_code in (200, 206)
    except Exception:
        ok = False
    _alive_cache[host] = ok
    return ok


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
    """Search web images. Serper → Brave → Pixabay → Pexels → Commons → Pollinations.
    A provider only wins if its first URL actually serves an image — dead or
    hotlink-protected hosts fall through to the next one."""
    if not query:
        return []
    for fn in (_serper, _brave, _pixabay, _pexels, _commons, _pollinations):
        hit = fn(query, limit)
        if hit and _alive(hit[0].get("thumb", "")):
            return hit
    return []
