from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.persistence.database import Database
from app.service import TaskService


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'assistant.sqlite3'}"


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    db = Database(database_url)
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def service(database: Database) -> TaskService:
    return TaskService(database)


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    app = create_app(Settings(database_url=database_url, auto_create_schema=True))
    with TestClient(app) as test_client:
        yield test_client
    app.state.database.dispose()
