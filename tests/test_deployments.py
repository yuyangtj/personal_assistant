from app.deployments import DeploymentRegistry, DeploymentStrategy


def test_deployment_registry_loads_target() -> None:
    target = DeploymentRegistry.from_directory("deployment-targets").get(
        "personal-assistant-production"
    )
    assert target.repository_id == "personal-assistant"
    assert target.requires_approval is True
    assert target.strategy == DeploymentStrategy.GITHUB_ACTIONS
    assert target.workflow_file == "deploy.yml"


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


def test_deployment_start_rejects_commit_that_is_not_current_main(client) -> None:
    client.app.state.approval_token = "approval-secret"
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
