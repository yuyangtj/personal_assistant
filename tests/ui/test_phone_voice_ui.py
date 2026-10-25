"""Talking to the assistant on the phone: once you speak in a conversation its replies are
spoken, and every message goes to the server, which sees the whole conversation. The
phone never decides what a message means; it only listens, speaks and runs actions."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.ui.helpers import eventually
from tests.ui.test_chat_ui import new_chat
from tests.ui.test_reply_blocks_ui import _chat_id

# A phone app that would still answer a "route" request itself, as small talk, the way its
# on-device model answered "Yes please do". The page must never ask it.
FAKE_PHONE = """
window.sentToPhone = [];
window.AndroidAssistant = {
  postMessage(raw) {
    const message = JSON.parse(raw);
    window.sentToPhone.push(message);
    const reply = (m) => setTimeout(() => this.onmessage({ data: JSON.stringify(m) }), 30);
    if (message.type === "hello") reply({ type: "capabilities", voice: true });
    if (message.type === "route") {
      reply({ type: "route", id: message.id, route: "reply", text: "Okay, I'm ready to assist." });
    }
  },
  onmessage: null,
};
"""


def _asked_to_route(page: Page) -> bool:
    return page.evaluate("() => window.sentToPhone.some((m) => m.type === 'route')")


def _saved(live_app, chat_id: str, text: str) -> bool:
    return any(m.content == text for m in live_app.state.task_service.list_chat_messages(chat_id))


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
        "() => onPhoneMessage({ type: 'voice', state: 'final',"
        " text: 'find street food in Shanghai' })"
    )
    page.click("#send")
    _answer(live_app, "Try Yunnan Road. Want me to add sweet dishes?")
    eventually(lambda: "Try Yunnan Road. Want me to add sweet dishes?" in _spoken(page), timeout=10)

    # Typed in the same conversation: still read out.
    _type(page, "add street food sweets too")
    _answer(live_app, "Added. Want me to research the best places for these?")
    eventually(lambda: any("research the best" in text for text in _spoken(page)), timeout=10)

    # "Yes please do" follows that question: the server gets it, not the phone's model.
    _type(page, "Yes please do")
    eventually(lambda: _saved(live_app, chat_id, "Yes please do"))
    _answer(live_app, "Here are the best places.")
    eventually(lambda: "Here are the best places." in _spoken(page), timeout=10)
    assert not _asked_to_route(page)


def test_even_the_simplest_message_goes_to_the_server_and_typing_stays_quiet(
    page: Page, live_server: str, live_app
) -> None:
    page.add_init_script(FAKE_PHONE)
    new_chat(page, live_server)
    chat_id = _chat_id(page)

    # Small talk and a timer, the things the phone used to answer itself: the server gets
    # both, and a timer comes back as a card the phone runs (see test_reply_blocks_ui).
    for text in ("hello there", "set a timer for 2 minutes"):
        _type(page, text)
        eventually(lambda text=text: _saved(live_app, chat_id, text))
        _answer(live_app, f"Reply to {text}.")
        expect(page.locator("#transcript")).to_contain_text(f"Reply to {text}.", timeout=10000)
    assert not _asked_to_route(page)
    assert _spoken(page) == []  # never spoken to, so nothing read out
