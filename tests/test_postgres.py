from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.persistence.database import Database
from app.persistence.models import WorkflowRunModel
from app.repositories import RepositoryRegistry
from app.services import TaskNotFoundError, TaskService
from app.workflows import WorkflowService
from app.workflows.registry import WorkflowRegistry

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres() -> Iterator[tuple[Database, Database, str]]:
    owner_url = os.environ.get("ASSISTANT_TEST_POSTGRES_OWNER_URL")
    coding_url = os.environ.get("ASSISTANT_TEST_POSTGRES_CODING_URL")
    if not owner_url or not coding_url:
        pytest.skip("explicit owner and coding PostgreSQL test URLs are required")
    owner = Database(owner_url)
    coding = Database(coding_url)
    run_id = uuid4().hex
    try:
        yield owner, coding, run_id
    finally:
        with owner.session() as session, session.begin():
            session.execute(
                text("DELETE FROM workflow_runs WHERE workflow_input->>'postgres_test' = :run_id"),
                {"run_id": run_id},
            )
            session.execute(
                text("DELETE FROM tasks WHERE source_context->>'postgres_test' = :run_id"),
                {"run_id": run_id},
            )
            session.execute(
                text("DELETE FROM chat_sessions WHERE title LIKE :title"),
                {"title": f"PostgreSQL test {run_id}%"},
            )
        coding.dispose()
        owner.dispose()


def _task(service: TaskService, run_id: str, *, coding: bool, chat_id: str | None = None):
    return service.create_task(
        request="Exercise the PostgreSQL coding role",
        required_capabilities=["coding", "pull_request_creation"] if coding else [],
        source_context={
            "postgres_test": run_id,
            **({"repository_id": "personal-assistant"} if coding else {}),
        },
        chat_session_id=chat_id,
        post_request=False,
    )


def test_coding_role_cannot_read_private_tables_or_noncoding_rows(postgres) -> None:
    owner, coding, run_id = postgres
    owner_service = TaskService(owner)
    visible = _task(owner_service, run_id, coding=True)
    hidden = _task(owner_service, run_id, coding=False)
    coding_service = TaskService(coding, coding_restricted=True)

    assert coding_service.get_task(visible.id).id == visible.id
    with pytest.raises(TaskNotFoundError):
        coding_service.get_task(hidden.id)
    with coding.session() as session:
        assert (
            session.scalar(
                text("SELECT count(*) FROM tasks WHERE id IN (:visible, :hidden)"),
                {"visible": visible.id, "hidden": hidden.id},
            )
            == 1
        )
        with pytest.raises(ProgrammingError, match="permission denied"):
            session.execute(text("SELECT count(*) FROM memories"))


def test_concurrent_coding_workers_claim_each_task_once(postgres) -> None:
    owner, coding, run_id = postgres
    owner_service = TaskService(owner)
    expected = {_task(owner_service, run_id, coding=True).id for _ in range(40)}
    coding_service = TaskService(coding, coding_restricted=True)

    def claim(index: int):
        return coding_service.claim_next_task(
            worker_id=f"postgres-coding-{index % 8}",
            lease_seconds=60,
            coding_only=True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(claim, range(40)))

    ids = [task.id for task in claimed if task is not None]
    assert len(ids) == len(set(ids)) == 40
    assert set(ids) == expected
    assert all(task.claim_id for task in claimed if task is not None)


def test_restricted_task_service_completes_a_coding_task_and_posts_its_reply(postgres) -> None:
    owner, coding, run_id = postgres
    owner_service = TaskService(owner)
    chat = owner_service.create_chat_session(title=f"PostgreSQL test {run_id}")
    created = _task(owner_service, run_id, coding=True, chat_id=chat.id)
    with owner.session() as session, session.begin():
        session.add(
            WorkflowRunModel(
                id=str(uuid4()),
                workflow_id="coding-change",
                status="running",
                workflow_input={"postgres_test": run_id},
                current_stage="implement",
                task_id=created.id,
            )
        )
    coding_service = TaskService(coding, coding_restricted=True)

    claimed = coding_service.claim_next_task(
        worker_id="postgres-coding-completion",
        lease_seconds=60,
        coding_only=True,
    )
    assert claimed is not None and claimed.id == created.id
    execution = coding_service.start_execution(
        created.id,
        capability_id="coding-pull-request",
        executor_id="coding-pull-request",
        execution_input={"postgres_test": run_id},
        claim_id=claimed.claim_id,
    )
    assert execution is not None
    assert coding_service.start_validation(
        created.id,
        execution,
        output={"summary": "Implemented"},
        claim_id=claimed.claim_id,
    )
    workflows = WorkflowService(coding, WorkflowRegistry([]), RepositoryRegistry([]))
    synced = workflows.sync_for_task(created.id)
    assert synced is not None and synced.current_stage == "validate"
    completed = coding_service.complete_task(
        created.id,
        execution,
        reply="Implemented in PostgreSQL",
        claim_id=claimed.claim_id,
    )

    assert completed is not None and completed.status == "completed"
    (message,) = owner_service.list_chat_messages(chat.id)
    assert message.role == "assistant"
    assert message.content == "Implemented in PostgreSQL"
    assert message.linked_task_id == created.id


def test_a_failed_revision_on_the_coding_role_gives_the_pr_its_review_back(postgres) -> None:
    owner, coding, run_id = postgres
    owner_service = TaskService(owner)
    chat = owner_service.create_chat_session(title=f"PostgreSQL test {run_id} revision")
    parent = _task(owner_service, run_id, coding=True, chat_id=chat.id)
    claimed = owner_service.claim_next_task(
        worker_id="postgres-pr-parent", lease_seconds=60, coding_only=True
    )
    assert claimed is not None and claimed.id == parent.id
    execution = owner_service.start_execution(
        parent.id,
        capability_id="coding-pull-request",
        executor_id="coding-pull-request",
        execution_input={"postgres_test": run_id},
        claim_id=claimed.claim_id,
    )
    assert owner_service.start_validation(
        parent.id, execution, output={"summary": "Opened a PR"}, claim_id=claimed.claim_id
    )
    assert owner_service.request_approval(
        parent.id,
        execution,
        artifacts=[],
        approval={"number": 41, "expected_head_sha": "a" * 40},
        claim_id=claimed.claim_id,
    )
    revision = owner_service.create_task(
        request="Use a different icon",
        required_capabilities=["coding", "pull_request_creation"],
        source_context={
            "postgres_test": run_id,
            "repository_id": "personal-assistant",
            "revision_pull_request": 41,
        },
        parent_task_id=parent.id,
        chat_session_id=chat.id,
        post_request=False,
    )
    owner_service.supersede_pull_request_approval(parent.id, revision_task_id=revision.id)
    coding_service = TaskService(coding, coding_restricted=True)

    assert coding_service.fail_task(revision.id, error="the coding agent failed")

    restored = owner_service.get_task(parent.id)
    assert restored.status == "waiting_for_approval"
    assert restored.superseded_by_task_id is None


def test_two_workers_deliver_each_reminder_once(postgres) -> None:
    import threading
    import time

    from app.delivery import DeliveryService
    from app.persistence.models import OutboxDeliveryModel, utc_now
    from app.persistence.outbox import enqueue

    owner, _, run_id = postgres
    service = TaskService(owner)
    with owner.session() as session, session.begin():
        ids = [
            enqueue(
                session,
                kind="task",
                payload={
                    "request": f"Report {index}",
                    "source_context": {"postgres_test": run_id},
                    "post_request": False,
                },
            )
            for index in range(10)
        ]
    created: list[str] = []
    lock = threading.Lock()
    real_create = service.create_task

    def slow_create(**kwargs):
        time.sleep(0.05)  # both workers are mid-delivery at the same time
        with lock:
            created.append(kwargs["external_key"])
        return real_create(**kwargs)

    service.create_task = slow_create  # type: ignore[method-assign]
    workers = [
        DeliveryService(owner, service, start_run=lambda _s, _k: None, clock=utc_now)
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda worker: worker.run_due(limit=10), workers))

    assert sorted(created) == sorted(ids)
    with owner.session() as session:
        left = session.query(OutboxDeliveryModel).filter(OutboxDeliveryModel.id.in_(ids))
        assert left.count() == 0
