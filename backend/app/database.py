from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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
