from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_SessionLocal: sessionmaker[Session] | None = None


def _normalize_database_url(database_url: str) -> str:
    """
    Normalize DB URLs for SQLAlchemy and enforce SSL for hosted Postgres (Render).

    - Accepts Render-style URLs: postgresql://... or postgres://...
    - Converts to SQLAlchemy psycopg driver: postgresql+psycopg://...
    - Ensures sslmode=require if not explicitly provided.
    """
    raw = database_url.strip()
    if not raw:
        raise ValueError("database_url must be non-empty when provided")

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+psycopg"

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "sslmode" not in {k.lower(): v for k, v in query.items()}:
        # Render Postgres requires SSL by default; enforce if user didn't specify.
        query["sslmode"] = "require"

    return urlunparse(
        parsed._replace(
            scheme=scheme,
            query=urlencode(query, doseq=True),
        )
    )


def init_db(database_url: str | None, db_path: str) -> None:
    global _SessionLocal
    if database_url:
        url = _normalize_database_url(database_url)
        engine = create_engine(url, future=True, pool_pre_ping=True)
        is_sqlite = False
    else:
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        is_sqlite = True

    # We return ORM objects from short-lived sessions (services layer).
    # Without this, attributes may expire on commit and then raise DetachedInstanceError.
    _SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )

    # Import here to avoid circular import at module import time.
    from app.db.models import Base  # noqa: WPS433

    Base.metadata.create_all(bind=engine)
    if is_sqlite:
        _migrate_schema(engine)


def _migrate_schema(engine) -> None:
    """
    Lightweight, idempotent schema migrations for sqlite.

    This project does not use Alembic. We maintain backwards compatibility for existing
    local DBs by applying additive migrations at startup.
    """
    with engine.connect() as conn:
        # users.language (EN/RU)
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)").fetchall()}
        if "language" not in cols:
            conn.exec_driver_sql("ALTER TABLE users ADD COLUMN language VARCHAR(8) NOT NULL DEFAULT 'en'")
            conn.commit()


@contextmanager
def get_session() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


