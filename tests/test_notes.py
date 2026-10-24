from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.persistence.database import Database
from app.work.notes import KeepImportResult, NoteImportError, NoteService

USEC = 1_700_000_000_000_000  # 2023-11-14 22:13:20 UTC


def test_notes_are_listed_pinned_first_then_newest(database: Database) -> None:
    service = NoteService(database)
    old = service.create(title="Old")
    service.create(body="Newer")
    service.create(title="Pinned", pinned=True)

    assert [note.title or note.body for note in service.list()] == ["Pinned", "Newer", "Old"]
    service.update(old.id, body="edited")  # editing moves it to the top of the unpinned
    assert [note.title or note.body for note in service.list()][1] == "Old"


def test_archived_notes_are_hidden_unless_asked(database: Database) -> None:
    service = NoteService(database)
    note = service.create(title="Trip ideas")

    service.update(note.id, archived=True)

    assert service.list() == []
    assert [n.id for n in service.list(include_archived=True)] == [note.id]


def test_tags_are_cleaned_and_deduplicated_ignoring_case(database: Database) -> None:
    note = NoteService(database).create(
        title="Pasta", tags=["Recipes", " #recipes ", "Italian  food", "", "#"]
    )

    assert note.tags == ["Recipes", "Italian food"]


def test_checklists_drop_empty_items_and_keep_order(database: Database) -> None:
    note = NoteService(database).create(
        checklist=[{"text": "milk", "done": True}, {"text": "  "}, {"text": "eggs"}]
    )

    assert note.checklist == [{"text": "milk", "done": True}, {"text": "eggs", "done": False}]


def test_a_note_needs_some_content_and_respects_size_limits(database: Database) -> None:
    service = NoteService(database)
    note = service.create(title="Keep me")

    with pytest.raises(ValueError, match="needs a title"):
        service.create(title="  ", checklist=[{"text": " "}])
    with pytest.raises(ValueError, match="needs a title"):
        service.update(note.id, title="")
    with pytest.raises(ValueError, match="tags"):
        service.create(title="x", tags=[f"tag{n}" for n in range(31)])
    with pytest.raises(ValueError, match="checklist"):
        service.create(checklist=[{"text": "x" * 501}])
    with pytest.raises(LookupError):
        service.update("missing", title="x")


def test_a_deleted_note_is_gone(database: Database) -> None:
    service = NoteService(database)
    note = service.create(title="Delete me")

    service.delete(note.id)

    assert service.list(include_archived=True) == []
    with pytest.raises(LookupError):
        service.delete(note.id)


def keep_note(**fields) -> dict:
    return {
        "color": "DEFAULT",
        "isTrashed": False,
        "isPinned": False,
        "isArchived": False,
        "title": "",
        "textContent": "",
        "createdTimestampUsec": USEC,
        "userEditedTimestampUsec": USEC + 60_000_000,
        **fields,
    }


def takeout_zip() -> bytes:
    """A Takeout export like Google's: Keep notes as JSON, their HTML and images beside them."""
    notes = {
        "Takeout/Keep/Recipe.json": keep_note(
            title="Pasta", textContent="Boil water", labels=[{"name": "Recipes"}], isPinned=True
        ),
        "Takeout/Keep/Shopping.json": keep_note(
            title="Shopping",
            createdTimestampUsec=USEC + 1,
            listContent=[
                {"text": "milk", "isChecked": True, "textHtml": "milk"},
                {"text": "eggs", "isChecked": False, "textHtml": "eggs"},
            ],
        ),
        "Takeout/Keep/Old.json": keep_note(title="Old", isArchived=True, createdTimestampUsec=2),
        "Takeout/Keep/Gone.json": keep_note(title="Gone", isTrashed=True, createdTimestampUsec=3),
        "Takeout/Keep/Photo.json": keep_note(
            title="Receipt",
            createdTimestampUsec=4,
            attachments=[{"filePath": "receipt.jpg", "mimetype": "image/jpeg"}],
            color="YELLOW",
        ),
        "Takeout/Keep/Link.json": keep_note(
            textContent="Read later",
            createdTimestampUsec=5,
            annotations=[{"source": "WEBLINK", "title": "Docs", "url": "https://example.com/a"}],
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, note in notes.items():
            archive.writestr(name, json.dumps(note))
            archive.writestr(name.replace(".json", ".html"), "<html></html>")
        archive.writestr("Takeout/Keep/receipt.jpg", b"\xff\xd8 not really a jpeg")
        archive.writestr("Takeout/Keep/Broken.json", "{ not json")
        archive.writestr("Takeout/Drive/other.json", json.dumps({"title": "not a note"}))
        archive.writestr("Takeout/archive_browser.html", "<html></html>")
    return buffer.getvalue()


def test_a_keep_export_becomes_notes(database: Database, tmp_path: Path) -> None:
    path = tmp_path / "takeout.zip"
    path.write_bytes(takeout_zip())
    service = NoteService(database)

    result = service.import_google_keep(path)

    assert result == KeepImportResult(
        added=5, already_here=0, trashed_skipped=1, images_skipped=1, empty_skipped=0, unreadable=1
    )
    notes = {note.title or note.body[:10]: note for note in service.list(include_archived=True)}
    assert set(notes) == {"Pasta", "Shopping", "Old", "Receipt", "Read later"}
    pasta = notes["Pasta"]
    assert (pasta.body, pasta.tags, pasta.pinned, pasta.source) == (
        "Boil water",
        ["Recipes"],
        True,
        "google-keep",
    )
    assert pasta.created_at.isoformat().startswith("2023-11-14T22:13:20")
    assert pasta.updated_at.isoformat().startswith("2023-11-14T22:14:20")
    assert notes["Shopping"].checklist == [
        {"text": "milk", "done": True},
        {"text": "eggs", "done": False},
    ]
    assert notes["Old"].archived is True
    assert notes["Read later"].body == "Read later\n\nLinks: https://example.com/a"
    assert service.list()[0].title == "Pasta"  # pinned first; archived hidden


def test_a_dry_run_counts_without_saving_and_a_second_import_adds_nothing(
    database: Database, tmp_path: Path
) -> None:
    path = tmp_path / "takeout.zip"
    path.write_bytes(takeout_zip())
    service = NoteService(database)

    preview = service.import_google_keep(path, dry_run=True)
    assert preview.added == 5
    assert service.list(include_archived=True) == []

    service.import_google_keep(path)
    again = service.import_google_keep(path)

    assert (again.added, again.already_here) == (0, 5)
    assert len(service.list(include_archived=True)) == 5


def test_a_file_that_is_not_a_keep_export_is_refused(database: Database, tmp_path: Path) -> None:
    service = NoteService(database)
    not_zip = tmp_path / "notes.txt"
    not_zip.write_text("hello")
    empty_zip = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty_zip, "w") as archive:
        archive.writestr("Takeout/Drive/a.json", "{}")

    with pytest.raises(NoteImportError, match="isn't a zip"):
        service.import_google_keep(not_zip)
    with pytest.raises(NoteImportError, match="No Google Keep notes"):
        service.import_google_keep(empty_zip)


def test_notes_api_round_trip(client: TestClient) -> None:
    created = client.post(
        "/notes", json={"title": "Ideas", "tags": ["Home"], "checklist": [{"text": "paint"}]}
    )
    assert created.status_code == 201
    note = created.json()
    assert note["checklist"] == [{"text": "paint", "done": False}]

    patched = client.patch(
        f"/notes/{note['id']}", json={"checklist": [{"text": "paint", "done": True}]}
    )
    assert patched.status_code == 200
    assert patched.json()["checklist"][0]["done"] is True
    assert patched.json()["title"] == "Ideas"  # fields not sent stay as they were

    assert [n["id"] for n in client.get("/notes").json()["notes"]] == [note["id"]]
    assert client.patch(f"/notes/{note['id']}", json={"archived": True}).status_code == 200
    assert client.get("/notes").json()["notes"] == []
    assert len(client.get("/notes?include_archived=true").json()["notes"]) == 1

    assert client.delete(f"/notes/{note['id']}").status_code == 204
    assert client.delete(f"/notes/{note['id']}").status_code == 404
    assert client.patch("/notes/missing", json={"title": "x"}).status_code == 404
    assert client.post("/notes", json={"title": " "}).status_code == 422


def test_keep_import_api_previews_then_imports(client: TestClient) -> None:
    upload = takeout_zip()
    headers = {"content-type": "application/zip"}

    preview = client.post("/notes/import/google-keep?dry_run=true", content=upload, headers=headers)
    assert preview.status_code == 200
    assert preview.json() == {
        "dry_run": True,
        "added": 5,
        "already_here": 0,
        "trashed_skipped": 1,
        "images_skipped": 1,
        "empty_skipped": 0,
        "unreadable": 1,
    }
    assert client.get("/notes?include_archived=true").json()["notes"] == []

    imported = client.post("/notes/import/google-keep", content=upload, headers=headers)
    assert imported.json()["added"] == 5
    assert len(client.get("/notes?include_archived=true").json()["notes"]) == 5

    bad = client.post("/notes/import/google-keep", content=b"plain text", headers=headers)
    assert bad.status_code == 422
    assert client.post("/notes/import/google-keep", content=b"").status_code == 422


def _zip_of(notes: dict[str, dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, note in notes.items():
            archive.writestr(name, json.dumps(note))
    return buffer.getvalue()


def test_a_real_export_with_odd_notes_loses_nothing(database: Database, tmp_path: Path) -> None:
    # Shapes found in a real 1,651-note export: pasted text in the title, a title over
    # several lines, links already typed in the text, empty and image-only notes, a note
    # whose file name starts with a dot, and a Google Tasks link.
    pasted = "Pasted into the title " + "word " * 80
    notes = {
        "Takeout/Keep/long.json": keep_note(
            title=pasted, textContent="and a body", createdTimestampUsec=11
        ),
        "Takeout/Keep/lines.json": keep_note(
            title="Exploratory Data Analysis for\nFeature Selection\n in Machine\nLearning",
            createdTimestampUsec=12,
        ),
        "Takeout/Keep/typed.json": keep_note(
            textContent="see https://example.com/a for more",
            createdTimestampUsec=13,
            annotations=[
                {"url": "https://example.com/a", "title": "A", "source": "WEBLINK"},
                {"url": "https://example.com/b", "title": "B", "source": "WEBLINK"},
                {"url": "https://example.com/b", "title": "B again", "source": "WEBLINK"},
            ],
        ),
        "Takeout/Keep/.terraform.lock.hcl.json": keep_note(
            title=".terraform.lock.hcl", createdTimestampUsec=14
        ),
        "Takeout/Keep/tasks.json": keep_note(
            title="check errors", createdTimestampUsec=15, tasks=[{"id": "abc"}]
        ),
        "Takeout/Keep/blank.json": keep_note(createdTimestampUsec=16),
        "Takeout/Keep/image-only.json": keep_note(
            createdTimestampUsec=17,
            labels=[{"name": "Receipts"}],
            attachments=[{"filePath": "a.png", "mimetype": "image/png"}],
        ),
    }
    path = tmp_path / "takeout.zip"
    path.write_bytes(_zip_of(notes))
    service = NoteService(database)

    result = service.import_google_keep(path)

    assert result == KeepImportResult(added=5, images_skipped=1, empty_skipped=2)
    by_key = {note.source_key.split(":")[1]: note for note in service.list(include_archived=True)}
    long = by_key["11"]
    assert len(long.title) <= 300 and long.title.endswith("…")
    assert long.body == pasted.strip() + "\n\nand a body"  # nothing lost
    assert (
        by_key["12"].title == "Exploratory Data Analysis for Feature Selection in Machine Learning"
    )
    assert by_key["13"].body == "see https://example.com/a for more\n\nLinks: https://example.com/b"
    assert by_key["14"].title == ".terraform.lock.hcl"
    assert by_key["15"].title == "check errors"
