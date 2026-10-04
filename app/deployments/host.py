"""File spool shared with the host deployer that runs outside the Compose stack.

The API never touches Docker. It drops a request file that a systemd path unit on the
host picks up, and reads back the status file the deployer writes. Both sides write
atomically (temporary file plus rename), so a reader never sees a partial document.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.persistence.models import utc_now

RUN_ID_PATTERN = re.compile(r"^[0-9a-f-]{36}$")
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_STATUS_BYTES = 64_000


class HostDeploymentState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class HostDeploymentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    state: HostDeploymentState
    stage: str = Field(pattern=r"^[a-z_]{1,32}$")
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN.pattern)
    previous_sha: str | None = Field(default=None, pattern=COMMIT_SHA_PATTERN.pattern)
    started_at: datetime
    finished_at: datetime | None = None
    log_tail: str = Field(default="", max_length=20_000)


class HostDeployerSpoolError(RuntimeError):
    pass


class HostDeployerSpool:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    @property
    def requests_directory(self) -> Path:
        return self.directory / "requests"

    @property
    def status_directory(self) -> Path:
        return self.directory / "status"

    def submit(
        self,
        *,
        workflow_run_id: str,
        commit_sha: str,
        deployment_target_id: str,
    ) -> None:
        """Queue a deployment. Resubmitting a run the deployer already knows is a no-op."""
        self._require_run_id(workflow_run_id)
        if not COMMIT_SHA_PATTERN.fullmatch(commit_sha):
            raise ValueError("commit_sha must be a full lowercase Git SHA")
        request_path = self.requests_directory / f"{workflow_run_id}.json"
        if request_path.exists() or self._status_path(workflow_run_id).exists():
            return
        if not self.requests_directory.is_dir():
            raise HostDeployerSpoolError(
                f"Deploy request directory does not exist: {self.requests_directory}"
            )
        document = {
            "workflow_run_id": workflow_run_id,
            "commit_sha": commit_sha,
            "deployment_target_id": deployment_target_id,
            "requested_at": utc_now().isoformat(),
        }
        descriptor, temporary = tempfile.mkstemp(
            dir=self.requests_directory, prefix=".", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(document, handle)
            os.chmod(temporary, 0o644)
            # The deployer only matches *.json, so it never sees the temporary name.
            os.replace(temporary, request_path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def status(self, workflow_run_id: str) -> HostDeploymentStatus | None:
        self._require_run_id(workflow_run_id)
        path = self._status_path(workflow_run_id)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(raw) > MAX_STATUS_BYTES:
            raise HostDeployerSpoolError(f"Deploy status file is too large: {path}")
        try:
            status = HostDeploymentStatus.model_validate_json(raw)
        except ValidationError as error:
            raise HostDeployerSpoolError(f"Malformed deploy status {path}: {error}") from error
        if status.workflow_run_id != workflow_run_id:
            raise HostDeployerSpoolError(f"Deploy status {path} names a different run")
        return status

    def _status_path(self, workflow_run_id: str) -> Path:
        return self.status_directory / f"{workflow_run_id}.json"

    @staticmethod
    def _require_run_id(workflow_run_id: str) -> None:
        if not RUN_ID_PATTERN.fullmatch(workflow_run_id):
            raise ValueError("workflow_run_id must be a UUID")
