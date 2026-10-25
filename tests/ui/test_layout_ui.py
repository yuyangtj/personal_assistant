"""The console's layout: tabs on the phone, the rail on a desktop, and a message box that
starts one line tall and grows."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.ui.test_chat_ui import new_chat


def _no_sideways_scroll(page: Page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def test_on_the_phone_the_tabs_show_on_lists_and_hide_inside_a_chat(
    phone: Page, live_server: str
) -> None:
    phone.goto(f"{live_server}/ui#/chats")
    rail = phone.locator("nav.rail")
    expect(rail).to_be_visible()
    box = rail.bounding_box()
    assert box["y"] + box["height"] >= 844 - 1  # at the bottom of the screen
    for section in ("chats", "notes", "items", "memories"):
        expect(phone.locator(f"#nav-{section}")).to_be_visible()
    assert _no_sideways_scroll(phone)

    phone.click("#new-chat")
    expect(phone.locator("#chat-title")).not_to_have_text("No conversation open")
    expect(rail).to_be_hidden()  # the composer owns the bottom of the screen
    expect(phone.locator("#composer")).to_be_in_viewport()
    assert _no_sideways_scroll(phone)

    for route in ("notes", "memories", "stats"):
        phone.goto(f"{live_server}/ui#/{route}")
        expect(rail).to_be_visible()
        assert _no_sideways_scroll(phone), route


def test_more_holds_stats_and_the_theme(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/ui#/chats")
    expect(page.locator("#more-sheet")).to_be_hidden()

    page.click("#more-open")
    page.click("#theme-toggle")
    theme = page.evaluate("() => document.documentElement.dataset.theme")
    page.click("#theme-toggle")
    assert page.evaluate("() => document.documentElement.dataset.theme") != theme
    page.click("#nav-stats")

    expect(page.locator("#stats-view")).to_be_visible()
    expect(page.locator("#more-sheet")).to_be_hidden()  # following a link closes it


def test_the_message_box_starts_one_line_tall_and_grows(page: Page, live_server: str) -> None:
    new_chat(page, live_server)
    box = page.locator("#input")
    one_line = box.bounding_box()["height"]
    assert one_line <= 40

    box.fill("line\n" * 4 + "line")
    grown = box.bounding_box()["height"]
    box.fill("line\n" * 40)
    capped = box.bounding_box()["height"]

    assert grown > one_line * 2
    assert capped <= 170  # about six lines, then it scrolls inside
    box.fill("")
    assert box.bounding_box()["height"] == one_line
