"""Notes: a casual pile of notes with tags and checklists, like Google Keep.

Unlike memories, notes are never given to a chat model or any other service; they are
only shown in the console. Notes exported from Google Keep (Google Takeout) can be
imported; images and colours are left out.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.persistence.database import Database
from app.persistence.models import NoteModel, utc_now

MAX_TITLE = 300
MAX_BODY = 20_000
MAX_ITEMS = 200
MAX_ITEM = 500
MAX_TAGS = 30
MAX_TAG = 50
KEEP_SOURCE = "google-keep"
#: Fields update() may change; anything else is a programming error.
_EDITABLE = {"title", "body", "checklist", "tags", "pinned", "archived"}


class NoteImportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KeepImportResult:
    added: int = 0
    already_here: int = 0
    trashed_skipped: int = 0
    images_skipped: int = 0
    unreadable: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def normalize_tags(tags: list[str] | None) -> list[str]:
    """Trimmed tags without a leading #, de-duplicated ignoring case (first spelling wins)."""
    seen: dict[str, str] = {}
    for tag in tags or []:
        cleaned = " ".join(str(tag).split()).lstrip("#").strip()
        if cleaned and cleaned.lower() not in seen:
            seen[cleaned.lower()] = cleaned
    result = list(seen.values())
    if len(result) > MAX_TAGS or any(len(tag) > MAX_TAG for tag in result):
        raise ValueError(f"at most {MAX_TAGS} tags of {MAX_TAG} characters each")
    return result


def normalize_checklist(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result = [
        {"text": " ".join(str(item.get("text", "")).split()), "done": bool(item.get("done"))}
        for item in items or []
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    if len(result) > MAX_ITEMS or any(len(item["text"]) > MAX_ITEM for item in result):
        raise ValueError(f"at most {MAX_ITEMS} checklist items of {MAX_ITEM} characters each")
    return result


def _check(title: str, body: str, checklist: list[dict[str, Any]]) -> None:
    if len(title) > MAX_TITLE:
        raise ValueError(f"the title can be at most {MAX_TITLE} characters")
    if len(body) > MAX_BODY:
        raise ValueError(f"the text can be at most {MAX_BODY} characters")
    if not (title.strip() or body.strip() or checklist):
        raise ValueError("a note needs a title, some text or a checklist item")


class NoteService:
    def __init__(self, database: Database):
        self.database = database

    def list(self, *, include_archived: bool = False) -> list[NoteModel]:
        with self.database.session() as session:
            query = select(NoteModel)
            if not include_archived:
                query = query.where(NoteModel.archived.is_(False))
            query = query.order_by(NoteModel.pinned.desc(), NoteModel.updated_at.desc())
            return list(session.scalars(query))

    def get(self, note_id: str) -> NoteModel:
        with self.database.session() as session:
            note = session.get(NoteModel, note_id)
            if note is None:
                raise LookupError(note_id)
            return note

    def create(
        self,
        *,
        title: str = "",
        body: str = "",
        checklist: list[dict[str, Any]] | None = None,
        tags: list[str] | None = None,
        pinned: bool = False,
    ) -> NoteModel:
        items = normalize_checklist(checklist)
        title = title.strip()
        _check(title, body, items)
        note = NoteModel(
            id=str(uuid4()),
            title=title,
            body=body,
            checklist=items,
            tags=normalize_tags(tags),
            pinned=pinned,
            archived=False,
        )
        with self.database.session() as session, session.begin():
            session.add(note)
        return note

    def update(self, note_id: str, **fields: Any) -> NoteModel:
        unknown = set(fields) - _EDITABLE
        if unknown:
            raise TypeError(f"not editable: {sorted(unknown)}")
        with self.database.session() as session, session.begin():
            note = session.get(NoteModel, note_id, with_for_update=True)
            if note is None:
                raise LookupError(note_id)
            if fields.get("title") is not None:
                note.title = str(fields["title"]).strip()
            if fields.get("body") is not None:
                note.body = str(fields["body"])
            if fields.get("checklist") is not None:
                note.checklist = normalize_checklist(fields["checklist"])
            if fields.get("tags") is not None:
                note.tags = normalize_tags(fields["tags"])
            for flag in ("pinned", "archived"):
                if fields.get(flag) is not None:
                    setattr(note, flag, bool(fields[flag]))
            _check(note.title, note.body, note.checklist)
            note.updated_at = utc_now()
        return note

    def delete(self, note_id: str) -> None:
        with self.database.session() as session, session.begin():
            note = session.get(NoteModel, note_id)
            if note is None:
                raise LookupError(note_id)
            session.delete(note)

    def import_google_keep(self, zip_path: Path, *, dry_run: bool = False) -> KeepImportResult:
        """Import the notes in a Google Takeout zip; notes imported before are left alone."""
        try:
            archive = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError) as error:
            raise NoteImportError("That file isn't a zip archive.") from error
        counts = {key: 0 for key in KeepImportResult().as_dict()}
        notes: list[NoteModel] = []
        with archive:
            entries = [
                info
                for info in archive.infolist()
                if info.filename.lower().endswith(".json")
                and "keep" in [part.lower() for part in Path(info.filename).parts[:-1]]
            ]
            if not entries:
                raise NoteImportError("No Google Keep notes in that zip (no Keep/*.json files).")
            for info in entries:
                try:
                    raw = json.loads(archive.read(info).decode("utf-8"))
                    note = _keep_note(raw)
                except (ValueError, UnicodeDecodeError, KeyError, TypeError):
                    counts["unreadable"] += 1
                    continue
                if note is None:
                    counts["trashed_skipped"] += 1
                    continue
                counts["images_skipped"] += len(raw.get("attachments") or [])
                notes.append(note)
        with self.database.session() as session, session.begin():
            keys = [note.source_key for note in notes]
            existing = (
                set(
                    session.scalars(
                        select(NoteModel.source_key).where(NoteModel.source_key.in_(keys))
                    )
                )
                if keys
                else set()
            )
            new: dict[str, NoteModel] = {}
            for note in notes:
                if note.source_key in existing or note.source_key in new:
                    counts["already_here"] += 1
                else:
                    new[str(note.source_key)] = note
            counts["added"] = len(new)
            if not dry_run:
                session.add_all(new.values())
        return KeepImportResult(**counts)


def _keep_note(raw: Any) -> NoteModel | None:
    """A note from one Keep JSON file; None for a trashed note. Raises on a broken file."""
    if not isinstance(raw, dict):
        raise ValueError("not a Keep note")
    if raw.get("isTrashed"):
        return None
    title = str(raw.get("title") or "").strip()[:MAX_TITLE]
    body = str(raw.get("textContent") or "")
    links = [
        str(annotation["url"])
        for annotation in raw.get("annotations") or []
        if isinstance(annotation, dict) and annotation.get("url")
    ]
    if links:
        body = (body.rstrip() + "\n\n" if body.strip() else "") + "Links: " + " ".join(links)
    checklist = [
        {"text": str(item.get("text", ""))[:MAX_ITEM], "done": bool(item.get("isChecked"))}
        for item in raw.get("listContent") or []
        if isinstance(item, dict)
    ]
    checklist = normalize_checklist(checklist[:MAX_ITEMS])
    tags = [str(label.get("name", ""))[:MAX_TAG] for label in raw.get("labels") or []]
    created = _from_usec(raw.get("createdTimestampUsec"))
    edited = _from_usec(raw.get("userEditedTimestampUsec")) or created
    if created is None:
        raise ValueError("a Keep note has a creation time")
    if not (title or body.strip() or checklist):
        title = "(empty note)"  # e.g. an image-only note: keep it so its tags survive
    digest = hashlib.sha1(title.encode()).hexdigest()[:8]
    return NoteModel(
        id=str(uuid4()),
        title=title,
        body=body[:MAX_BODY],
        checklist=checklist,
        tags=normalize_tags(tags[:MAX_TAGS]),
        pinned=bool(raw.get("isPinned")),
        archived=bool(raw.get("isArchived")),
        source=KEEP_SOURCE,
        source_key=f"keep:{raw['createdTimestampUsec']}:{digest}",
        created_at=created,
        updated_at=edited,
    )


def _from_usec(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)
