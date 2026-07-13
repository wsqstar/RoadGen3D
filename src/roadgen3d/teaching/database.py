"""Database configuration shared by the API, migrations, and RQ workers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE_PATH = ROOT / "artifacts" / "teaching" / "roadgen3d.db"


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    configured = os.getenv("ROADGEN_DATABASE_URL", "").strip()
    if configured:
        return configured
    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE_PATH}"


class TeachingDatabase:
    def __init__(self, url: str | None = None, *, create_schema: bool = True) -> None:
        self.url = url or database_url()
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        self.engine = create_engine(self.url, pool_pre_ping=True, connect_args=connect_args)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False, class_=Session)
        if create_schema:
            from . import models  # noqa: F401

            Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self.session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

