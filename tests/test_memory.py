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


def _say(client, chat_id: str, text: str) -> dict:
    return client.post(f"/chat-sessions/{chat_id}/messages", json={"content": text}).json()


def test_memories_can_be_edited_and_food_ones_are_tagged(client) -> None:
    food = client.post("/memories", json={"kind": "preference", "content": "I'm vegetarian"})
    other = client.post("/memories", json={"kind": "fact", "content": "My partner is Anna"})

    assert food.json()["tags"] == ["food"]
    assert other.json()["tags"] == []

    edited = client.patch(
        f"/memories/{other.json()['id']}",
        json={"content": "My partner is called Anna", "private": True, "tags": ["family"]},
    ).json()

    assert edited["content"] == "My partner is called Anna"
    assert edited["private"] is True and edited["tags"] == ["family"]
    assert client.patch("/memories/missing", json={"private": False}).status_code == 404


def test_the_model_gets_every_shared_memory_and_never_a_private_one(client) -> None:
    service = client.app.state.memory_service
    service.create(kind="fact", content="My partner is called Anna")
    service.create(kind="preference", content="Jag äter inte kött")
    service.create(kind="fact", content="Door code 4711", private=True)

    for_model = service.for_model()

    # No keyword matching: "what's my wife's name?" can still find Anna.
    assert "My partner is called Anna" in for_model
    assert "Jag äter inte kött" in for_model
    assert "Door code 4711" not in for_model
    assert service.for_groceries() == ["Jag äter inte kött"]


def test_forget_in_chat_deletes_the_matching_memory(client) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    service = client.app.state.memory_service
    service.create(kind="preference", content="I like window seats")
    service.create(kind="fact", content="My gym is SATS Odenplan")

    reply = _say(client, chat, "Forget that I like window seats")["decision"]["result"]

    assert reply == "Forgotten: I like window seats"
    assert [m["content"] for m in client.get("/memories").json()["memories"]] == [
        "My gym is SATS Odenplan"
    ]
    missing = _say(client, chat, "forget my shoe size")["decision"]["result"]
    assert missing.startswith("I don't have a memory about that")


def test_forget_never_repeats_a_private_memory(client) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    client.app.state.memory_service.create(kind="fact", content="Door code 4711", private=True)

    reply = _say(client, chat, "forget the door code")["decision"]["result"]

    assert "4711" not in reply
    assert len(client.get("/memories").json()["memories"]) == 1


def test_remember_privately_is_kept_out_of_the_chat_and_any_model(client) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]

    posted = _say(client, chat, "Remember privately that the door code is 4711")

    assert posted["decision"]["handled"] is True
    (memory,) = client.get("/memories").json()["memories"]
    assert memory["content"] == "the door code is 4711" and memory["private"] is True
    messages = client.get(f"/chat-sessions/{chat}/messages").json()["messages"]
    assert all("4711" not in message["content"] for message in messages)
    session = client.get(f"/chat-sessions/{chat}").json()
    assert "4711" not in session["title"]
    # Nothing was queued for a model.
    assert client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"] == []
