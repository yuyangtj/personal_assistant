"""The avatar: off by default (and not even loaded), on with one button, and driven only
by events, so it can be checked on its own."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.ui.helpers import eventually
from tests.ui.test_chat_ui import new_chat
from tests.ui.test_reply_blocks_ui import FAKE_PHONE, _chat_id


def _face(page: Page) -> dict:
    return page.evaluate(
        "() => { const s = document.querySelector('#avatar-stage svg');"
        " return s ? { ...s.dataset } : null; }"
    )


def _switch_on(page: Page) -> None:
    page.click("#avatar-toggle")
    expect(page.locator("#avatar-stage svg")).to_be_visible()


def test_off_by_default_and_never_downloaded_until_switched_on(
    page: Page, live_server: str
) -> None:
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))
    new_chat(page, live_server)
    before = page.locator("#transcript").bounding_box()

    expect(page.locator("#avatar-stage")).to_be_hidden()
    assert not any("avatar.js" in url for url in requested)

    _switch_on(page)
    assert any(url.endswith("/ui/static/avatar.js") for url in requested)
    expect(page.locator("#avatar-toggle")).to_have_attribute("aria-pressed", "true")

    page.reload()  # remembered on this device
    expect(page.locator("#avatar-stage svg")).to_be_visible()

    page.click("#avatar-toggle")
    expect(page.locator("#avatar-stage")).to_be_hidden()
    after = page.locator("#transcript").bounding_box()
    assert (after["x"], after["y"], after["width"]) == (before["x"], before["y"], before["width"])
    page.reload()
    expect(page.locator("#avatar-stage")).to_be_hidden()


def test_a_recorded_sequence_of_events_drives_the_face(page: Page, live_server: str) -> None:
    new_chat(page, live_server)
    _switch_on(page)

    def event(type_: str, data: dict | None = None) -> dict:
        page.evaluate("([t, d]) => window.avatarEvent(t, d)", [type_, data or {}])
        return _face(page)

    assert event("transcript", {"waiting": True, "last": None})["state"] == "thinking"
    face = event("transcript", {"waiting": False, "last": {"id": "m1", "emotion": "Concerned"}})
    assert (face["state"], face["emotion"]) == ("idle", "Concerned")
    assert event("listening", {"on": True})["state"] == "listening"
    assert event("listening", {"on": False})["state"] == "idle"

    event("speak", {"text": "Oh no, that is really annoying.", "ms": 1500})
    opened = eventually(lambda: float((_face(page) or {}).get("open", 0)) > 0.3)
    assert opened
    eventually(lambda: _face(page)["state"] == "idle", timeout=4)
    assert float(_face(page)["open"]) == 0

    # Poses are a pure function: the concerned brows lean, a warm face smiles.
    poses = page.evaluate(
        "() => [avatar.pose('idle', 'Concerned'), avatar.pose('idle', 'Warm'),"
        " avatar.pose('thinking', 'Warm'), avatar.pose('speaking', 'Warm', 1)]"
    )
    assert poses[0]["browTilt"] > 0 and poses[0]["smile"] < 0
    assert poses[1]["smile"] > 0 and poses[1]["browTilt"] == 0
    assert poses[2]["dots"] and poses[2]["pupilDy"] < 0
    assert poses[3]["open"] == 1


def test_it_follows_a_real_reply_and_its_emotion(page: Page, live_server: str, live_app) -> None:
    new_chat(page, live_server)
    _switch_on(page)
    chat_id = _chat_id(page)
    service = live_app.state.task_service
    posted = service.append_chat_message(chat_id, content="my flight was cancelled")
    service.create_task_from_message(chat_id, posted.message.id)
    page.reload()
    eventually(lambda: (_face(page) or {}).get("state") == "thinking")

    task = service.claim_next_task(worker_id="ui-test", lease_seconds=60)
    execution = service.start_execution(
        task.id, capability_id="conversation", executor_id="model-conversation", execution_input={}
    )
    service.start_validation(task.id, execution, output={"summary": "sympathy"})
    service.complete_task(task.id, execution, reply="That's rotten, sorry.", emotion="Concerned")

    eventually(lambda: _face(page)["emotion"] == "Concerned", timeout=10)
    assert _face(page)["state"] == "idle"


def test_on_the_phone_it_listens_and_speaks(page: Page, live_server: str) -> None:
    page.add_init_script(FAKE_PHONE)
    new_chat(page, live_server)
    _switch_on(page)

    page.evaluate("() => onPhoneMessage({ type: 'voice', state: 'listening' })")
    assert _face(page)["state"] == "listening"
    page.evaluate("() => onPhoneMessage({ type: 'voice', state: 'final', text: 'hi' })")
    assert _face(page)["state"] == "idle"

    page.evaluate("() => phoneSpeak('Hello there, how was your day today?')")
    assert _face(page)["state"] == "speaking"
    eventually(lambda: float(_face(page)["open"]) > 0.3)
    page.evaluate("() => stopSpeaking()")
    assert (_face(page)["state"], _face(page)["open"]) == ("idle", "0.00")


def test_it_fits_a_phone_and_keeps_still_with_reduced_motion(browser, live_server: str) -> None:
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, reduced_motion="reduce"
    )
    phone = context.new_page()
    phone.set_default_timeout(8000)
    new_chat(phone, live_server)
    _switch_on(phone)

    stage = phone.locator("#avatar-stage").bounding_box()
    assert stage["height"] <= 112 and stage["width"] <= 390
    expect(phone.locator("#composer")).to_be_in_viewport()
    phone.evaluate("() => avatarEvent('speak', { text: 'Hello there', ms: 2000 })")
    first = _face(phone)["open"]
    phone.wait_for_timeout(400)
    assert _face(phone)["open"] == first == "0.40"  # a still, open-mouthed pose
    context.close()


def test_switched_on_away_from_a_conversation_it_opens_one(
    phone: Page, live_server: str, api
) -> None:
    # On a phone the chat pane is hidden until a conversation is open, so the face would
    # land there unseen. Without any conversation yet, it says where the face shows.
    phone.goto(f"{live_server}/ui#/chats")
    phone.click("#avatar-toggle")
    expect(phone.locator("#status")).to_have_text("The avatar shows in a conversation.")
    phone.click("#avatar-toggle")  # off again

    chat = api.post("/chat-sessions", json={}).json()["id"]
    for start in ("#/chats", "#/notes"):
        phone.goto(f"{live_server}/ui{start}")
        phone.click("#avatar-toggle")
        expect(phone.locator("#avatar-stage svg")).to_be_visible()
        assert chat in phone.url
        phone.click("#avatar-toggle")
