# Ashtra AI — Phase 6 (Advanced Companion)

Master build. Full roadmap complete (P1–P6) per DEVELOPMENT_PHASES.txt.

- Frontend: React + TS + Vite (Tailwind/shadcn land next)
- Backend: FastAPI + SQLAlchemy + SQLite (Postgres in prod)
- AI ✅: OpenAI-compatible → **local custom transformer** → echo fallback
  - Miniature decoder-only transformer (tokenizer, embeddings, 2× attention
    blocks, output head) with manual backprop + Adam — gradient-checked 1e-9
  - NumPy implementation (PyTorch ships no wheels for this machine; shapes and
    checkpoint semantics mirror a torch port — swap-in documented below)
  - Learning loop: POST /feedback → /model/train → /model/fine-tune →
    /model/status (perplexity) → served via /model/generate + chat [local 🧠]

- Memory ✅: dual SQL (truth) + Qdrant vector index (embedded local `./qdrant_data`, no server needed)
  - Embeddings: OpenAI → sentence-transformers → hashed fallback (auto)
  - Pipeline: extract → importance score → store → retrieve (top-5 into prompt)
  - Profile auto-update: interests/goals/skills from chat signals
- Agents ✅: Request → Planner → Tool Selection → Execution → Response
  - Tools: calculator (safe AST eval), notes, files (sandboxed `./uploads/{user}`),
    code analysis (AST), web search (keyless DDG), calendar/reminders
  - Chat auto-detects intents; `POST /agent/run` for direct use
  - Automation lite: pending reminders join short-message context

## Run backend
```
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
# → http://localhost:8000/health , docs at /docs
```

Optional LLM (.env in backend/, see .env.example):
```
# Free open-source cloud model — Groq free tier, no card needed.
# Key at https://console.groq.com, then restart backend.
GROQ_API_KEY=gsk-...
GROQ_MODEL=openai/gpt-oss-20b
# Or any OpenAI-compatible endpoint (takes priority over Groq):
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini
```

## Run frontend
```
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

## API (Phase 1–4)
- POST /chat {user_id, conversation_id?, message} → {conversation_id, reply, sources[], adaptation{}, adaptations_made[], tool_calls[]}
- GET /chat/conversations/{user} → recent chats (sidebar)
- GET /chat/conversations/{user}/{id} → full message history
- DELETE /chat/conversations/{user}/{id}
- GET/POST /memories, DELETE /memories/{id}, GET /memories/{user}/export
- POST /memories/search {user_id, query, top_k} → semantic hits
- POST /memories/{user}/reindex → rebuild vector index from SQL
- GET/PATCH /profile/{user_id}, GET /profile/{user}/model
- POST /agent/run {user_id, message} → {reply, steps[], tool_calls[]}
  (tools: calc, notes, files, code, search, calendar, goal_create/list/done/progress)
- GET /notes/{user}, DELETE /notes/{id}
- GET/POST /events/{user}, POST /events/{id}/done
- POST /files/{user}/upload, GET /files/{user}, POST /search {user_id, query}
- GET /graph/{user}, POST /graph/{user}/rebuild (Neo4j in prod via NEO4J_URI)
- GET/POST /goals/{user}, PATCH /goals/{id}, GET /goals/{user}/dashboard
- POST /feedback, GET /model/dataset, POST /model/train + /model/fine-tune,
  GET /model/status, POST /model/generate
- POST /learn/consolidate?user_id=&days=, GET /learn/digest?user_id=
- Try: "calculate 12*8", "take a note …", "remind me …", "what is on my plate?",
  "add a goal …", "my goals", "set goal X to 40%", "mark goal X done",
  "search for …", upload then "analyze file x.py"

Upgrade semantics: set OPENAI_BASE_URL+KEY (.env) or `pip install sentence-transformers`.
Prod memory: Postgres with pgvector (see backend/.env). Set DATABASE_URL to a Postgres URL
and `CREATE EXTENSION vector;` once, then `POST /memories/{user}/reindex` to backfill.
Privacy (DATA_PRIVACY_RULES.txt): memories are transparent, soft-delete, exportable.

## Host it free (Fly.io backend + Vercel frontend)
Backend keeps SQLite + Qdrant on a persistent volume, so memories survive.
```
# 1. backend → Fly.io
cd backend
fly launch          # pick a name, reuse fly.toml, skip Postgres/Redis
fly volumes create ashtra_data --size 1
fly secrets set GROQ_API_KEY=gsk-... VISION_API_KEY=sk-or-... \
  API_KEY=<long-random> SECRET_KEY=<long-random> \
  CORS_ORIGINS=https://<your-app>.vercel.app
fly deploy          # → https://ashtra-backend.fly.dev/health

# 2. frontend → Vercel or Netlify (both free)
# Vercel: import repo, root = frontend. Netlify: import repo, netlify.toml
# at repo root already handles base/publish/redirects.
# env (either platform):
# VITE_API_URL=https://ashtra-backend.fly.dev VITE_API_KEY=<same API_KEY>
```
Notes: first request after idle takes ~30–60s (cold start on shared CPU).

## Safety (anti-abuse)
- Binds to `127.0.0.1` only — not reachable from the network. Never use `--host 0.0.0.0` on a dev box.
- CORS locked to `http://localhost:5173` + `http://127.0.0.1:5173` (no `*`).
- Rate limits (per minute, sliding window): 60/IP globally, 20/user on POST /chat → `429 + Retry-After`.
- Input caps: chat message 1..4000 chars (`422` if violated), user_id ≤ 128 chars.
- Optional shared secret: set `API_KEY` in `backend/.env`; browser must send it as
  `X-API-Key` (frontend: `VITE_API_KEY` in `frontend/.env`). `/health` stays public.
- Security headers on every response; `/health` never leaks keys.
- `DOCS_ENABLED=0` hides `/docs` + `/openapi.json`. See `backend/.env.example`.
Next: none — full roadmap P1–P6 complete.
