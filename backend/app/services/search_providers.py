"""Retrieval backends for the Answer engine.

Web chain: Brave (key) → Serper (key) → DuckDuckGo instant (keyless) →
DuckDuckGo HTML scrape (keyless, real ranked results) → Wikipedia (keyless).
Academic chain: OpenAlex → arXiv → Wikipedia (all free, all keyless).

Every function returns a list of {"title","url","snippet"} and NEVER raises —
an empty list just means that backend had nothing (caller falls through).
"""
import html as _html
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

from ..config import settings

_log = logging.getLogger("ashtra.search")

UA = {"User-Agent": "AshtraAI-AnswerEngine/1.0 (contact: ashtra@example.com)"}


# ---------- web: Brave ----------

async def _brave(c: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    key = (getattr(settings, "brave_key", "") or "").strip()
    if not key:
        return []
    r = await c.get("https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": min(limit, 10)},
                    headers={**UA, "X-Subscription-Token": key}, timeout=12)
    if r.status_code >= 400:
        _log.info("brave → HTTP %d", r.status_code)
        return []
    out = []
    for it in (r.json().get("results") or [])[:limit]:
        if it.get("url"):
            out.append({"title": it.get("title") or it["url"],
                        "url": it["url"],
                        "snippet": it.get("description") or ""})
    return out


# ---------- web: Serper (Google results as JSON) ----------

async def _serper(c: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    key = (getattr(settings, "serper_key", "") or "").strip()
    if not key:
        return []
    r = await c.post("https://google.serper.dev/search",
                     json={"q": query, "num": min(limit, 10)},
                     headers={**UA, "X-API-KEY": key}, timeout=12)
    if r.status_code >= 400:
        _log.info("serper → HTTP %d", r.status_code)
        return []
    out = []
    for it in (r.json().get("organic") or [])[:limit]:
        if it.get("link"):
            out.append({"title": it.get("title") or it["link"],
                        "url": it["link"],
                        "snippet": it.get("snippet") or ""})
    return out


# ---------- web: DuckDuckGo instant (keyless floor) ----------

async def _ddg(c: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    r = await c.get("https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1,
                            "skip_disambig": 1},
                    headers=UA, timeout=12)
    if r.status_code >= 400:
        return []
    j = r.json()
    out = []
    if j.get("AbstractText") and j.get("AbstractURL"):
        out.append({"title": j.get("Heading") or j["AbstractURL"],
                    "url": j["AbstractURL"], "snippet": j["AbstractText"]})
    for t in (j.get("RelatedTopics") or []):
        for cand in ([t] if "FirstURL" in t else (t.get("Topics") or [])):
            if cand.get("FirstURL") and cand.get("Text") and len(out) < limit:
                out.append({"title": (cand["Text"][:80] or cand["FirstURL"]),
                            "url": cand["FirstURL"], "snippet": cand["Text"][:400]})
    return out[:limit]


async def web_search(query: str, limit: int = 8) -> list[dict]:
    """Ranked web results. Brave → Serper → DDG → DDG-scrape → Wikipedia."""
    async with httpx.AsyncClient() as c:
        for name, fn in (("brave", _brave), ("serper", _serper), ("ddg", _ddg),
                         ("ddg-html", _ddg_html), ("wikipedia", _wikipedia)):
            try:
                out = await fn(c, query, limit)
            except Exception as e:
                _log.info("%s failed (%s)", name, type(e).__name__)
                continue
            if out:
                _log.info("web_search %s → %d results", name, len(out))
                return out
    return []


# ---------- academic: OpenAlex (45M+ works, keyless) ----------

def _openalex_abstract(inv: dict | None, max_chars: int = 600) -> str:
    if not inv:
        return ""
    try:
        pos = [(p, w) for w, ps in inv.items() for p in ps]
        words = [w for _, w in sorted(pos)]
        return " ".join(words)[:max_chars]
    except Exception:
        return ""


async def _openalex(c: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    r = await c.get("https://api.openalex.org/works",
                    params={"search": query, "per-page": min(limit, 10),
                            "mailto": "ashtra@example.com"}, headers=UA, timeout=12)
    if r.status_code >= 400:
        return []
    out = []
    for w in (r.json().get("results") or [])[:limit]:
        title = w.get("title") or "Untitled"
        url = w.get("doi") or (w.get("primary_location") or {}).get("landing_page_url") or w.get("id") or ""
        if not url:
            continue
        year = w.get("publication_year") or ""
        cited = w.get("cited_by_count") or 0
        out.append({"title": f"{title} ({year})" if year else title, "url": url,
                    "snippet": (_openalex_abstract(w.get("abstract_inverted_index")) or
                                f"Cited by {cited} works."[:120])})
    return out


# ---------- academic: arXiv (keyless Atom) ----------

async def _arxiv(c: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    r = await c.get("http://export.arxiv.org/api/query",
                    params={"search_query": f"all:{query}",
                            "start": 0, "max_results": min(limit, 10),
                            "sortBy": "relevance"},
                    headers=UA, timeout=12)
    if r.status_code >= 400:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    try:
        for e in ET.fromstring(r.text).findall("a:entry", ns)[:limit]:
            link = (e.find("a:id", ns).text or "").strip() if e.find("a:id", ns) is not None else ""
            title = " ".join((e.find("a:title", ns).text or "").split()) if e.find("a:title", ns) is not None else ""
            summ = " ".join((e.find("a:summary", ns).text or "").split()) if e.find("a:summary", ns) is not None else ""
            pub = (e.find("a:published", ns).text or "")[:4] if e.find("a:published", ns) is not None else ""
            if link and title:
                out.append({"title": f"{title} (arXiv, {pub})" if pub else title,
                            "url": link, "snippet": summ[:500]})
    except Exception as e:
        _log.info("arxiv parse failed (%s)", type(e).__name__)
        return []
    return out


# ---------- academic: Wikipedia (keyless) ----------

async def _wikipedia(c: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    r = await c.get("https://en.wikipedia.org/w/api.php",
                    params={"action": "query", "list": "search", "srsearch": query,
                            "srlimit": min(limit, 10), "format": "json"},
                    headers=UA, timeout=12)
    if r.status_code >= 400:
        return []
    out = []
    for it in ((r.json().get("query") or {}).get("search") or [])[:limit]:
        title = it.get("title") or ""
        if not title:
            continue
        url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        snippet = re.sub(r"<[^>]+>", "", it.get("snippet") or "")[:400]
        out.append({"title": f"{title} (Wikipedia)", "url": url, "snippet": snippet})
    return out


async def _ddg_html(c: httpx.AsyncClient, query: str, limit: int) -> list[dict]:
    """Keyless ranked results by scraping DDG's HTML endpoint. Fragile by
    nature (it's scraping + bot-guarded) — any parse miss just yields [],
    never raises. Must POST like the real form with a standard browser UA;
    GET/odd UAs get a 202 challenge page."""
    r = await c.post(
        "https://html.duckduckgo.com/html/", data={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/126.0.0.0 Safari/537.36",
                 "Content-Type": "application/x-www-form-urlencoded"}, timeout=12)
    if r.status_code >= 400:
        return []
    out = []
    for block in re.split(r'<div class="result[^"]*"', r.text)[1:]:
        tm = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                       block, re.S)
        if not tm:
            continue
        href = tm.group(1)
        title = _html.unescape(re.sub(r"<[^>]+>", "", tm.group(2)).strip())
        um = re.search(r"[?&]uddg=([^&]+)", href)
        url = urllib.parse.unquote(um.group(1)) if um else href
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http") or "duckduckgo.com" in url:
            continue
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        snippet = (_html.unescape(re.sub(r"<[^>]+>", "", sm.group(1)).strip())
                   [:400] if sm else "")
        if title and url:
            out.append({"title": title[:120], "url": url, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


async def academic_search(query: str, limit: int = 8) -> list[dict]:
    """Scholarly results. Fills from OpenAlex → arXiv → Wikipedia until limit."""
    out: list[dict] = []
    seen: set[str] = set()
    async with httpx.AsyncClient() as c:
        for name, fn in (("openalex", _openalex), ("arxiv", _arxiv),
                         ("wikipedia", _wikipedia)):
            if len(out) >= limit:
                break
            try:
                got = await fn(c, query, limit - len(out))
            except Exception as e:
                _log.info("%s failed (%s)", name, type(e).__name__)
                continue
            for s in got:
                if s["url"] not in seen:
                    seen.add(s["url"])
                    out.append(s)
    _log.info("academic_search → %d results", len(out))
    return out[:limit]
