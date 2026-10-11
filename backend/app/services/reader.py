"""Fetch-and-extract for the Answer engine: top pages → clean text.

Parallel fetches under a total budget; one slow site can't stall the answer.
Never raises — failures just shrink the passage pool.
"""
import asyncio
import logging
import re
from html.parser import HTMLParser

import httpx

_log = logging.getLogger("ashtra.reader")

UA = {"User-Agent": "AshtraAI-AnswerEngine/1.0 (contact: ashtra@example.com)"}
_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside",
         "form", "button", "svg", "canvas", "iframe", "select"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("p", "li", "h1", "h2", "h3", "h4", "tr", "div", "section"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title_parts.append(data.strip())
        else:
            self.parts.append(data)


def extract_text(html: str, max_chars: int = 12000) -> tuple[str, str]:
    """(title, clean text) from raw HTML. Pure function, never raises."""
    try:
        p = _Text()
        p.feed(html[:500_000])
        title = " ".join(p.title_parts).strip()[:200]
        text = "".join(p.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        return title, text[:max_chars]
    except Exception:
        return "", ""


async def _one(c: httpx.AsyncClient, sem: asyncio.Semaphore,
               url: str, max_chars: int) -> dict | None:
    if not url.startswith(("http://", "https://")):
        return None
    async with sem:
        try:
            r = await c.get(url, headers=UA, timeout=15, follow_redirects=True)
            if r.status_code >= 400:
                return None
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
            if ctype and "html" not in ctype and "text" not in ctype:
                return None
            title, text = extract_text(r.text, max_chars)
            if len(text) < 200:
                return None
            return {"url": str(r.url), "title": title, "text": text}
        except Exception as e:
            _log.info("fetch %s failed (%s)", url[:80], type(e).__name__)
            return None


async def fetch_pages(urls: list[str], max_chars: int = 12000,
                      budget_s: float = 45.0) -> list[dict]:
    """Fetch pages in parallel. Returns [{url,title,text}] in input order."""
    sem = asyncio.Semaphore(5)

    async def _run() -> list[dict | None]:
        async with httpx.AsyncClient() as c:
            return list(await asyncio.gather(
                *(_one(c, sem, u, max_chars) for u in urls)))

    try:
        got = await asyncio.wait_for(_run(), timeout=budget_s)
    except asyncio.TimeoutError:
        _log.info("fetch_pages hit %.0fs budget", budget_s)
        return []
    out = [g for g in got if g]
    _log.info("fetch_pages %d/%d ok", len(out), len(urls))
    return out
