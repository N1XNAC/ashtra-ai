"""Web image search — Wikimedia Commons (free, no key, stable).

Covers objects/animals/food/places well; weak for celebrities and
current events. Returns [] on any failure so chat still replies.
"""
import re

import httpx

API = "https://commons.wikimedia.org/w/api.php"
_UA = {"User-Agent": "AshtraAI/1.0 (personal assistant; contact: local)"}


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
    """Search Commons images. Each item: {title, thumb, page}."""
    if not query:
        return []
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
