from __future__ import annotations

import re
import time

import httpx
from playwright.sync_api import Page, expect

from tests.ui.helpers import eventually, in_more, notes, pick_repository


def new_chat(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/ui#/chats")
    page.click("#new-chat")
    expect(page.locator("#chat-title")).not_to_have_text("No conversation open")


def say(page: Page, text: str) -> None:
    page.fill("#input", text)
    page.press("#input", "Enter")


def test_a_message_shows_at_once_even_when_the_server_is_slow(page: Page, live_server: str) -> None:
    def slow(route) -> None:
        if route.request.method == "POST":
            page.wait_for_timeout(2000)
        route.continue_()

    new_chat(page, live_server)
    page.route("**/chat-sessions/*/messages", slow)

    started = time.monotonic()
    say(page, "hello there")
    expect(page.locator(".msg.user.pending .bubble")).to_have_text("hello there", timeout=1000)
    assert time.monotonic() - started < 1.5  # well before the server's 2 s
    expect(page.locator(".typing")).to_be_visible()  # the assistant is "thinking"
    expect(page.locator("#input")).to_have_value("")

    # When the server answers, the real message replaces it: no duplicate.
    expect(page.locator(".msg.pending")).to_have_count(0, timeout=8000)
    expect(page.locator("#transcript .msg.user")).to_have_count(1)
    expect(page.locator("#transcript .msg.user")).to_contain_text("hello there")


def test_a_failed_send_gives_the_text_back_and_keeps_the_error(
    page: Page, live_server: str
) -> None:
    new_chat(page, live_server)
    say(page, "first message")  # queues a task (no worker here), so the page keeps polling
    expect(page.locator("#transcript .taskcard")).to_be_visible()
    page.route(
        "**/chat-sessions/*/messages",
        lambda route: (
            route.fulfill(status=500, body='{"detail": "boom"}', content_type="application/json")
            if route.request.method == "POST"
            else route.continue_()
        ),
    )

    say(page, "this one fails")

    expect(page.locator("#input")).to_have_value("this one fails")  # nothing is lost
    expect(page.locator("#error")).to_have_text("boom")
    page.wait_for_timeout(2500)  # a few background refreshes later it is still there
    expect(page.locator("#error")).to_have_text("boom")
    expect(page.locator("#transcript")).not_to_contain_text("this one fails")


def test_reads_are_retried_through_a_restart_but_writes_are_not(
    page: Page, live_server: str
) -> None:
    page.goto(f"{live_server}/ui#/chats")
    failures = {"left": 2}

    def restarting(route) -> None:
        if failures["left"] > 0:
            failures["left"] -= 1
            route.fulfill(status=502, body="Bad Gateway")
        else:
            route.continue_()

    page.route("**/work-items?*", restarting)
    result = page.evaluate("api('/work-items?limit=5').then(() => 'ok').catch(e => e.message)")
    assert result == "ok"  # two 502s, then the answer
    assert failures["left"] == 0

    page.route(
        "**/chat-sessions",
        lambda r: r.fulfill(status=503, body="x") if r.request.method == "POST" else r.continue_(),
    )
    message = page.evaluate(
        "api('/chat-sessions', {method: 'POST', body: '{}'}).catch(e => e.message)"
    )
    assert "restarting" in message  # said at once, and not repeated by itself


def test_polling_pauses_in_a_hidden_tab_and_the_page_is_not_redrawn_when_nothing_changed(
    page: Page, live_server: str
) -> None:
    reads: list[float] = []
    page.on(
        "request",
        lambda r: (
            r.method == "GET" and r.url.endswith("/messages") and reads.append(time.monotonic())
        ),
    )
    new_chat(page, live_server)
    say(page, "keep me busy")  # a queued task: the page polls until it is done
    expect(page.locator("#transcript .taskcard")).to_be_visible()
    expect(page.locator("#send")).to_be_enabled()  # the send has finished
    page.wait_for_timeout(1500)  # a refresh already under way when it ended redraws once more

    page.evaluate("document.querySelector('#transcript .msg').dataset.mark = 'untouched'")
    reads.clear()
    page.wait_for_timeout(3500)
    assert len(reads) >= 2  # it is looking
    assert page.evaluate("document.querySelector('#transcript .msg').dataset.mark") == "untouched"

    hide = "Object.defineProperty(document, 'hidden', {value: %s, configurable: true});"
    page.evaluate(hide % "true" + "document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(600)
    reads.clear()
    page.wait_for_timeout(3000)
    assert reads == []  # hidden: not looking at all
    page.evaluate(hide % "false" + "document.dispatchEvent(new Event('visibilitychange'))")
    eventually(lambda: len(reads) >= 1)  # shown again: looks at once


def test_saying_note_in_chat_saves_a_note_and_links_to_it(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    new_chat(page, live_server)

    say(page, "note: call the dentist on Monday")

    link = page.locator("#transcript a.block-link")
    expect(link).to_contain_text("Open Notes")
    expect(page.locator("#transcript")).to_contain_text(
        "Saved to Notes: “call the dentist on Monday”"
    )
    assert [n["body"] for n in notes(api)] == ["call the dentist on Monday"]
    link.click()
    expect(page.locator("#notes-view")).to_be_visible()
    expect(page.locator("#note-list .note")).to_contain_text("call the dentist on Monday")


def test_the_stats_page_shows_the_work_done(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/ui#/stats")
    expect(page.locator("#stats-view")).to_be_visible()
    expect(page.locator("#stats-body")).to_contain_text("No work in this period")
    expect(page.locator("#stats-body")).to_contain_text("Disk")

    new_chat(page, live_server)
    say(page, "hello there")  # queues a reply (no worker runs here)
    expect(page.locator("#transcript .taskcard")).to_be_visible()

    in_more(page, "#nav-stats")
    row = page.locator("#stats-body tbody tr").first
    expect(row.locator("th")).to_have_text("chat reply")
    expect(row.locator("td").first).to_have_text("1")  # one task
    expect(row.locator("td").nth(3)).to_have_text("1")  # not finished: nothing runs it here
    page.select_option("#stats-days", "30")
    expect(page.locator("#stats-body tbody tr")).to_have_count(1)


def test_the_repository_picked_above_the_chat_is_sent_with_the_message(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    chat = api.post("/chat-sessions", json={}).json()["id"]
    messages = [
        api.post(f"/chat-sessions/{chat}/messages", json={"content": text}).json()["message"]["id"]
        for text in ("What's this repo about", "and another thing")
    ]
    sent: list[dict] = []
    page.on(
        "request",
        lambda r: r.method == "POST" and r.url.endswith("/task") and sent.append(r.post_data_json),
    )
    page.goto(f"{live_server}/ui#/chats/{chat}")
    expect(page.locator("#transcript")).to_contain_text("What's this repo about")

    # The reply task is made by the console, once the server has routed the message.
    pick_repository(page, "analytics-agent-playground")
    page.evaluate("id => createTask(id)", messages[0])
    eventually(lambda: len(sent) == 1)
    assert sent[0]["source_context"]["selected_repository_id"] == "analytics-agent-playground"

    pick_repository(page, "")  # "Pick for me": nothing is sent
    page.evaluate("id => createTask(id)", messages[1])
    eventually(lambda: len(sent) == 2)
    assert "selected_repository_id" not in sent[1]["source_context"]


def test_the_stats_page_lists_what_the_assistant_can_do(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/ui#/stats")

    body = page.locator("#abilities-body")
    expect(body.locator("h3")).to_have_text(
        [
            "Done without asking",
            "Done, then you review the result",
            "Only after you confirm",
            "Never done by the assistant",
        ]
    )
    expect(body).to_contain_text("Notes")
    coding = body.locator("li", has_text=re.compile(r"^\s*Coding"))
    expect(coding).to_contain_text("runs in the background")
    # No GitHub token on the test server: shown, but marked as not set up.
    expect(body.locator("li", has_text="Looking at repositories")).to_contain_text(
        "not set up on this server"
    )
