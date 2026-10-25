"""What the chat agent's replies carry, as the console shows them: charts, upload buttons,
choices, and the progress line while it works. The data goes straight into the services
(there is no worker in these tests); the page must draw it and act on it."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from app.chat.blocks import model_blocks, take_tool_blocks
from app.chat.uploads import upload_block
from app.tools.klarna import KlarnaTools
from app.work.purchases import PurchaseService
from tests.ui.helpers import eventually
from tests.ui.test_chat_ui import new_chat

EXPORT = (
    "date,time,merchant,amount,currency,original_amount,payment_type,status,date_display\n"
    "2026-07-03,10:00,Apotea,100,SEK,,Pay in 30 days,,3 Jul\n"
    "2026-08-14,20:05,SJ,388,SEK,,Monthly Invoice,,14 Aug\n"
    "2026-09-02,09:10,Apotea,173,SEK,,Pay in 30 days,,2 Sep\n"
)


def _chat_id(page: Page) -> str:
    """The open chat, from the page's address (#/chats/<id>)."""
    return eventually(lambda: page.evaluate("() => location.hash.split('/')[2] || ''"))


def _reply(page: Page, live_app, text: str, blocks: list[dict]) -> str:
    """An assistant reply with these blocks in the open chat, then the page reloaded (it
    polls only while something runs)."""
    chat_id = _chat_id(page)
    live_app.state.task_service.append_chat_message(
        chat_id, content=text, role="assistant", blocks=blocks
    )
    page.reload()
    return chat_id


def test_a_spending_chart_is_drawn_with_its_numbers_for_screen_readers(
    page: Page, live_server: str, live_app
) -> None:
    service = PurchaseService(live_app.state.database)
    service.import_klarna_csv(EXPORT)
    charts, _ = take_tool_blocks(KlarnaTools(service).call("spending", {"group_by": "month"}))
    (chart,) = charts
    new_chat(page, live_server)
    _reply(page, live_app, "Spending dipped in July.", charts)

    figure = page.locator("#transcript figure.chart")
    expect(figure.locator("svg")).to_be_visible(timeout=10000)
    expect(figure.locator("figcaption")).to_have_text(chart["title"])
    expect(page.locator("#transcript")).to_contain_text("Spending dipped in July.")
    # One row per month in the hidden table, so the figures aren't only a picture.
    expect(figure.locator("table.sr-only tr")).to_have_count(3)


def test_the_klarna_upload_button_takes_a_file_and_imports_it(
    page: Page, live_server: str, live_app, tmp_path
) -> None:
    new_chat(page, live_server)
    _reply(
        page,
        live_app,
        "Pick your Klarna export with the button.",
        [upload_block("klarna-purchases")],
    )

    button = page.locator("#transcript .upload-button")
    expect(button).to_be_visible(timeout=10000)
    export = tmp_path / "klarna.csv"
    export.write_text(EXPORT)
    page.locator("#transcript .upload-button input[type=file]").set_input_files(str(export))

    expect(page.locator("#transcript .upload-done")).to_be_visible(timeout=10000)
    assert len(PurchaseService(live_app.state.database).purchases()) == 3


def test_a_choice_is_sent_as_the_next_message(page: Page, live_server: str, live_app) -> None:
    new_chat(page, live_server)
    chat_id = _reply(
        page, live_app, "By week or by merchant?", model_blocks({"choices": ["By week"]})
    )

    choice = page.locator("#transcript [data-choice='By week']")
    expect(choice).to_be_visible(timeout=10000)
    choice.click()

    expect(page.locator("#transcript .msg.user")).to_contain_text("By week")
    eventually(
        lambda: any(
            m.content == "By week"
            for m in live_app.state.task_service.list_chat_messages(chat_id)
            if m.role == "user"
        )
    )


def test_the_reply_card_shows_what_the_agent_is_doing(
    page: Page, live_server: str, live_app
) -> None:
    new_chat(page, live_server)
    chat_id = _chat_id(page)
    service = live_app.state.task_service
    posted = service.append_chat_message(chat_id, content="how much at Lidl?")
    service.create_task_from_message(chat_id, posted.message.id)
    # A worker picks it up and starts on it, as the real one would.
    task = service.claim_next_task(worker_id="ui-test", lease_seconds=60)
    service.start_execution(
        task.id, capability_id="conversation", executor_id="model-conversation", execution_input={}
    )
    page.reload()

    service.record_progress(task.id, "Looking at Klarna", "milestone")

    expect(page.locator("#transcript .taskcard")).to_contain_text(
        "Looking at Klarna", timeout=10000
    )


def _timer_card(page: Page, live_app) -> tuple[str, str, str]:
    """A reply with a proposed 10-minute timer, as the agent leaves it."""
    chat_id = _chat_id(page)
    service = live_app.state.task_service
    posted = service.append_chat_message(chat_id, content="set a timer for 10 minutes")
    service.create_task_from_message(chat_id, posted.message.id)
    task = service.claim_next_task(worker_id="ui-test", lease_seconds=60)
    execution = service.start_execution(
        task.id, capability_id="conversation", executor_id="model-conversation", execution_input={}
    )
    service.start_validation(task.id, execution, output={"summary": "timer"})
    service.complete_task(
        task.id,
        execution,
        reply="A 10-minute timer for the pasta.",
        action={"type": "set_timer", "seconds": 600, "label": "pasta"},
    )
    reply = service.list_chat_messages(chat_id)[-1]
    (block,) = reply.blocks
    return chat_id, reply.id, block["id"]


def test_in_a_browser_a_phone_action_says_to_use_the_phone(
    page: Page, live_server: str, live_app
) -> None:
    new_chat(page, live_server)
    _timer_card(page, live_app)
    page.reload()

    expect(page.locator("#transcript .phone-action-note")).to_contain_text(
        "Set a 10-minute timer: open the assistant on your phone", timeout=10000
    )
    expect(page.locator("#transcript [data-message-action]")).to_have_count(0)


# A stand-in for the phone app's bridge: it records what the page asks and answers as the
# app does once it has read and checked the action on the server.
FAKE_PHONE = """
window.sentToPhone = [];
window.AndroidAssistant = {
  postMessage(raw) {
    const message = JSON.parse(raw);
    window.sentToPhone.push(message);
    const reply = (m) => setTimeout(() => this.onmessage({ data: JSON.stringify(m) }), 50);
    if (message.type === "offer_message_action") {
      reply({ type: "message_action", id: message.id, action: "native-1",
              label: "Set a timer for 10 minutes" });
    } else if (message.type === "run_action") {
      reply({ type: "action_result", action: message.id });
    }
  },
  onmessage: null,
};
"""


def test_on_the_phone_a_proposed_timer_is_confirmed_and_marked_done(
    page: Page, live_server: str, live_app
) -> None:
    page.add_init_script(FAKE_PHONE)
    new_chat(page, live_server)
    chat_id, message_id, block_id = _timer_card(page, live_app)
    page.reload()

    card = page.locator("#transcript .phone-action-card")
    expect(card).to_contain_text("Set a timer for 10 minutes?", timeout=10000)
    sent = page.evaluate("() => window.sentToPhone")
    # The page names the card only; the app reads the action from the server.
    assert {
        "type": "offer_message_action",
        "id": block_id,
        "text": f"{chat_id}/{message_id}",
    } in sent
    assert not any("seconds" in str(message) for message in sent)

    card.locator("[data-message-action]").click()

    expect(page.locator("#transcript .upload-done")).to_contain_text(
        "Set a timer for 10 minutes", timeout=10000
    )
    assert {"type": "run_action", "id": "native-1"} in page.evaluate("() => window.sentToPhone")
    (block,) = live_app.state.task_service.list_chat_messages(chat_id)[-1].blocks
    assert block["state"] == "done"
