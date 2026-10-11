"""Perplexity-lite answer engine: rewrite → retrieve → read → rerank → cite.

Yields SSE-ready events: ("phase", label), ("sources", [...]),
("delta", text), ("done", payload), ("error", detail). Never raises out —
failures become ("error", ...) events.
"""
import asyncio
import json
import logging
import re

from . import search_providers as sp
from .reader import fetch_pages
from . import ai_core

_log = logging.getLogger("ashtra.answer")

REWRITE_SYS = (
    "Rewrite the user's question into 2-3 short standalone web search "
    "queries, one per line, no numbering, no commentary."
)
GROUND_SYS = (
    "Answer the question using ONLY the sources below. Cite every factual "
    "claim with [n] using the source numbers given. If the sources do not "
    "contain the answer, say so plainly instead of guessing."
)

SEARCH_SYS = (
    "Summarize the answer using ONLY the web results below. Cite every "
    "factual claim with [n] using the source numbers given. Keep it tight. "
    "Do NOT append your own sources list — it is added automatically."
)


async def web_search_answer(query: str) -> tuple[str, list[dict]]:
    """(context_block, sources) for the /websearch chat command.

    Rewrite → parallel fan-out → fetch top-3 → BM25 rerank → top-5 grounded
    context (same recipe as the Answer view, minus threads/follow-ups).
    Never raises.
    """
    try:
        queries = await rewrite_queries(query)
    except Exception:
        queries = [query]
    if not queries:
        queries = [query]
    try:
        merged, seen = [], set()
        for got in await asyncio.gather(*(sp.web_search(q, limit=8) for q in queries[:3])):
            for s in got or []:
                if s.get("url") and s["url"] not in seen:
                    seen.add(s["url"])
                    merged.append(s)
        sources = merged[:5]
    except Exception as e:
        _log.info("web_search_answer retrieve failed (%s)", type(e).__name__)
        return "", []
    if not sources:
        return "", []
    for i, s in enumerate(sources, 1):
        s["n"] = i
    try:
        pages = await fetch_pages([s["url"] for s in sources[:3]], max_chars=100000)
    except Exception as e:
        _log.info("web_search_answer fetch failed (%s)", type(e).__name__)
        pages = []
    by_url = {p["url"]: p for p in pages}
    passages = []
    for s in sources[:3]:
        page = None
        for u, p in by_url.items():
            if u == s["url"] or u.rstrip("/") == s["url"].rstrip("/"):
                page = p
                break
        if page:
            for ch in _chunks(page["text"])[:40]:
                passages.append({"source": s["n"], "text": ch})
    if not passages:
        for s in sources:
            if s.get("snippet"):
                passages.append({"source": s["n"], "text": s["snippet"]})
    passages = passages[:200]
    if not passages:
        return "", []
    ranked = await rerank(query, passages)
    ctx_lines = []
    for p in ranked[:5]:
        s = sources[p["source"] - 1]
        ctx_lines.append(f"[{p['source']}] {s['title']} — {s['url']}\n{p['text'][:1500]}")
    prompt = f"Question: {query}\n\nWeb results:\n" + "\n\n".join(ctx_lines)
    return prompt, [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in sources]


def _lines(text: str, limit: int = 3) -> list[str]:
    out = []
    for raw in (text or "").split("\n"):
        line = re.sub(r"^[\d.)\-\*\s]+", "", raw).strip().strip('"').strip()
        if len(line) >= 3:
            out.append(line)
        if len(out) >= limit:
            break
    return out


async def rewrite_queries(question: str) -> list[str]:
    try:
        out = await ai_core.generate_reply(
            f"{REWRITE_SYS}\n\nQuestion: {question}")
        qs = _lines(out)
        if qs:
            return qs
    except Exception as e:
        _log.info("rewrite failed (%s)", type(e).__name__)
    return [question]


def _chunks(text: str, size: int = 1000) -> list[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > size:
            chunks.append(cur)
            cur = ""
        cur = (cur + "\n\n" + p).strip() if cur else p
    if cur:
        chunks.append(cur)
    return chunks


async def rerank(question: str, passages: list[dict]) -> list[dict]:
    """Best passages first (BM25 lexical rank — deterministic, no model needed).

    The embedding fallback in this repo is a hash trick too blunt to separate
    a perfect passage from generic ones, so rank with BM25 term weights: rare
    query terms (names, years) dominate, boilerplate scores ~zero.
    """
    import math
    if not passages:
        return []
    stop = {"who", "what", "when", "where", "how", "why", "which", "the", "a",
            "an", "of", "in", "on", "and", "or", "to", "for", "is", "are", "was",
            "were", "be", "by", "with", "that", "this", "it", "as", "at", "from"}
    docs = [re.findall(r"[a-z0-9]+", p["text"][:2000].lower()) for p in passages]
    qterms = [t for t in re.findall(r"[a-z0-9]+", question.lower()) if t not in stop]
    if not qterms:
        return passages
    n = len(docs)
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    avgdl = sum(len(d) for d in docs) / max(n, 1) or 1.0
    k1, b = 1.2, 0.75
    scored = []
    for d in docs:
        tf: dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        dl = len(d)
        score = 0.0
        for t in qterms:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scored.append(score)
    order = sorted(range(n), key=lambda i: -scored[i])
    return [passages[i] for i in order]


async def _retrieve(query: str, focus: str) -> list[dict]:
    """One query → merged deduped sources for the focus mode."""
    jobs = []
    if focus in ("web", "all"):
        jobs.append(sp.web_search(query, limit=8))
    if focus in ("academic", "all"):
        jobs.append(sp.academic_search(query, limit=8))
    if not jobs:
        jobs.append(sp.web_search(query, limit=8))
    out, seen = [], set()
    for got in await asyncio.gather(*jobs):
        for s in got or []:
            if s.get("url") and s["url"] not in seen:
                seen.add(s["url"])
                out.append(s)
    return out


async def run_answer(question: str, focus: str, db, user_id: str):
    """Async generator of (kind, value) events. db used only for the final save."""
    question = (question or "").strip()
    if not question:
        yield ("error", "Ask a question first.")
        return
    if focus not in ("web", "academic", "all"):
        focus = "all"

    yield ("phase", "understanding")
    queries = await rewrite_queries(question)
    _log.info("answer rewrite → %d queries", len(queries))

    yield ("phase", "searching")
    merged, seen = [], set()
    for got in await asyncio.gather(*(_retrieve(q, focus) for q in queries)):
        for s in got:
            if s["url"] not in seen:
                seen.add(s["url"])
                merged.append(s)
    sources = merged[:5]
    if not sources:
        yield ("error", "No sources found — try rephrasing, or add a search key in .env.")
        return
    for i, s in enumerate(sources, 1):
        s["n"] = i
    yield ("sources", [{"n": s["n"], "title": s["title"], "url": s["url"],
                        "snippet": s.get("snippet", "")[:300]} for s in sources])

    yield ("phase", "reading")
    pages = await fetch_pages([s["url"] for s in sources[:3]], max_chars=100000)
    by_url = {p["url"]: p for p in pages}
    passages = []
    for s in sources[:3]:
        page = None
        for u, p in by_url.items():
            if u == s["url"] or u.rstrip("/") == s["url"].rstrip("/"):
                page = p
                break
        if page:
            for ch in _chunks(page["text"])[:40]:
                passages.append({"source": s["n"], "text": ch})
    if not passages:
        # fetch failed everywhere — rerank/answer from snippets instead
        for s in sources[:5]:
            if s.get("snippet"):
                passages.append({"source": s["n"], "text": s["snippet"]})
    passages = passages[:200]
    if not passages:
        yield ("error", "Sources found but nothing readable — try rephrasing.")
        return

    ranked = await rerank(question, passages)
    top = ranked[:5]
    ctx_lines = []
    for p in top:
        s = sources[p["source"] - 1]
        ctx_lines.append(f"[{p['source']}] {s['title']} — {s['url']}\n{p['text'][:1500]}")
    prompt = (f"{GROUND_SYS}\n\nQuestion: {question}\n\nSources:\n"
              + "\n\n".join(ctx_lines))

    yield ("phase", "writing")
    parts: list[str] = []
    try:
        # Grounded answers need content, not chain-of-thought: pin reasoning
        # low so the token budget goes to the answer (high reasoning on a big
        # prompt burns the whole budget silently → empty stream).
        async for chunk in ai_core.generate_reply_stream(prompt, deep_thinking=True,
                                                         reasoning="low"):
            parts.append(chunk)
            yield ("delta", chunk)
    except Exception as e:
        _log.info("answer stream failed (%s)", type(e).__name__)
        if not parts:
            yield ("error", "Generation failed — try again shortly.")
            return
    answer = "".join(parts).strip()
    if not answer:
        yield ("error", "Empty answer — try again shortly.")
        return

    followups: list[str] = []
    try:
        fq = await ai_core.generate_reply(
            "Suggest 3 short follow-up questions about this topic, one per line, "
            f"no numbering.\n\nTopic: {question}\nAnswer: {answer[:600]}")
        followups = _lines(fq)
    except Exception:
        pass

    thread_id = None
    try:
        from .. import models
        row = models.AnswerThread(user_id=user_id, question=question, answer=answer,
                                  sources=json.dumps(sources), followups=json.dumps(followups),
                                  focus=focus)
        db.add(row)
        db.commit()
        db.refresh(row)
        thread_id = row.id
    except Exception as e:
        _log.info("thread save failed (%s)", type(e).__name__)
        try:
            db.rollback()
        except Exception:
            pass

    yield ("done", {"answer": answer, "sources": sources, "followups": followups,
                    "thread_id": thread_id, "focus": focus})
