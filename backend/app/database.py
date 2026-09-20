from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import settings


def normalize_url(url: str) -> str:
    """Accept Supabase-style URLs: postgresql:// → postgresql+psycopg2://."""
    if url.startswith("postgres://"):  # legacy Heroku-style
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+" not in url.split("://")[0]:
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    if url.startswith("postgresql+psycopg2://") and "sslmode" not in url:
        url += "?sslmode=require"
    return url


DATABASE_URL = normalize_url(settings.database_url or "sqlite:///./ashtray_dev.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {"connect_timeout": 5}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# pgvector extension (idempotent — safe on any Postgres instance)
# Wrapped so a temporarily unreachable DB (e.g. IPv6-only direct URL from
# an IPv4-only host) doesn't crash the app at import time. The Supabase
# fix is to use the Shared pooler session-mode URL (IPv4).
if not DATABASE_URL.startswith("sqlite"):
    try:
        with engine.connect() as _c:
            _c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            _c.commit()
    except Exception as e:
        print(f"WARNING: pgvector extension check skipped (DB unreachable): {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def ensure_phase3_columns():
    """Lightweight SQLite/Postgres migration: add Phase-3 profile columns if missing."""
    from sqlalchemy import text
    new_cols = {
        "explanation_depth": "VARCHAR DEFAULT 'balanced'",
        "communication_format": "VARCHAR DEFAULT 'chat'",
        "teaching_style": "VARCHAR DEFAULT 'examples'",
        "expertise_level": "VARCHAR DEFAULT 'intermediate'",
        "behaviour_signals": "JSON",
    }
    with engine.connect() as conn:
        try:
            existing = {r[1] for r in conn.execute(text("PRAGMA table_info(user_profiles)"))} \
                if engine.dialect.name == "sqlite" else None
        except Exception:
            existing = None
        if existing is None:  # postgres path: try add, ignore if exists
            for col, ddl in new_cols.items():
                try:
                    conn.execute(text(f"ALTER TABLE user_profiles ADD COLUMN {col} {ddl}"))
                except Exception:
                    pass
            try:
                conn.commit()
            except Exception:
                pass
            return
        for col, ddl in new_cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE user_profiles ADD COLUMN {col} {ddl}"))
        try:
            conn.commit()
        except Exception:
            pass
