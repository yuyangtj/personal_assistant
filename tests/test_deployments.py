from app.deployments import DeploymentRegistry


def test_deployment_registry_loads_target() -> None:
    target = DeploymentRegistry.from_directory("deployment-targets").get(
        "personal-assistant-production"
    )
    assert target.repository_id == "personal-assistant"
    assert target.requires_approval is True


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
