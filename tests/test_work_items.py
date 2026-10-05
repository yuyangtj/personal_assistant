from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.enums import TaskStatus
from app.persistence.database import Database
from app.persistence.models import CodingRunModel, TaskModel
from app.services import TaskService
from app.work.items import WorkItemService


def _chat(client: TestClient) -> str:
    return client.post("/chat-sessions", json={}).json()["id"]


def _message(client: TestClient, chat_id: str, content: str) -> str:
    response = client.post(f"/chat-sessions/{chat_id}/messages", json={"content": content})
    return response.json()["message"]["id"]


def test_work_items_are_created_with_unique_slugs_and_default_brief(client: TestClient) -> None:
    first = client.post("/work-items", json={"title": "Add voice login"})
    second = client.post("/work-items", json={"title": "Add  voice login", "space": "coding"})

    assert first.status_code == 201
    assert first.json()["slug"] == "add-voice-login"
    assert first.json()["space"] == "general"
    assert first.json()["status"] == "open"
    assert first.json()["brief"]["goal"] == "Add voice login"
    assert second.json()["slug"] == "add-voice-login-2"
    assert second.json()["space"] == "coding"
    assert client.get("/work-items/add-voice-login").json()["id"] == first.json()["id"]
    assert client.post("/work-items", json={"title": "x", "space": "nope"}).status_code == 422
    assert client.get("/work-items/missing").status_code == 404
    assert {space["slug"] for space in client.get("/spaces").json()["spaces"]} == {
        "general",
        "coding",
        "shopping",
    }


def test_update_requires_the_current_version_and_records_changes(client: TestClient) -> None:
    item = client.post("/work-items", json={"title": "Plan trip"}).json()

    updated = client.patch(
        f"/work-items/{item['id']}",
        json={
            "version": 1,
            "status": "active",
            "brief": {"goal": "Plan the October trip", "next_steps": ["Book hotel"]},
        },
    )
    stale = client.patch(f"/work-items/{item['id']}", json={"version": 1, "title": "Other"})

    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["brief"]["next_steps"] == ["Book hotel"]
    assert stale.status_code == 409
    summaries = [
        entry["summary"]
        for entry in client.get(f"/work-items/{item['id']}/timeline").json()["entries"]
    ]
    assert "Status: open → active" in summaries
    assert "Brief updated (goal, next_steps)" in summaries


def test_brief_entries_are_bounded(client: TestClient) -> None:
    item = client.post("/work-items", json={"title": "Bounded"}).json()

    response = client.patch(
        f"/work-items/{item['id']}",
        json={"version": 1, "brief": {"decisions": ["x"] * 13}},
    )

    assert response.status_code == 422


def test_checklist_entries_can_be_added_checked_and_removed(client: TestClient) -> None:
    item = client.post("/work-items", json={"title": "Groceries", "kind": "list"}).json()

    added = client.post(f"/work-items/{item['id']}/checklist", json={"text": "Oat milk"})
    entry = added.json()["checklist"][0]
    checked = client.patch(f"/work-items/{item['id']}/checklist/{entry['id']}", json={"done": True})
    removed = client.delete(f"/work-items/{item['id']}/checklist/{entry['id']}")

    assert added.status_code == 201
    assert checked.json()["checklist"][0]["done"] is True
    assert removed.json()["checklist"] == []
    assert removed.json()["version"] == 4
    assert client.delete(f"/work-items/{item['id']}/checklist/{entry['id']}").status_code == 404


def test_chats_can_focus_and_unfocus_work_items(client: TestClient) -> None:
    chat_id = _chat(client)
    item = client.post("/work-items", json={"title": "Voice login"}).json()

    focused = client.put(f"/chat-sessions/{chat_id}/focus/{item['slug']}")
    again = client.put(f"/chat-sessions/{chat_id}/focus/{item['id']}")
    removed = client.delete(f"/chat-sessions/{chat_id}/focus/{item['id']}")

    assert [entry["id"] for entry in focused.json()["work_items"]] == [item["id"]]
    assert len(again.json()["work_items"]) == 1
    assert removed.json()["work_items"] == []
    assert client.get("/chat-sessions/" + "0" * 36 + "/focus").status_code == 404


def test_make_this_a_task_creates_then_reuses_the_chats_work_item(client: TestClient) -> None:
    chat_id = _chat(client)
    first_message = _message(client, chat_id, "Research standing desks under 400 euro")
    second_message = _message(client, chat_id, "Also compare their warranties")
    small_talk = _message(client, chat_id, "thanks!")

    first = client.post(
        f"/chat-sessions/{chat_id}/messages/{first_message}/task",
        json={"create_work_item": True},
    ).json()
    second = client.post(
        f"/chat-sessions/{chat_id}/messages/{second_message}/task",
        json={"create_work_item": True},
    ).json()
    chat_turn = client.post(f"/chat-sessions/{chat_id}/messages/{small_talk}/task", json={}).json()

    assert first["work_item_id"] is not None
    assert second["work_item_id"] == first["work_item_id"]
    assert chat_turn["work_item_id"] is None
    focus = client.get(f"/chat-sessions/{chat_id}/focus").json()["work_items"]
    assert [item["id"] for item in focus] == [first["work_item_id"]]
    assert focus[0]["space"] == "general"
    runs = client.get(f"/work-items/{first['work_item_id']}/runs").json()["tasks"]
    assert {run["id"] for run in runs} == {first["id"], second["id"]}


def test_explicit_work_item_links_tasks_and_follow_ups_inherit_it(client: TestClient) -> None:
    item = client.post("/work-items", json={"title": "Voice login"}).json()

    task = client.post(
        "/tasks", json={"request": "Draft the login copy", "work_item_id": item["slug"]}
    ).json()
    follow_up = client.post(
        f"/tasks/{task['id']}/follow-up", json={"request": "Make it shorter"}
    ).json()

    assert task["work_item_id"] == item["id"]
    assert follow_up["work_item_id"] == item["id"]
    assert (
        client.post("/tasks", json={"request": "x", "work_item_id": "missing"}).status_code == 404
    )


def test_started_coding_workflow_joins_the_focused_item_or_creates_one(
    client: TestClient,
) -> None:
    client.app.state.approval_token = "secret"
    headers = {"X-Assistant-Approval-Token": "secret"}

    def start(chat_id: str, content: str) -> dict:
        message = _message(client, chat_id, content)
        run = client.post(
            f"/chat-sessions/{chat_id}/messages/{message}/workflow-run",
            json={"repository_id": "personal-assistant"},
        ).json()
        client.post(
            f"/workflow-runs/{run['id']}/decision", headers=headers, json={"decision": "approve"}
        )
        task_id = client.post(f"/workflow-runs/{run['id']}/start").json()["task_id"]
        return client.get(f"/tasks/{task_id}").json()

    new_chat = _chat(client)
    created = start(new_chat, "Update the toggle icon in the web console")
    item = client.get(f"/work-items/{created['work_item_id']}").json()

    focused_chat = _chat(client)
    existing = client.post(
        "/work-items",
        json={"title": "Theme polish", "space": "coding", "chat_session_id": focused_chat},
    ).json()
    joined = start(focused_chat, "Fix the dark mode contrast")

    assert item["space"] == "coding"
    assert item["links"] == [
        {
            "kind": "repository",
            "label": "personal-assistant",
            "url": None,
            "ref": "personal-assistant",
        }
    ]
    assert [
        entry["id"] for entry in client.get(f"/chat-sessions/{new_chat}/focus").json()["work_items"]
    ] == [item["id"]]
    assert joined["work_item_id"] == existing["id"]


def test_timeline_merges_item_history_with_whitelisted_run_events(
    client: TestClient, service: TaskService
) -> None:
    item = client.post("/work-items", json={"title": "Voice login"}).json()
    task = client.post(
        "/tasks", json={"request": "Draft the login copy", "work_item_id": item["id"]}
    ).json()
    service.record_plan(task["id"], ["internal step"])
    client.post("/tasks", json={"request": "Unrelated chat turn"})

    entries = client.get(f"/work-items/{item['id']}/timeline").json()["entries"]
    everything = client.get("/timeline").json()["entries"]

    started = next(entry for entry in entries if entry["event_type"] == "TASK_CREATED")
    assert started["summary"] == "Run started: Draft the login copy"
    assert (started["source"], started["task_id"]) == ("task", task["id"])
    assert entries[-1]["event_type"] == "WORK_ITEM_CREATED"
    assert {entry["event_type"] for entry in entries} == {
        "TASK_CREATED",
        "RUN_LINKED",
        "STATUS_CHANGED",
        "WORK_ITEM_CREATED",
    }
    assert "PLAN_CREATED" not in {entry["event_type"] for entry in everything}
    assert all(entry["work_item_id"] == item["id"] for entry in everything)
    oldest = entries[-1]["at"]
    assert (
        client.get(f"/work-items/{item['id']}/timeline", params={"before": oldest}).json()[
            "entries"
        ]
        == []
    )


def test_discuss_opens_a_focused_chat_with_a_reference(client: TestClient) -> None:
    item = client.post("/work-items", json={"title": "Voice login"}).json()

    chat = client.post(f"/work-items/{item['slug']}/discuss", json={"about": "Run failed"}).json()

    focus = client.get(f"/chat-sessions/{chat['id']}/focus").json()["work_items"]
    messages = client.get(f"/chat-sessions/{chat['id']}/messages").json()["messages"]
    assert [entry["id"] for entry in focus] == [item["id"]]
    assert messages[0]["role"] == "system"
    assert "#voice-login" in messages[0]["content"]
    assert "Run failed" in messages[0]["content"]


def test_backfill_creates_one_item_per_coding_lineage_once(
    database: Database, service: TaskService
) -> None:
    chat = service.create_chat_session()
    root = service.create_task(
        request="Add voice login",
        chat_session_id=chat.id,
        source_context={"repository_id": "personal-assistant"},
    )
    revision = service.create_task(request="Fix review comments", parent_task_id=root.id)
    chat_turn = service.create_task(request="What's the weather?")
    with database.session() as session, session.begin():
        session.add(
            CodingRunModel(
                task_id=root.id,
                repository_id="personal-assistant",
                github_repository="yuyangtj/personal_assistant",
                branch="assistant/task-1",
                phase="pr_created",
                pull_request_number=21,
                pull_request_url="https://github.com/yuyangtj/personal_assistant/pull/21",
            )
        )
        session.get(TaskModel, revision.id).status = TaskStatus.COMPLETED.value

    items = WorkItemService(database)
    assert items.backfill_from_tasks() == 1
    assert items.backfill_from_tasks() == 0

    (item,) = items.list()
    assert item.status == "done"
    assert {task.id for task in items.runs(item.id)} == {root.id, revision.id}
    assert {link["kind"] for link in item.links} == {"repository", "pull_request"}
    assert [focus.id for focus in items.focused(chat.id)] == [item.id]
    assert service.get_task(chat_turn.id).work_item_id is None


def test_new_work_skips_a_finished_focused_item_and_starts_open_items(
    client: TestClient, database: Database, service: TaskService
) -> None:
    chat = service.create_chat_session()
    items = WorkItemService(database)
    finished = items.create(title="Toggle icon", chat_session_id=chat.id)
    items.update(finished.id, expected_version=1, status="done")
    message = _message(client, chat.id, "Research a new font for the console")

    task = client.post(
        f"/chat-sessions/{chat.id}/messages/{message}/task", json={"create_work_item": True}
    ).json()

    assert task["work_item_id"] != finished.id
    fresh = items.get(task["work_item_id"])
    assert fresh.status == "active"
    assert items.get(finished.id).status == "done"
    summaries = [entry["summary"] for entry in items.timeline(fresh.id)]
    assert "Status: open → active" in summaries
