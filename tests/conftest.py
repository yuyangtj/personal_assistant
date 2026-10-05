from __future__ import annotations

import errno
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _redirect_tmpdir_if_noexec() -> None:
    # Some environments mount /tmp noexec, which breaks tests that execute
    # files created under pytest's tmp_path. Probe the default temp dir and
    # fall back to a repo-local directory that allows execution.
    candidate = Path(tempfile.gettempdir())
    probe = candidate / f".exec-probe-{os.getpid()}"
    try:
        probe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        probe.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        subprocess.run([str(probe)], check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError) as error:
        if not isinstance(error, PermissionError) and getattr(error, "errno", None) != errno.EACCES:
            raise
        fallback = Path(__file__).resolve().parent.parent / ".pytest-tmp"
        fallback.mkdir(exist_ok=True)
        os.environ["TMPDIR"] = str(fallback)
        tempfile.tempdir = None  # force gettempdir() to re-read TMPDIR
    finally:
        probe.unlink(missing_ok=True)


_redirect_tmpdir_if_noexec()

# These imports are intentionally deferred until after the tmpdir redirect above,
# so they must stay below the call (E402).
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.services import TaskService  # noqa: E402


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
