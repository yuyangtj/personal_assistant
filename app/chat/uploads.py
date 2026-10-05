"""Files the user hands over in the chat, through an upload button in a reply.

An agent that needs a file (a Klarna export, say) offers an upload: its tool result says
``"offer_upload": "<target>"`` and the reply carries an upload block for that target. The
user picks the file in the chat; the server checks the block is open and hands the file to
the target's handler, whose summary becomes the next assistant message. A new kind of file
is one more entry in ``TARGETS``; no new screen or flow.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from app.work.purchases import PurchaseImportError, PurchaseService

MAX_UPLOAD_CHARACTERS = 5_000_000


class UploadError(ValueError):
    pass


@dataclass(frozen=True)
class UploadTarget:
    id: str
    label: str
    accept: str
    #: (app state, file text) -> the reply to post; raises UploadError for a bad file.
    handle: Callable[[Any, str], str]


def _klarna(state: Any, text: str) -> str:
    service = PurchaseService(state.database)
    try:
        result = service.import_klarna_csv(text)
    except PurchaseImportError as error:
        raise UploadError(str(error)) from error
    overview = service.overview()
    return (
        f"Imported your Klarna export: {result.added} new purchases, {result.updated} updated, "
        f"{result.unchanged} already there. I now have {overview['count']} purchases from "
        f"{overview['first']} to {overview['last']}. Ask me anything about them."
    )


TARGETS = {
    target.id: target
    for target in (
        UploadTarget("klarna-purchases", "Upload Klarna export (CSV)", ".csv,text/csv", _klarna),
    )
}


def upload_block(target_id: str) -> dict[str, Any]:
    target = TARGETS[target_id]
    return {"type": "upload", "target": target.id, "label": target.label, "accept": target.accept}


def offered_uploads(tool_result: dict[str, Any]) -> list[str]:
    """Targets a tool result asks to offer (known ones only)."""
    offers = []
    for part in tool_result.get("content", []):
        try:
            payload = json.loads(part.get("text", ""))
        except (AttributeError, TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("offer_upload") in TARGETS:
            offers.append(payload["offer_upload"])
    return offers


def upload_blocks(targets: Iterable[str]) -> list[dict[str, Any]]:
    return [upload_block(target) for target in dict.fromkeys(targets)]
