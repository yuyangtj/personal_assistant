from fastapi.testclient import TestClient


def test_create_get_list_and_cancel_task(client: TestClient) -> None:
    created_response = client.post(
        "/tasks",
        json={"request": "Research database hosting", "goal": "Compare three options"},
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["status"] == "created"
    assert created["current_goal"] == "Compare three options"

    fetched = client.get(f"/tasks/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    listed = client.get("/tasks", params={"status": "created"})
    assert listed.status_code == 200
    assert [task["id"] for task in listed.json()["tasks"]] == [created["id"]]

    cancelled = client.post(f"/tasks/{created['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    events = client.get(f"/tasks/{created['id']}/events").json()["events"]
    assert [event["event_type"] for event in events] == [
        "TASK_CREATED",
        "ASSISTANT_REPLY",
        "TASK_CANCELLED",
    ]
    assert events[1]["payload"]["text"] == "Okay, I've stopped working on that."


def test_external_key_makes_creation_idempotent(client: TestClient) -> None:
    payload = {
        "request": "Handle a Slack message",
        "external_source": "slack",
        "external_key": "Ev123",
    }
    first = client.post("/tasks", json=payload)
    second = client.post("/tasks", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/tasks").json()["tasks"]) == 1


def test_missing_task_returns_404(client: TestClient) -> None:
    assert client.get("/tasks/missing").status_code == 404
    assert client.post("/tasks/missing/cancel").status_code == 404
    assert client.get("/tasks/missing/events").status_code == 404


def test_health_checks_database(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_capabilities_include_enabled_and_planned_adapters(client: TestClient) -> None:
    response = client.get("/capabilities")
    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert [capability["id"] for capability in capabilities] == [
        "fake-executor",
        "kimi-code",
        "kimi-conversation",
    ]
    assert capabilities[0]["availability"]["enabled"] is True
    assert capabilities[1]["availability"]["enabled"] is False
    assert capabilities[2]["availability"]["enabled"] is True

    enabled = client.get("/capabilities", params={"include_disabled": False}).json()
    assert [capability["id"] for capability in enabled["capabilities"]] == [
        "fake-executor",
        "kimi-conversation",
    ]


def test_task_accepts_explicit_capability_requirements(client: TestClient) -> None:
    response = client.post(
        "/tasks",
        json={
            "request": "Inspect a repository",
            "required_capabilities": ["repository_analysis"],
        },
    )
    assert response.status_code == 201
    assert response.json()["required_capabilities"] == ["repository_analysis"]


def test_task_rejects_invalid_capability_requirements(client: TestClient) -> None:
    response = client.post(
        "/tasks",
        json={
            "request": "Do something",
            "required_capabilities": ["Not Valid"],
        },
    )
    assert response.status_code == 422
    assert "Invalid required capability identifiers" in response.json()["detail"]
