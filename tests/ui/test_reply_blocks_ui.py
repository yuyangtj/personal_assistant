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
