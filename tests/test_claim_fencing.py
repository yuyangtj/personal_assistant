from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.enums import ExecutionStatus, TaskStatus
from app.persistence.database import Database
from app.persistence.models import ExecutionModel, TaskModel
from app.services import TaskService


def _expire(database: Database, task_id: str) -> None:
    with database.session() as session, session.begin():
        task = session.get(TaskModel, task_id)
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)


def _start(service: TaskService, task_id: str, claim_id: str) -> str:
    execution = service.start_execution(
        task_id,
        capability_id="model-conversation",
        executor_id="model-conversation",
        execution_input={},
        claim_id=claim_id,
    )
    assert execution is not None
    return execution


def test_stale_worker_cannot_mutate_reclaimed_task(
    service: TaskService, database: Database
) -> None:
    task = service.create_task(request="Do durable work")
    first = service.claim_next_task(worker_id="worker-a", lease_seconds=30)
    assert first is not None and first.claim_id is not None
    first_execution = _start(service, task.id, first.claim_id)

    _expire(database, task.id)
    assert service.recover_expired_tasks() == [task.id]
    second = service.claim_next_task(worker_id="worker-b", lease_seconds=30)
    assert second is not None and second.claim_id is not None
    assert second.claim_id != first.claim_id
    second_execution = _start(service, task.id, second.claim_id)

    assert not service.renew_lease(
        task.id,
        worker_id="worker-a",
        claim_id=first.claim_id,
        lease_seconds=30,
    )
    assert service.is_cancelled(task.id, claim_id=first.claim_id)
    assert not service.start_validation(
        task.id,
        first_execution,
        output={"summary": "stale"},
        claim_id=first.claim_id,
    )
    assert (
        service.complete_task(
            task.id,
            first_execution,
            reply="stale",
            claim_id=first.claim_id,
        )
        is None
    )
    assert not service.fail_task(
        task.id,
        error="stale failure",
        execution_id=first_execution,
        claim_id=first.claim_id,
    )
    assert (
        service.request_approval(
            task.id,
            first_execution,
            artifacts=[],
            approval={"number": 1},
            claim_id=first.claim_id,
        )
        is None
    )

    assert service.start_validation(
        task.id,
        second_execution,
        output={"summary": "fresh"},
        claim_id=second.claim_id,
    )
    completed = service.complete_task(
        task.id,
        second_execution,
        reply="fresh",
        claim_id=second.claim_id,
    )
    assert completed is not None
    assert completed.status == TaskStatus.COMPLETED.value
    assert completed.claim_id is None
    assert service.get_task(task.id).current_goal == task.current_goal


def test_same_worker_reclaim_uses_a_new_fence(service: TaskService, database: Database) -> None:
    task = service.create_task(request="Reclaim once")
    first = service.claim_next_task(worker_id="worker", lease_seconds=30)
    assert first is not None
    _expire(database, task.id)
    service.recover_expired_tasks()
    second = service.claim_next_task(worker_id="worker", lease_seconds=30)

    assert second is not None
    assert second.claim_id != first.claim_id
    assert not service.renew_lease(
        task.id,
        worker_id="worker",
        claim_id=first.claim_id,
        lease_seconds=30,
    )


def test_expired_lease_cannot_be_renewed_before_recovery(
    service: TaskService, database: Database
) -> None:
    task = service.create_task(request="Expire naturally")
    claimed = service.claim_next_task(worker_id="worker", lease_seconds=30)
    assert claimed is not None
    _expire(database, task.id)

    assert not service.renew_lease(
        task.id,
        worker_id="worker",
        claim_id=claimed.claim_id,
        lease_seconds=30,
    )
    assert service.is_cancelled(task.id, claim_id=claimed.claim_id)


def test_stale_plan_and_progress_are_ignored(service: TaskService, database: Database) -> None:
    task = service.create_task(request="Keep event history clean")
    first = service.claim_next_task(worker_id="worker-a", lease_seconds=30)
    assert first is not None
    _expire(database, task.id)
    service.recover_expired_tasks()
    second = service.claim_next_task(worker_id="worker-b", lease_seconds=30)
    assert second is not None
    events_before = [event.id for event in service.list_events(task.id)]

    service.record_plan(task.id, ["stale"], claim_id=first.claim_id)
    service.record_manager_decision(task.id, {"stale": True}, claim_id=first.claim_id)
    service.record_progress(task.id, "stale progress", "tool", claim_id=first.claim_id)

    assert [event.id for event in service.list_events(task.id)] == events_before
    service.record_plan(task.id, ["fresh"], claim_id=second.claim_id)
    assert service.list_events(task.id)[-1].event_type == "PLAN_CREATED"


def test_user_cancellation_marks_only_the_running_execution_cancelled(
    service: TaskService,
) -> None:
    task = service.create_task(request="Cancel this")
    claimed = service.claim_next_task(worker_id="worker", lease_seconds=30)
    assert claimed is not None
    execution = _start(service, task.id, claimed.claim_id)

    service.cancel_task(task.id)
    service.finish_cancelled_execution(execution)

    stored = service.get_task(task.id)
    assert stored.status == TaskStatus.CANCELLED.value
    assert stored.claim_id is None
    with service.database.session() as session:
        from app.persistence.models import ExecutionModel

        assert session.get(ExecutionModel, execution).status == ExecutionStatus.CANCELLED.value


def test_a_run_that_fails_after_its_task_was_cancelled_is_closed(
    service: TaskService, database: Database
) -> None:
    task = service.create_task(request="Do work the user then cancels")
    claimed = service.claim_next_task(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None and claimed.claim_id is not None
    execution_id = _start(service, task.id, claimed.claim_id)
    service.cancel_task(task.id)

    assert not service.fail_task(
        task.id,
        error="the executor raised after the cancel",
        execution_id=execution_id,
        claim_id=claimed.claim_id,
    )

    with database.session() as session:
        execution = session.get(ExecutionModel, execution_id)
        assert execution.status == ExecutionStatus.CANCELLED.value
        assert execution.completed_at is not None
    assert service.get_task(task.id).status == TaskStatus.CANCELLED.value
