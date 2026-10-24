"""Notes in the Notes tab, and importing them from a Google Keep (Takeout) export."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.schemas import (
    CreateNoteRequest,
    KeepImportResponse,
    NoteListResponse,
    NoteResponse,
    UpdateNoteRequest,
)
from app.work.notes import NoteImportError

router = APIRouter()

#: A Takeout zip can hold years of photos; only its small JSON files are read.
MAX_IMPORT_BYTES = 1024 * 1024 * 1024


@router.get("/notes", response_model=NoteListResponse)
def list_notes(request: Request, include_archived: bool = False) -> NoteListResponse:
    notes = request.app.state.note_service.list(include_archived=include_archived)
    return NoteListResponse(notes=[NoteResponse.model_validate(note) for note in notes])


@router.get("/notes/export")
def export_notes(request: Request) -> Response:
    """All notes (archived too) as a zip: notes.json plus one Markdown file per note."""
    data = request.app.state.note_service.export_zip()
    filename = f"notes-{datetime.now(UTC).date().isoformat()}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/notes", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
def create_note(body: CreateNoteRequest, request: Request) -> NoteResponse:
    try:
        note = request.app.state.note_service.create(
            title=body.title,
            body=body.body,
            checklist=[item.model_dump() for item in body.checklist],
            tags=body.tags,
            pinned=body.pinned,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return NoteResponse.model_validate(note)


@router.patch("/notes/{note_id}", response_model=NoteResponse)
def update_note(note_id: str, body: UpdateNoteRequest, request: Request) -> NoteResponse:
    fields = body.model_dump(exclude_unset=True)
    try:
        note = request.app.state.note_service.update(note_id, **fields)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Note not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return NoteResponse.model_validate(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: str, request: Request) -> Response:
    try:
        request.app.state.note_service.delete(note_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Note not found") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notes/import/google-keep", response_model=KeepImportResponse)
async def import_google_keep(request: Request, dry_run: bool = False) -> KeepImportResponse:
    """The body is the Takeout .zip as is; images in it are skipped, not stored."""
    with tempfile.TemporaryDirectory(prefix="keep-import-") as directory:
        path = Path(directory) / "takeout.zip"
        size = 0
        with path.open("wb") as file:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise HTTPException(status_code=413, detail="That export is over 1 GB.")
                file.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="The upload was empty.")
        try:
            result = request.app.state.note_service.import_google_keep(path, dry_run=dry_run)
        except NoteImportError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    return KeepImportResponse(dry_run=dry_run, **result.as_dict())
