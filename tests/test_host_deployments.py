from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.capabilities import CapabilityRegistry
from app.deployments import (
    DeploymentRegistry,
    DeploymentStrategy,
    DeploymentTarget,
    HostDeployerSpool,
)
from app.domain.enums import TaskStatus
from app.execution.fake import FakeExecutor
from app.integrations.github import GitHubWorkflowRun
from app.manager import DeterministicManager
from app.persistence.database import Database
from app.persistence.models import CodingRunModel, TaskModel
from app.repositories import RepositoryRegistry
from app.service import TaskService
from app.worker import TaskWorker
from app.workflows import (
    WorkflowIntegrationUnavailableError,
    WorkflowRegistry,
    WorkflowRunConflictError,
    WorkflowService,
)

HEAD = "b" * 40


class GitHub:
    def __init__(self, head: str = HEAD):
        self.head = head
        self.dispatched: list[dict] = []

    def get_branch_head(self, **_kwargs) -> str:
        return self.head

    def dispatch_workflow(self, **kwargs) -> None:
        self.dispatched.append(kwargs)


def _targets() -> DeploymentRegistry:
    common = {
        "repository_id": "personal-assistant",
        "environment": "production",
        "requires_approval": True,
        "enabled": True,
    }
    return DeploymentRegistry(
        [
            DeploymentTarget(
                id="host",
                name="Host",
                strategy=DeploymentStrategy.HOST_DEPLOYER,
                **common,
            ),
            DeploymentTarget(
                id="actions",
                name="Actions",
                strategy=DeploymentStrategy.GITHUB_ACTIONS,
                workflow_file="deploy.yml",
                **common,
            ),
        ]
    )


@pytest.fixture
def spool(tmp_path: Path) -> HostDeployerSpool:
    spool = HostDeployerSpool(tmp_path / "deploy")
    spool.requests_directory.mkdir(parents=True)
    spool.status_directory.mkdir()
    return spool


@pytest.fixture
def workflows(database: Database, spool: HostDeployerSpool) -> WorkflowService:
    return WorkflowService(
        database,
        WorkflowRegistry.from_directory("workflows"),
        RepositoryRegistry.from_directory("repositories"),
        _targets(),
        host_deployer=spool,
    )


def _approved_run(workflows: WorkflowService, target: str = "host", **extra) -> str:
    run = workflows.create_run(
        workflow_id="assistant-deployment",
        workflow_input={"deployment_target_id": target, "commit_sha": HEAD, **extra},
    )
    workflows.decide(run.id, approve=True)
    return run.id


def _report(spool: HostDeployerSpool, run_id: str, state: str, stage: str, **extra) -> None:
    document = {
        "workflow_run_id": run_id,
        "state": state,
        "stage": stage,
        "commit_sha": HEAD,
        "previous_sha": "c" * 40,
        "started_at": "2026-10-04T12:00:00+00:00",
        "finished_at": "2026-10-04T12:02:00+00:00",
        "log_tail": f"{state} log",
        **extra,
    }
    (spool.status_directory / f"{run_id}.json").write_text(json.dumps(document))


def _event_types(workflows: WorkflowService, run_id: str) -> list[str]:
    return [event.event_type for event in workflows.list_events(run_id)]


def _start_coding_task(service: TaskService, database: Database) -> str:
    task = service.create_task(request="Add voice login")
    with database.session() as session, session.begin():
        stored = session.get(TaskModel, task.id)
        stored.status = TaskStatus.EXECUTING.value
        session.add(
            CodingRunModel(
                task_id=task.id,
                repository_id="personal-assistant",
                github_repository="yuyangtj/personal_assistant",
                branch="assistant/voice-login",
                phase="implementing",
            )
        )
    return task.id


def test_start_refuses_while_a_coding_run_is_executing(
    workflows: WorkflowService,
    service: TaskService,
    database: Database,
    spool: HostDeployerSpool,
) -> None:
    _start_coding_task(service, database)
    run_id = _approved_run(workflows)

    with pytest.raises(WorkflowRunConflictError, match="coding run is executing"):
        workflows.start_deployment(run_id, github=GitHub())
    assert list(spool.requests_directory.iterdir()) == []


def test_force_overrides_the_coding_run_guard(
    workflows: WorkflowService,
    service: TaskService,
    database: Database,
    spool: HostDeployerSpool,
) -> None:
    _start_coding_task(service, database)
    run_id = _approved_run(workflows, force=True)

    run = workflows.start_deployment(run_id, github=GitHub())

    assert run.status == "running"
    assert (spool.requests_directory / f"{run_id}.json").exists()


def test_start_requires_a_configured_spool(database: Database) -> None:
    workflows = WorkflowService(
        database,
        WorkflowRegistry.from_directory("workflows"),
        RepositoryRegistry.from_directory("repositories"),
        _targets(),
    )
    run_id = _approved_run(workflows)

    with pytest.raises(WorkflowRunConflictError, match="spool is not configured"):
        workflows.start_deployment(run_id, github=GitHub())


def test_host_start_does_not_dispatch_github_actions(
    workflows: WorkflowService,
) -> None:
    github = GitHub()
    run_id = _approved_run(workflows)

    workflows.start_deployment(run_id, github=github)
    again = workflows.start_deployment(run_id, github=github)

    assert again.status == "running"
    assert github.dispatched == []
    assert _event_types(workflows, run_id).count("DEPLOYMENT_SUBMITTED") == 1


@pytest.mark.parametrize(
    ("state", "stage", "status", "expected_stage", "event_type"),
    [
        ("succeeded", "promote", "completed", "promote", "WORKFLOW_COMPLETED"),
        ("rolled_back", "health_check", "failed", "health_check", "DEPLOYMENT_ROLLED_BACK"),
        ("failed", "deploy", "failed", "deploy", "WORKFLOW_FAILED"),
    ],
)
def test_sync_maps_deployer_outcomes(
    workflows: WorkflowService,
    spool: HostDeployerSpool,
    state: str,
    stage: str,
    status: str,
    expected_stage: str,
    event_type: str,
) -> None:
    run_id = _approved_run(workflows)
    workflows.start_deployment(run_id, github=GitHub())

    _report(spool, run_id, "running", "deploy", finished_at=None)
    assert workflows.sync_deployment(run_id, github=None).status == "running"

    _report(spool, run_id, state, stage)
    run = workflows.sync_deployment(run_id, github=None)
    assert (run.status, run.current_stage) == (status, expected_stage)

    repeated = workflows.sync_deployment(run_id, github=None)
    assert repeated.status == status
    events = workflows.list_events(run_id)
    assert [event.event_type for event in events].count(event_type) == 1
    assert events[-1].payload["log_tail"] == f"{state} log"
    assert events[-1].payload["previous_sha"] == "c" * 40


def test_sync_rejects_status_for_another_commit(
    workflows: WorkflowService,
    spool: HostDeployerSpool,
) -> None:
    run_id = _approved_run(workflows)
    workflows.start_deployment(run_id, github=GitHub())
    _report(spool, run_id, "succeeded", "promote", commit_sha="d" * 40)

    with pytest.raises(WorkflowRunConflictError, match="different commit"):
        workflows.sync_deployment(run_id, github=None)
    assert workflows.get_run(run_id).status == "running"


def test_actions_fallback_dispatches_and_syncs(workflows: WorkflowService) -> None:
    run_id = _approved_run(workflows, target="actions")

    class ActionsGitHub(GitHub):
        def find_workflow_run(self, **kwargs) -> GitHubWorkflowRun:
            assert kwargs["display_title"] == f"Deploy {run_id}"
            return GitHubWorkflowRun(
                id=42,
                url="https://github.com/yuyangtj/personal_assistant/actions/runs/42",
                status="completed",
                conclusion="success",
                head_sha=HEAD,
                display_title=f"Deploy {run_id}",
            )

    github = ActionsGitHub()
    workflows.start_deployment(run_id, github=github)
    assert github.dispatched == [
        {
            "repository": "yuyangtj/personal_assistant",
            "workflow_file": "deploy.yml",
            "ref": "main",
            "inputs": {"commit_sha": HEAD, "workflow_run_id": run_id},
        }
    ]

    run = workflows.sync_deployment(run_id, github=github)

    assert (run.status, run.current_stage) == ("completed", "promote")
    assert _event_types(workflows, run_id)[-2:] == ["DEPLOYMENT_DISPATCHED", "WORKFLOW_COMPLETED"]


def test_actions_sync_needs_github(workflows: WorkflowService) -> None:
    run_id = _approved_run(workflows, target="actions")
    workflows.start_deployment(run_id, github=GitHub())

    with pytest.raises(WorkflowIntegrationUnavailableError):
        workflows.sync_deployment(run_id, github=None)


def test_sync_running_deployments_reports_finished_runs(
    workflows: WorkflowService,
    spool: HostDeployerSpool,
) -> None:
    finished_id = _approved_run(workflows)
    pending_id = _approved_run(workflows)
    actions_id = _approved_run(workflows, target="actions")
    for run_id in (finished_id, pending_id, actions_id):
        workflows.start_deployment(run_id, github=GitHub())
    _report(spool, finished_id, "succeeded", "promote")

    assert workflows.sync_running_deployments() == [finished_id]
    assert workflows.get_run(pending_id).status == "running"
    assert workflows.get_run(actions_id).status == "running"


def _worker(service: TaskService, workflows: WorkflowService, **options) -> TaskWorker:
    executors = {"fake": FakeExecutor(delay_seconds=0)}
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters(executors)
    return TaskWorker(
        service=service,
        manager=DeterministicManager(registry),
        executors=executors,
        worker_id="test-worker",
        workflow_service=workflows,
        **options,
    )


def test_idle_worker_completes_deployments(
    service: TaskService,
    workflows: WorkflowService,
    spool: HostDeployerSpool,
) -> None:
    run_id = _approved_run(workflows)
    workflows.start_deployment(run_id, github=GitHub())
    _report(spool, run_id, "succeeded", "promote")

    assert _worker(service, workflows).run_once() is False
    assert workflows.get_run(run_id).status == "completed"


def test_worker_throttles_and_coding_worker_skips_deployment_sync(
    service: TaskService,
    workflows: WorkflowService,
    spool: HostDeployerSpool,
) -> None:
    run_id = _approved_run(workflows)
    workflows.start_deployment(run_id, github=GitHub())

    coding_worker = _worker(service, workflows, coding_only=True)
    worker = _worker(service, workflows, deployment_sync_interval_seconds=3600)
    worker.run_once()
    _report(spool, run_id, "succeeded", "promote")

    coding_worker.run_once()
    worker.run_once()
    assert workflows.get_run(run_id).status == "running"
