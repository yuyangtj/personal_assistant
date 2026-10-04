"""Starting coding runs: one path for the console's approval and the supervisor.

A coding run is a ``coding-change`` workflow run plus the coding task that carries it
out. The task only ever works on a new branch and opens a draft pull request; merging
and deploying stay behind the approval token.
"""

from __future__ import annotations

from app.persistence.models import WorkflowRunModel
from app.repositories import RepositoryRegistry
from app.service import TaskService
from app.workflows import WorkflowService
from app.workflows.service import WorkflowRunConflictError


def start_coding_task(
    tasks: TaskService, workflows: WorkflowService, run: WorkflowRunModel
) -> WorkflowRunModel:
    """Queue the coding task for an approved run (idempotent once started)."""
    if run.task_id is not None:
        return workflows.sync_run(run.id)
    if run.status != "approved":
        raise WorkflowRunConflictError("Workflow run must be approved before it can start")
    task = tasks.create_task(
        request=str(run.workflow_input["request"]),
        required_capabilities=["coding", "pull_request_creation"],
        source_context={
            "repository_id": str(run.workflow_input["repository_id"]),
            "workflow_run_id": run.id,
        },
        chat_session_id=run.chat_session_id,
        origin_message_id=run.origin_message_id,
        external_source="workflow",
        external_key=run.id,
        work_item_space="coding",
    )
    return workflows.attach_coding_task(run.id, task_id=task.id)


def start_coding_run(
    tasks: TaskService,
    workflows: WorkflowService,
    repositories: RepositoryRegistry,
    *,
    repository_id: str,
    request: str,
    chat_session_id: str | None,
    origin_message_id: str | None,
    approved_by: str,
) -> WorkflowRunModel:
    """Propose, approve and start a run in one go, recording who decided to start it."""
    repository = repositories.get(repository_id)
    run = workflows.create_run(
        workflow_id="coding-change",
        workflow_input={"repository_id": repository.id, "request": request.strip()},
        chat_session_id=chat_session_id,
        origin_message_id=origin_message_id,
    )
    run = workflows.decide(run.id, approve=True, by=approved_by)
    return start_coding_task(tasks, workflows, run)
