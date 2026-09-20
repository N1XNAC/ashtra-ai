from fastapi import FastAPI
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from .database import Base, engine, ensure_phase3_columns
from . import models  # noqa: F401 — register tables
from .routers import chat, memory, profile, agent, model, graph, vision
from .config import settings
from .security import (
    GlobalRateLimitMiddleware, ApiKeyMiddleware, SecurityHeadersMiddleware)
from .services.ai_core import active_provider

try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"WARNING: create_all skipped (DB unreachable): {e}")
try:
    ensure_phase3_columns()
except Exception as e:
    print(f"WARNING: ensure_phase3_columns skipped (DB unreachable): {e}")
# pgvector extension (idempotent — safe on any Postgres instance)
if not engine.dialect.name == "sqlite":
    try:
        with engine.connect() as _c:
            _c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            _c.commit()
    except Exception as e:
        print(f"WARNING: pgvector extension check skipped (DB unreachable): {e}")

app = FastAPI(title="Ashtra AI — Phase 6",
              docs_url="/docs" if settings.docs_enabled else None,
              redoc_url=None,
              openapi_url="/openapi.json" if settings.docs_enabled else None)
# Safety stack: tight CORS (no "*"), global rate limit, API-key gate, headers.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(GlobalRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat.router)
app.include_router(vision.router)
app.include_router(memory.router)
app.include_router(profile.router)
app.include_router(agent.router)
app.include_router(model.router)
app.include_router(graph.router)

@app.get("/health")
def health():
    return {"status": "ok", "master": "all systems loyal", "phase": 6, "memory": "vector",
            "personalization": True, "agents": ["calculator", "notes", "files", "code", "search", "calendar",
                                               "goal_create", "goal_list", "goal_done", "goal_progress"],
            "local_model": True, "knowledge_graph": True, "goals": True, "long_term_learning": True,
            "llm": active_provider()}
