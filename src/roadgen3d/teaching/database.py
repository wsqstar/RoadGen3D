"""Database configuration shared by the API, migrations, and RQ workers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
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
            self._ensure_compatibility_columns()

    def _ensure_compatibility_columns(self) -> None:
        """Apply the small additive migration needed by existing deployments."""

        with self.engine.begin() as connection:
            is_postgresql = connection.dialect.name == "postgresql"
            if is_postgresql:
                # API and all RQ workers initialize concurrently. Serialize
                # compatibility DDL so separate processes cannot race while
                # adding the same column or index to an existing deployment.
                connection.execute(text("SELECT pg_advisory_xact_lock(724720333)"))

            inspector = inspect(connection)
            if "users" not in inspector.get_table_names():
                return
            columns = {item["name"] for item in inspector.get_columns("users")}
            if "guest_recovery_key" not in columns:
                try:
                    if is_postgresql:
                        connection.execute(text(
                            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                            "guest_recovery_key VARCHAR(96)"
                        ))
                    else:
                        connection.execute(text("ALTER TABLE users ADD COLUMN guest_recovery_key VARCHAR(96)"))
                except OperationalError:
                    # Another SQLite process may have completed the migration.
                    refreshed = {item["name"] for item in inspect(connection).get_columns("users")}
                    if "guest_recovery_key" not in refreshed:
                        raise
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_guest_recovery_key "
                "ON users (guest_recovery_key)"
            ))

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
