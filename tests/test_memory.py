def test_memory_is_explicit_searchable_and_archivable(client) -> None:
    created = client.post(
        "/memories",
        json={
            "kind": "preference",
            "content": "Prefer MiniMax for simple coding tasks",
            "tags": ["coding"],
        },
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]
    assert client.get("/memories").json()["memories"][0]["content"].startswith("Prefer")

    archived = client.delete(f"/memories/{memory_id}")
    assert archived.status_code == 200
    assert archived.json()["active"] is False
    assert client.get("/memories").json()["memories"] == []
    assert len(client.get("/memories?include_archived=true").json()["memories"]) == 1
