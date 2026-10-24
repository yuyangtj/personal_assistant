from __future__ import annotations

import json
import zipfile

import httpx
from playwright.sync_api import Page, expect

from tests.test_notes import takeout_zip
from tests.ui.helpers import add_note, eventually, notes


def open_notes(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/ui#/notes")
    expect(page.locator("#notes-view")).to_be_visible()


def card(page: Page, text: str):
    return page.locator("#note-list .note:not(.editing)", has_text=text)


def open_editor(page: Page, text: str):
    card(page, text).locator(".note-title, .note-body, .note-items").first.click()
    editor = page.locator(".note-modal .note.editing")
    expect(editor).to_be_visible()
    return editor


def test_a_note_is_written_tagged_checked_off_and_pinned(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    open_notes(page, live_server)
    expect(page.locator("#note-title")).to_be_hidden()  # one slim line until it is used
    page.click("#note-body")
    expect(page.locator("#note-title")).to_be_visible()

    page.fill("#note-title", "Shopping")
    page.fill("#note-body", "milk\neggs")
    page.check("#note-as-list")
    page.fill("#note-tags", "Home")
    page.click("#note-form button[type=submit]")

    shopping = card(page, "Shopping")
    expect(shopping.locator("li")).to_have_count(2)
    expect(shopping.locator(".note-tag")).to_have_text(["Home"])
    shopping.locator("input[type=checkbox]").first.check()
    eventually(lambda: [item["done"] for item in notes(api)[0]["checklist"]] == [True, False])

    add_note(api, title="Another")
    page.reload()
    card(page, "Another").hover()
    card(page, "Another").locator("[data-note-action=pin]").click()
    expect(page.locator("#note-list .note-title").first).to_have_text("Another")
    eventually(lambda: any(n["title"] == "Another" and n["pinned"] for n in notes(api)))


def test_the_editor_saves_only_what_changed(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    add_note(api, title="Pasta", body="Boil water", tags=["Recipes"])
    changes: list[str] = []
    page.on("request", lambda r: r.method == "PATCH" and changes.append(r.url))
    open_notes(page, live_server)

    open_editor(page, "Pasta")
    page.click(".note-modal [data-note-action=done]")
    expect(page.locator(".note-modal")).to_have_count(0)
    assert changes == []  # only looking at a note changes nothing, not even its edited date

    editor = open_editor(page, "Pasta")
    editor.locator(".note-edit-title").fill("Pasta night")
    editor.locator(".note-edit-body").fill("Boil water\nAdd salt")
    page.keyboard.press("Escape")  # Escape saves too
    expect(card(page, "Pasta night")).to_contain_text("Add salt")
    assert len(changes) == 1
    assert notes(api)[0]["title"] == "Pasta night"


def test_an_empty_note_stays_open_with_a_message(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    add_note(api, title="Keep me")
    open_notes(page, live_server)

    editor = open_editor(page, "Keep me")
    editor.locator(".note-edit-title").fill("")
    page.click(".note-modal [data-note-action=done]")

    expect(page.locator(".note-edit-error")).to_contain_text("needs a title")
    expect(page.locator(".note-modal")).to_be_visible()
    assert notes(api)[0]["title"] == "Keep me"


def test_checklist_rows_in_the_editor(page: Page, live_server: str, api: httpx.Client) -> None:
    add_note(api, title="List", checklist=[{"text": "milk", "done": False}])
    open_notes(page, live_server)

    editor = open_editor(page, "List")
    rows = editor.locator(".note-edit-item")
    expect(rows).to_have_count(1)
    rows.first.locator(".note-edit-text").press("Enter")  # Enter adds a row below
    expect(rows).to_have_count(2)
    page.keyboard.type("eggs")
    rows.first.locator(".note-edit-check").check()
    page.keyboard.press("Escape")

    eventually(
        lambda: (
            notes(api)[0]["checklist"]
            == [{"text": "milk", "done": True}, {"text": "eggs", "done": False}]
        )
    )


def test_a_text_note_becomes_a_checklist_and_back(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    add_note(api, title="Todo", body="one\ntwo")
    open_notes(page, live_server)

    editor = open_editor(page, "Todo")
    editor.get_by_label("Turn into a checklist").click()
    expect(editor.locator(".note-edit-item")).to_have_count(2)
    expect(editor.locator(".note-edit-body")).to_have_count(0)
    editor.get_by_label("Turn into text").click()
    expect(editor.locator(".note-edit-body")).to_have_value("one\ntwo")


def test_tag_suggestions_offer_existing_tags_and_reuse_their_spelling(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    for title, tags in [
        ("A", ["Airflow", "Work"]),
        ("B", ["Airflow", "Workshop"]),
        ("C", ["Airflow"]),
    ]:
        add_note(api, title=title, body="x", tags=tags)
    open_notes(page, live_server)

    # In the new-note box: focus shows the existing tags, typing narrows them.
    page.click("#note-body")
    page.click("#note-tags")
    options = page.locator("#note-tag-suggest .tag-option")
    expect(options.first).to_contain_text("Airflow")
    page.keyboard.type("wo")
    expect(options).to_have_text(["Work 1", "Workshop 1", "+ New tag “wo”"])
    page.locator("#note-tag-suggest .tag-option", has_text="Workshop").click()
    expect(page.locator("#note-tags")).to_have_value("Workshop, ")
    page.click("#note-close")

    # In the editor: a tag typed in another case takes the spelling already in use.
    editor = open_editor(page, "C")
    editor.locator(".note-tag-input").click()
    expect(editor.locator(".tag-option").first).to_be_visible()
    page.keyboard.type("WORK")
    page.keyboard.press("ArrowDown")  # the first suggestion, "Work"
    page.keyboard.press("Enter")
    expect(editor.locator(".note-tagger .note-tag")).to_have_text(["Airflow×", "Work×"])
    page.keyboard.type("airflowx")  # a new tag
    page.keyboard.press("Enter")
    page.click(".note-modal [data-note-action=done]")
    eventually(
        lambda: (
            next(n for n in notes(api) if n["title"] == "C")["tags"]
            == ["Airflow", "Work", "airflowx"]
        )
    )


def test_the_tag_row_stays_on_one_line_however_many_tags(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    for number in range(40):
        add_note(api, title=f"Note {number}", tags=[f"tag-number-{number}", "shared"])
    open_notes(page, live_server)

    expect(page.locator("#note-list .note").first).to_be_visible()
    assert page.locator("#note-tags-bar").bounding_box()["height"] < 45
    page.click("[data-note-tags-open]")
    panel = page.locator("#note-tag-list button")
    expect(panel).to_have_count(41)
    page.fill("#note-tag-filter", "number-7")
    expect(panel).to_have_text(["tag-number-7 1"])
    panel.first.click()
    expect(page.locator("#note-tag-panel")).to_be_hidden()
    expect(page.locator("#note-list .note")).to_have_count(1)
    expect(page.locator("#note-tags-bar button.active")).to_contain_text("tag-number-7")


def test_google_keep_import_and_export(page: Page, live_server: str, api: httpx.Client) -> None:
    open_notes(page, live_server)
    keep = {"name": "takeout.zip", "mimeType": "application/zip", "buffer": takeout_zip()}

    page.set_input_files("#keep-file", keep)
    expect(page.locator("#keep-import")).to_contain_text("Found 5 new notes")
    assert notes(api) == []  # the preview saves nothing
    page.click("[data-keep=import]")
    expect(page.locator("#keep-import")).to_contain_text("Imported 5 notes")
    assert len(notes(api)) == 5

    page.click("[data-keep=cancel]")
    page.set_input_files("#keep-file", keep)  # the same export again adds nothing
    expect(page.locator("#keep-import")).to_contain_text("0 new notes, 5 already here")
    expect(page.locator("[data-keep=import]")).to_be_disabled()
    page.click("[data-keep=cancel]")

    with page.expect_download() as download:
        page.click("#notes-export")
    archive = zipfile.ZipFile(download.value.path())
    exported = json.loads(archive.read("notes.json"))
    assert exported["count"] == 5 and len(archive.namelist()) == 6


def test_a_long_list_is_drawn_a_screenful_at_a_time(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    for number in range(130):
        add_note(
            api,
            title=f"Note {number:03d}",
            body="text",
            tags=["even" if number % 2 == 0 else "odd"],
        )
    open_notes(page, live_server)
    cards = page.locator("#note-list .note")

    expect(cards).to_have_count(48)  # not all 130 at once
    expect(page.locator("#note-more")).to_contain_text("82 more")
    page.evaluate("document.querySelector('#notes-view main').scrollTo(0, 1e9)")
    expect(cards).to_have_count(96)  # the next screenful loads by itself

    page.fill("#note-search", "Note 000")  # search covers notes that are not drawn yet
    expect(cards).to_have_count(1)
    page.fill("#note-search", "")
    page.locator("#note-tags-bar button", has_text="even").click()
    expect(page.locator("#note-more")).to_contain_text("17 more")  # 65 notes, 48 drawn


def test_delete_can_be_undone_for_a_few_seconds(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    add_note(api, title="Precious", body="keep")
    add_note(api, title="Scrap", body="drop")
    open_notes(page, live_server)

    for title in ("Precious", "Scrap"):
        target = card(page, title)
        target.hover()
        target.locator("[data-note-action=delete]").click()
        target.locator("[data-note-action=delete]").click()  # the second tap confirms
        expect(card(page, title)).to_have_count(0)
        assert len(notes(api)) == 2  # nothing is deleted on the server yet
        if title == "Precious":
            page.click("#note-toast-undo")
            expect(card(page, "Precious")).to_have_count(1)
    eventually(lambda: [n["title"] for n in notes(api)] == ["Precious"], timeout=10)
    expect(page.locator("#note-toast")).to_be_hidden()


def test_keyboard_shortcuts_only_work_when_no_field_has_focus(
    page: Page, live_server: str, api: httpx.Client
) -> None:
    add_note(api, title="Something")
    open_notes(page, live_server)
    expect(card(page, "Something")).to_be_visible()

    page.click("#notes-view h1")
    page.keyboard.press("/")
    expect(page.locator("#note-search")).to_be_focused()
    page.keyboard.type("n")  # typing in a field just types
    expect(page.locator("#note-search")).to_have_value("n")
    page.fill("#note-search", "")
    page.click("#notes-view h1")
    page.keyboard.press("n")
    expect(page.locator("#note-body")).to_be_focused()
    expect(page.locator("#note-title")).to_be_visible()
    page.click("#note-close")

    open_editor(page, "Something")
    page.keyboard.press("/")  # not while a note is open
    expect(page.locator("#note-search")).not_to_be_focused()


def test_the_notes_page_fits_a_phone(phone: Page, live_server: str, api: httpx.Client) -> None:
    for number in range(6):
        add_note(api, title=f"Note {number}", body="a line of text", tags=["Home"])
    phone.goto(f"{live_server}/ui#/notes")
    expect(phone.locator("#note-list .note")).to_have_count(6)

    xs = {
        round(box["x"])
        for box in (c.bounding_box() for c in phone.locator("#note-list .note").all())
    }
    assert len(xs) == 2  # two columns
    assert not phone.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    expect(
        phone.locator("#note-list .note").first.locator(".note-tools")
    ).to_be_visible()  # no hover on a phone
