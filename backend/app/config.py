from pydantic_settings import BaseSettings
from pydantic import model_validator

class Settings(BaseSettings):
    app_name: str = "Ashtra AI"
    database_url: str = "sqlite:///./ashtray_dev.db"

    @model_validator(mode="after")
    def ensure_database_url(self) -> "Settings":
        if not self.database_url:
            self.database_url = "sqlite:///./ashtray_dev.db"
        return self
    # Phase 1: pluggable AI. Set OPENAI_BASE_URL + OPENAI_API_KEY for real model,
    # else falls back to echo adapter (local dev, no key needed).
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # Free open-source cloud model (Groq free dev tier, no card needed).
    # Get a key at https://console.groq.com and set GROQ_API_KEY.
    # Serves open-weight models: gpt-oss-20b (Apache 2.0), llama-3.3-70b, qwen3.x.
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    # Vision link of the describe-then-chat pipeline: sees the image, writes
    # text context for the text brain above. Any OpenAI-compatible vision model.
    groq_vision_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    vision_max_mb: int = 5
    # Vision override: point the vision link at any OpenAI-compatible vision
    # endpoint (e.g. a free Google AI Studio / OpenRouter key). Empty = reuse
    # the main chain (custom OpenAI endpoint, else Groq).
    vision_base_url: str = ""
    vision_api_key: str = ""
    # Comma-separated fallback chain: tried in order, so one hot quota
    # doesn't kill the feature. Keep entries to well-behaved (non-reasoning) models.
    vision_model: str = ""
    secret_key: str = "dev-only-change-me"
    # --- Safety: exposure + abuse protection ---
    # Bind uvicorn to 127.0.0.1 (never 0.0.0.0 on a dev box) and keep CORS tight.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Shared secret for API clients. Empty = open (ok on localhost).
    # Set API_KEY and browser clients must send it as X-API-Key header.
    api_key: str = ""
    # Abuse caps (sliding window, per minute). 429 + Retry-After when exceeded.
    rate_limit_per_minute: int = 60      # global, per client IP
    chat_rate_limit_per_minute: int = 20  # POST /chat, per user_id (LLM spend)
    max_message_length: int = 4000      # hard cap on chat input chars
    docs_enabled: bool = True           # set DOCS_ENABLED=0 to hide /docs + /openapi.json
    # Phase 5: local custom model. Chain is OpenAI → local checkpoint → echo.
    # Set LOCAL_MODEL_ENABLED=0 to skip local inference.
    local_model_enabled: bool = True
    local_model_dir: str = "./local_model"
    # Phase 6: knowledge graph. Dev default = local JSON (no server).
    # Prod Neo4j: set NEO4J_URI (+ NEO4J_USER/NEO4J_PASSWORD) + `pip install neo4j`.
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    class Config:
        env_file = ".env"

settings = Settings()
