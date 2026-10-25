"""Talking to the assistant on the phone: once you speak in a conversation its replies are
spoken, and a follow-up like "yes please do" goes to the server, which knows what it
follows, not to the phone's own model, which doesn't."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.ui.helpers import eventually
from tests.ui.test_chat_ui import new_chat
from tests.ui.test_reply_blocks_ui import _chat_id

# A phone whose on-device model answers everything as small talk, like the real one did
# with "Yes please do"; anything about street food it leaves to the server.
FAKE_PHONE = """
window.sentToPhone = [];
window.AndroidAssistant = {
  postMessage(raw) {
    const message = JSON.parse(raw);
    window.sentToPhone.push(message);
    const reply = (m) => setTimeout(() => this.onmessage({ data: JSON.stringify(m) }), 30);
    if (message.type === "hello") reply({ type: "capabilities", voice: true });
    if (message.type === "route") {
      reply(/street food/i.test(message.text)
        ? { type: "route", id: message.id, route: "cloud" }
        : { type: "route", id: message.id, route: "reply", text: "Okay, I'm ready to assist." });
    }
  },
  onmessage: null,
};
"""


def _answer(live_app, text: str) -> None:
    """The worker's part: take the queued reply task and finish it with this text."""
    service = live_app.state.task_service
    task = eventually(lambda: service.claim_next_task(worker_id="ui-test", lease_seconds=60))
    execution = service.start_execution(
        task.id, capability_id="conversation", executor_id="model-conversation", execution_input={}
    )
    service.start_validation(task.id, execution, output={"summary": text})
    service.complete_task(task.id, execution, reply=text, emotion="Warm")


def _spoken(page: Page) -> list[str]:
    return page.evaluate(
        "() => window.sentToPhone.filter((m) => m.type === 'speak').map((m) => m.text)"
    )


def _type(page: Page, text: str) -> None:
    page.fill("#input", text)
    page.press("#input", "Enter")


def test_a_spoken_conversation_stays_spoken_and_follow_ups_go_to_the_server(
    page: Page, live_server: str, live_app
) -> None:
    page.add_init_script(FAKE_PHONE)
    new_chat(page, live_server)
    chat_id = _chat_id(page)

    # Spoken: the reply is read out.
    page.evaluate("() => onPhoneMessage({ type: 'voice', state: 'listening' })")
    page.evaluate(
        "() => onPhoneMessage({ type: 'voice', state: 'final', text: 'find street food in Shanghai' })"
    )
    page.click("#send")
    _answer(live_app, "Try Yunnan Road. Want me to add sweet dishes?")
    eventually(lambda: "Try Yunnan Road. Want me to add sweet dishes?" in _spoken(page), timeout=10)

    # Typed in the same conversation: still read out.
    _type(page, "add street food sweets too")
    _answer(live_app, "Added. Want me to research the best places for these?")
    eventually(lambda: any("research the best places" in text for text in _spoken(page)), timeout=10)

    # "Yes please do" follows that question: the server gets it, not the phone's model.
    _type(page, "Yes please do")
    eventually(lambda: any(
        m.content == "Yes please do"
        for m in live_app.state.task_service.list_chat_messages(chat_id)
    ))
    expect(page.locator("#transcript")).not_to_contain_text("ON THIS PHONE")
    _answer(live_app, "Here are the best places.")
    eventually(lambda: "Here are the best places." in _spoken(page), timeout=10)


def test_a_typed_conversation_stays_quiet_and_a_fresh_one_still_answers_on_the_phone(
    page: Page, live_server: str, live_app
) -> None:
    page.add_init_script(FAKE_PHONE)
    new_chat(page, live_server)

    # Nothing saved yet: the phone may answer on its own, instantly.
    _type(page, "hello there")
    expect(page.locator("#transcript")).to_contain_text("ON THIS PHONE")

    _type(page, "find street food in Shanghai")
    _answer(live_app, "Try Yunnan Road.")
    expect(page.locator("#transcript")).to_contain_text("Try Yunnan Road.", timeout=10000)
    assert _spoken(page) == []  # never spoken to, so nothing read out
