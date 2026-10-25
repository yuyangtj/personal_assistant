"""The one Assistant conversation: the app opens in it, it leads the chat list, and the
face is on there by default (and only there)."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.ui.helpers import eventually, in_chat_menu
from tests.ui.test_reply_blocks_ui import _chat_id


def _home(api) -> str:
    return api.get("/chat-sessions/home").json()["id"]


def test_the_app_opens_in_assistant_and_lists_it_first_without_actions(
    phone: Page, live_server: str, api
) -> None:
    api.post("/chat-sessions", json={})
    home = _home(api)

    phone.goto(f"{live_server}/ui")
    expect(phone.locator("#chat-title")).to_have_text("Assistant")
    assert _chat_id(phone) == home

    phone.click(".main-head a.back")  # one tap back: the other chats
    rows = phone.locator("#chat-list .chat-row")
    expect(rows.first).to_contain_text("Assistant")
    expect(rows.first.locator("[data-chat-menu]")).to_have_count(0)  # no pin, archive, delete
    expect(rows).to_have_count(2)


def test_the_face_is_on_in_assistant_and_off_elsewhere_each_remembered(
    page: Page, live_server: str, api
) -> None:
    other = api.post("/chat-sessions", json={}).json()["id"]
    page.goto(f"{live_server}/ui")
    expect(page.locator("#avatar-stage svg")).to_be_visible()

    page.goto(f"{live_server}/ui#/chats/{other}")
    expect(page.locator("#avatar-stage")).to_be_hidden()

    page.goto(f"{live_server}/ui#/chats/{_home(api)}")
    expect(page.locator("#avatar-stage svg")).to_be_visible()
    in_chat_menu(page, "#avatar-toggle")  # switched off in Assistant
    expect(page.locator("#avatar-stage")).to_be_hidden()
    page.reload()
    expect(page.locator("#chat-title")).to_have_text("Assistant")
    expect(page.locator("#avatar-stage")).to_be_hidden()  # and it stays off


SHARING_PHONE = """
window.AndroidAssistant = { postMessage() {}, onmessage: null };
"""


def test_something_shared_with_no_chat_open_goes_to_assistant(
    page: Page, live_server: str, api
) -> None:
    page.add_init_script(SHARING_PHONE)
    page.goto(f"{live_server}/ui#/chats")
    expect(page.locator("#chat-list")).to_contain_text("Assistant")

    page.evaluate("() => onPhoneMessage({ type: 'shared', text: 'https://example.com/recipe' })")

    eventually(lambda: _chat_id(page) == _home(api))
    expect(page.locator("#input")).to_have_value("https://example.com/recipe")


def test_memories_shows_what_the_assistant_remembers_of_recent_days(
    page: Page, live_server: str, live_app
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.work.digest import DigestJob, DigestWriter
    from tests.fakes import ScriptedChat

    page.goto(f"{live_server}/ui#/memories")
    expect(page.locator("#recent-days")).to_be_hidden()  # nothing yet

    service = live_app.state.task_service
    chat = service.create_chat_session(title="Trip").id
    service.append_chat_message(chat, content="flying to Shanghai on 12 November")
    writer = DigestWriter(ScriptedChat('{"summary": "Flying to Shanghai on 12 November."}'))
    later = datetime.now(UTC) + timedelta(minutes=11)
    DigestJob(service.database, writer, clock=lambda: later).run_due()

    page.reload()
    expect(page.locator("#recent-days")).to_contain_text("Flying to Shanghai on 12 November.")
    expect(page.locator("#recent-days-when")).to_contain_text("last updated")
