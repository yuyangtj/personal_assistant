from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import select

from app.domain.enums import EventType
from app.persistence.database import Database
from app.persistence.models import CodingRunModel, TaskModel, utc_now
from app.persistence.repository import TaskRepository


class CodingRunPhase(StrEnum):
    PREPARING = "preparing"
    AGENT_RUNNING = "agent_running"
    VALIDATED = "validated"
    VALIDATION_FAILED = "validation_failed"
    COMMITTED = "committed"
    PUSHED = "pushed"
    PR_CREATED = "pr_created"


class CodingRunStore:
    def __init__(self, database: Database):
        self.database = database

    def get(self, task_id: str) -> CodingRunModel | None:
        with self.database.session() as session:
            return session.get(CodingRunModel, task_id)

    def start(
        self,
        *,
        task_id: str,
        repository_id: str,
        github_repository: str,
        branch: str,
    ) -> CodingRunModel:
        with self.database.session() as session, session.begin():
            existing = session.get(CodingRunModel, task_id, with_for_update=True)
            if existing is not None:
                if (
                    existing.repository_id != repository_id
                    or existing.github_repository != github_repository
                    or existing.branch != branch
                ):
                    raise ValueError("Coding checkpoint does not match the trusted repository")
                self._task_event(
                    session,
                    task_id,
                    EventType.CODING_RECONCILED,
                    {"phase": existing.phase},
                )
                return existing
            model = CodingRunModel(
                task_id=task_id,
                repository_id=repository_id,
                github_repository=github_repository,
                branch=branch,
                phase=CodingRunPhase.PREPARING.value,
                validation_results=[],
                runner_attempts=[],
            )
            session.add(model)
            session.flush()
            self._event(session, task_id, model.phase, recovered=False)
            return model

    def update(self, task_id: str, phase: CodingRunPhase, **values: Any) -> CodingRunModel:
        allowed = {
            "base_sha",
            "commit_sha",
            "pull_request_number",
            "pull_request_url",
            "pull_request_head_sha",
            "validation_results",
            "runner_attempts",
            "report",
            "last_error",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported coding checkpoint fields: {sorted(unknown)}")
        with self.database.session() as session, session.begin():
            model = session.get(CodingRunModel, task_id, with_for_update=True)
            if model is None:
                raise LookupError(task_id)
            recovered = model.phase == phase.value
            model.phase = phase.value
            for name, value in values.items():
                setattr(model, name, value)
            model.updated_at = utc_now()
            self._event(session, task_id, phase.value, recovered=recovered)
            if phase in {
                CodingRunPhase.VALIDATED,
                CodingRunPhase.VALIDATION_FAILED,
            }:
                self._task_event(
                    session,
                    task_id,
                    (
                        EventType.VALIDATION_SUCCEEDED
                        if phase == CodingRunPhase.VALIDATED
                        else EventType.VALIDATION_FAILED
                    ),
                    {
                        "steps": [
                            {
                                "id": result.get("id"),
                                "passed": result.get("passed"),
                                "required": result.get("required"),
                            }
                            for result in values.get("validation_results", [])
                            if isinstance(result, dict)
                        ]
                    },
                )
            session.flush()
            return model

    @staticmethod
    def _event(session, task_id: str, phase: str, *, recovered: bool) -> None:
        CodingRunStore._task_event(
            session,
            task_id,
            EventType.CODING_CHECKPOINT,
            {"phase": phase, "reconciled": recovered},
        )

    @staticmethod
    def _task_event(session, task_id: str, event_type: EventType, payload: dict[str, Any]) -> None:
        task = session.scalar(select(TaskModel).where(TaskModel.id == task_id).with_for_update())
        if task is not None:
            TaskRepository.append_event(session, task, event_type, payload)
