import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.deployments import (
    DeploymentRegistry,
    DeploymentStrategy,
    DeploymentTarget,
    HostDeployerSpool,
    HostDeployerSpoolError,
    HostDeploymentState,
)

RUN_ID = "0f8d3c1e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


def test_deployment_registry_loads_targets() -> None:
    registry = DeploymentRegistry.from_directory("deployment-targets")
    target = registry.get("personal-assistant-production")
    assert target.repository_id == "personal-assistant"
    assert target.requires_approval is True
    assert target.strategy == DeploymentStrategy.HOST_DEPLOYER
    assert target.workflow_file is None

    fallback = registry.get("personal-assistant-production-actions")
    assert fallback.strategy == DeploymentStrategy.GITHUB_ACTIONS
    assert fallback.workflow_file == "deploy.yml"
    assert fallback.enabled is False


def test_deployment_strategy_controls_workflow_file() -> None:
    base = {
        "id": "example",
        "name": "Example",
        "repository_id": "personal-assistant",
        "environment": "production",
    }
    with pytest.raises(ValidationError, match="require workflow_file"):
        DeploymentTarget.model_validate({**base, "strategy": "github_actions"})
    with pytest.raises(ValidationError, match="must not set workflow_file"):
        DeploymentTarget.model_validate(
            {**base, "strategy": "host_deployer", "workflow_file": "deploy.yml"}
        )


def _spool(tmp_path: Path) -> HostDeployerSpool:
    spool = HostDeployerSpool(tmp_path)
    spool.requests_directory.mkdir()
    spool.status_directory.mkdir()
    return spool


def _write_status(spool: HostDeployerSpool, **overrides) -> None:
    document = {
        "workflow_run_id": RUN_ID,
        "state": "rolled_back",
        "stage": "health_check",
        "commit_sha": "a" * 40,
        "previous_sha": "b" * 40,
        "started_at": "2026-10-04T12:00:00+00:00",
        "finished_at": "2026-10-04T12:02:00+00:00",
        "log_tail": "health check failed",
        **overrides,
    }
    (spool.status_directory / f"{RUN_ID}.json").write_text(json.dumps(document))


def test_spool_submit_writes_request_once(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    spool.submit(
        workflow_run_id=RUN_ID,
        commit_sha="a" * 40,
        deployment_target_id="personal-assistant-production",
    )
    request_path = spool.requests_directory / f"{RUN_ID}.json"
    first = json.loads(request_path.read_text())
    assert first["commit_sha"] == "a" * 40
    assert [path.name for path in spool.requests_directory.iterdir()] == [f"{RUN_ID}.json"]

    spool.submit(
        workflow_run_id=RUN_ID,
        commit_sha="a" * 40,
        deployment_target_id="personal-assistant-production",
    )
    assert json.loads(request_path.read_text()) == first


def test_spool_submit_is_noop_after_deployer_reported(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    _write_status(spool)
    spool.submit(
        workflow_run_id=RUN_ID,
        commit_sha="a" * 40,
        deployment_target_id="personal-assistant-production",
    )
    assert list(spool.requests_directory.iterdir()) == []


def test_spool_rejects_unsafe_identifiers(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    with pytest.raises(ValueError, match="UUID"):
        spool.submit(
            workflow_run_id="../escape",
            commit_sha="a" * 40,
            deployment_target_id="personal-assistant-production",
        )
    with pytest.raises(ValueError, match="full lowercase"):
        spool.submit(
            workflow_run_id=RUN_ID,
            commit_sha="main",
            deployment_target_id="personal-assistant-production",
        )


def test_spool_submit_requires_request_directory(tmp_path: Path) -> None:
    with pytest.raises(HostDeployerSpoolError, match="does not exist"):
        HostDeployerSpool(tmp_path / "missing").submit(
            workflow_run_id=RUN_ID,
            commit_sha="a" * 40,
            deployment_target_id="personal-assistant-production",
        )


def test_spool_reads_status(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    assert spool.status(RUN_ID) is None
    _write_status(spool)
    status = spool.status(RUN_ID)
    assert status is not None
    assert status.state == HostDeploymentState.ROLLED_BACK
    assert status.previous_sha == "b" * 40


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "exploded"},
        {"commit_sha": "main"},
        {"workflow_run_id": "1f8d3c1e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"},
        {"unexpected": True},
    ],
)
def test_spool_rejects_malformed_status(tmp_path: Path, overrides) -> None:
    spool = _spool(tmp_path)
    _write_status(spool, **overrides)
    with pytest.raises(HostDeployerSpoolError):
        spool.status(RUN_ID)


def test_deployment_workflow_requires_immutable_sha(client) -> None:
    response = client.post(
        "/workflow-runs",
        json={
            "workflow_id": "assistant-deployment",
            "input": {
                "deployment_target_id": "personal-assistant-production",
                "commit_sha": "main",
            },
        },
    )
    assert response.status_code == 422
    assert "immutable lowercase Git SHA" in response.json()["detail"]


def test_deployment_workflow_resolves_trusted_target(client) -> None:
    response = client.post(
        "/workflow-runs",
        json={
            "workflow_id": "assistant-deployment",
            "input": {
                "deployment_target_id": "personal-assistant-production",
                "commit_sha": "a" * 40,
            },
        },
    )
    assert response.status_code == 201
    assert response.json()["input"]["repository_id"] == "personal-assistant"


def test_deployment_start_rejects_commit_that_is_not_current_main(client, tmp_path) -> None:
    client.app.state.approval_token = "approval-secret"
    client.app.state.workflow_service.host_deployer = _spool(tmp_path)
    created = client.post(
        "/workflow-runs",
        json={
            "workflow_id": "assistant-deployment",
            "input": {
                "deployment_target_id": "personal-assistant-production",
                "commit_sha": "a" * 40,
            },
        },
    ).json()
    client.post(
        f"/workflow-runs/{created['id']}/decision",
        headers={"X-Assistant-Approval-Token": "approval-secret"},
        json={"decision": "approve"},
    )

    class GitHub:
        def get_branch_head(self, **_kwargs) -> str:
            return "b" * 40

        def dispatch_workflow(self, **_kwargs) -> None:
            raise AssertionError("stale commit must not be dispatched")

    client.app.state.github_client = GitHub()
    started = client.post(f"/workflow-runs/{created['id']}/start")

    assert started.status_code == 409
    assert "exact head" in started.json()["detail"]
    assert list((tmp_path / "requests").iterdir()) == []
