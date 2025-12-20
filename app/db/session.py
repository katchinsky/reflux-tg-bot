from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_SessionLocal: sessionmaker[Session] | None = None


def init_db(db_path: str) -> None:
    global _SessionLocal
    engine = create_engine(f"sqlite:///{db_path}", future=True)
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


