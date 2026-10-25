"""A spoken reply starts at its first sentence: the draft is spoken as it is written, and
the finished reply adds only what wasn't said yet. Old phone apps wait for the whole reply."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.ui.helpers import eventually
from tests.ui.test_chat_ui import new_chat

PHONE = """
window.sentToPhone = [];
window.AndroidAssistant = {
  postMessage(raw) {
    const message = JSON.parse(raw);
    window.sentToPhone.push(message);
    if (message.type === "hello") setTimeout(() => this.onmessage({ data: JSON.stringify(
      { type: "capabilities", voice: true, speakQueue: QUEUE }) }), 30);
  },
  onmessage: null,
};
"""


def _said(page: Page) -> list[tuple[str, str]]:
    return page.evaluate(
        "() => window.sentToPhone.filter((m) => m.type === 'speak' || m.type === 'speak_more')"
        ".map((m) => [m.type, m.text])"
    )


def _ask_by_voice(page: Page, text: str) -> None:
    page.evaluate("() => onPhoneMessage({ type: 'voice', state: 'listening' })")
    page.evaluate(f"() => onPhoneMessage({{ type: 'voice', state: 'final', text: {text!r} }})")
    page.click("#send")


def _start_reply(live_app):
    service = live_app.state.task_service
    task = eventually(lambda: service.claim_next_task(worker_id="ui", lease_seconds=60))
    execution = service.start_execution(
        task.id, capability_id="conversation", executor_id="model-conversation", execution_input={}
    )
    return service, task, execution


def test_the_reply_is_spoken_from_its_first_sentence(
    page: Page, live_server: str, live_app
) -> None:
    page.add_init_script(PHONE.replace("QUEUE", "true"))
    new_chat(page, live_server)
    _ask_by_voice(page, "when is the museum open?")
    service, task, execution = _start_reply(live_app)

    service.record_progress(task.id, "It opens at 10.", "draft")
    eventually(lambda: _said(page) == [["speak", "It opens at 10."]], timeout=10)
    expect(page.locator("#transcript .msg.draft")).to_contain_text("It opens at 10.")

    service.record_progress(task.id, "It opens at 10. On Fridays it stays open until 20.", "draft")
    eventually(lambda: len(_said(page)) == 2, timeout=10)
    assert _said(page)[1] == ["speak_more", "On Fridays it stays open until 20."]

    reply = "It opens at 10. On Fridays it stays open until 20. Mondays it's closed."
    service.start_validation(task.id, execution, output={"summary": reply})
    service.complete_task(task.id, execution, reply=reply)

    eventually(lambda: len(_said(page)) == 3, timeout=10)
    assert _said(page)[2] == ["speak_more", "Mondays it's closed."]  # only what was left
    expect(page.locator("#transcript .msg.draft")).to_have_count(0)


def test_an_older_phone_app_waits_for_the_whole_reply(
    page: Page, live_server: str, live_app
) -> None:
    page.add_init_script(PHONE.replace("QUEUE", "false"))
    new_chat(page, live_server)
    _ask_by_voice(page, "when is the museum open?")
    service, task, execution = _start_reply(live_app)

    service.record_progress(task.id, "It opens at 10.", "draft")
    expect(page.locator("#transcript .msg.draft")).to_contain_text("It opens at 10.", timeout=10000)
    assert _said(page) == []  # shown as it's written, but not spoken early

    reply = "It opens at 10. Mondays it's closed."
    service.start_validation(task.id, execution, output={"summary": reply})
    service.complete_task(task.id, execution, reply=reply)
    eventually(lambda: _said(page) == [["speak", reply]], timeout=10)
