"""Bring Your Own Key — user-supplied provider credentials (Groq / OpenRouter).

Keys are stored per user in `user_api_keys`, masked in every API response,
and used FIRST in the LLM chain for that user's chat/build/answer requests.
On any provider failure the request falls through to the server-side chain so
the app stays usable (last_error is recorded for the Settings panel).
"""
import logging

import httpx

from ..config import settings

_log = logging.getLogger("ashtra.byok")

# Only providers that are genuinely wired into the app (no fake integrations).
PROVIDERS: dict[str, dict] = {
    "groq": {
        "label": "Groq",
        "base_url": lambda: (settings.groq_base_url or "https://api.groq.com/openai/v1"),
        "default_model": lambda: (settings.groq_model or "openai/gpt-oss-20b"),
        "models": ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "llama-3.3-70b-versatile"],
        "docs": "https://console.groq.com/keys",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": lambda: "https://openrouter.ai/api/v1",
        "default_model": lambda: "openai/gpt-oss-120b:free",
        "models": ["openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free",
                   "meta-llama/llama-3.3-70b-instruct:free"],
        "docs": "https://openrouter.ai/keys",
    },
}


def mask_key(key: str) -> str:
    """Never expose the whole key — show only a stable mask + last 4."""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return "•" * 12 + key[-4:]


def get_row(db, user_id: str, provider: str):
    from .. import models
    return (db.query(models.UserApiKey)
            .filter_by(user_id=user_id, provider=provider).first())


def creds_for(db, user_id: str) -> list[tuple[str, str, str]]:
    """[(base_url, api_key, model)] — the user's keys, most useful first (groq)."""
    from .. import models
    out: list[tuple[str, str, str]] = []
    for provider in ("groq", "openrouter"):
        row = (db.query(models.UserApiKey)
               .filter_by(user_id=user_id, provider=provider).first())
        if not row or not row.api_key:
            continue
        info = PROVIDERS.get(provider)
        if not info:
            continue
        model = (row.model or "").strip() or info["default_model"]()
        out.append((info["base_url"](), row.api_key, model))
    return out


def record_result(db, user_id: str, provider: str, error: str | None) -> None:
    """Persist last_error on a live-test failure (never raise out)."""
    try:
        row = get_row(db, user_id, provider)
        if row:
            row.last_error = (error or "")[:300] or None
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


async def test_key(provider: str, api_key: str) -> tuple[bool, str]:
    """Live check: GET {base}/models with the key. (ok, human message)."""
    info = PROVIDERS.get(provider)
    if not info:
        return False, f"Unknown provider '{provider}' (supported: {', '.join(PROVIDERS)})."
    if not api_key or not api_key.strip():
        return False, "Enter an API key."
    url = f"{info['base_url']().rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {api_key.strip()}"})
            if r.status_code in (401, 403):
                return False, f"{info['label']} rejected this key (HTTP {r.status_code}). Check it's active."
            if r.status_code == 404:
                # some providers don't expose /models — key may still be valid
                return True, f"{info['label']} key looks valid (endpoint check limited)."
            if r.status_code >= 400:
                return False, f"{info['label']} check failed (HTTP {r.status_code})."
            return True, f"{info['label']} key is valid."
    except httpx.HTTPError as e:
        return False, f"Could not reach {info['label']} ({type(e).__name__})."
