"""Small pieces of UI an assistant message can carry.

The console renders each block type with its own pre-built component, so a reply only
ever carries data: which component, with what values. Nothing here is markup, and a
model can never inject any. Choices hold their server-side payload (goal, repository,
origin message) in the stored message; a client only sends the label it picked back as
an ordinary message.
"""

from __future__ import annotations

import json
import math
from contextlib import suppress
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

MAX_CHART_SERIES = 6
MAX_CHART_POINTS = 200
MAX_TABLE_ROWS = 50

ChoiceAction = Literal["start_coding", "start_research", "start_task", "answer", "reply"]


class ChoiceOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=60)
    #: What picking it does; ``reply`` only sends the label on as a message.
    action: ChoiceAction = "reply"
    #: The option a plain "yes" picks.
    primary: bool = False
    #: The option a plain "no" picks.
    decline: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class ChoicesBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["choices"] = "choices"
    id: str = Field(default_factory=lambda: str(uuid4()))
    options: list[ChoiceOption] = Field(min_length=1, max_length=4)
    #: open until answered; lapsed when the next message ignored it.
    state: Literal["open", "chosen", "lapsed"] = "open"
    chosen: str | None = None


class LinkBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["link"] = "link"
    label: str = Field(min_length=1, max_length=80)
    href: str = Field(max_length=2000)

    @field_validator("href")
    @classmethod
    def _safe_href(cls, value: str) -> str:
        # A console route or a web page; never javascript:, data: or the like.
        if value.startswith("#/") or value.startswith("https://"):
            return value
        raise ValueError("links must be https:// URLs or console routes (#/...)")


class WorkflowBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["workflow"] = "workflow"
    workflow_run_id: str = Field(min_length=1, max_length=36)


class ItemBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["item"] = "item"
    slug: str = Field(min_length=1, max_length=80)


class UploadBlock(BaseModel):
    """A button to hand over a file for a registered purpose (see app/chat/uploads.py)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["upload"] = "upload"
    id: str = Field(default_factory=lambda: str(uuid4()))
    target: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    label: str = Field(min_length=1, max_length=60)
    accept: str = Field(default="", max_length=100)
    state: Literal["open", "done"] = "open"
    #: What the upload led to, once done (e.g. "1051 purchases imported").
    result: str | None = Field(default=None, max_length=300)


class AttachmentBlock(BaseModel):
    """Context the user attached to their message, e.g. the text of the screen they were on.

    It came from another app, so it is untrusted data for every agent that sees it.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["attachment"] = "attachment"
    kind: Literal["screen"] = "screen"
    app: str = Field(default="", max_length=80)
    text: str = Field(min_length=1, max_length=8_000)


class ChartAxis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=60)
    #: ``time`` x values are ISO dates or months (YYYY-MM-DD, YYYY-MM); ``category`` is any label.
    type: Literal["time", "category"] = "category"
    unit: str = Field(default="", max_length=20)


class ChartSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    #: [x, y] pairs, x as text and y a finite number.
    points: list[tuple[str, float]] = Field(min_length=1, max_length=MAX_CHART_POINTS)

    @field_validator("points")
    @classmethod
    def _finite(cls, points: list[tuple[str, float]]) -> list[tuple[str, float]]:
        for x, y in points:
            if not math.isfinite(y) or len(x) > 60:
                raise ValueError("chart points need a short x and a finite y")
        return points


class ChartBlock(BaseModel):
    """A line, area or bar chart of a few series; the console draws it, interactively."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["chart"] = "chart"
    kind: Literal["line", "area", "bar"] = "line"
    title: str = Field(min_length=1, max_length=120)
    x: ChartAxis = Field(default_factory=ChartAxis)
    y: ChartAxis = Field(default_factory=ChartAxis)
    series: list[ChartSeries] = Field(min_length=1, max_length=MAX_CHART_SERIES)


class TableBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["table"] = "table"
    title: str = Field(default="", max_length=120)
    columns: list[Annotated[str, Field(max_length=60)]] = Field(min_length=1, max_length=8)
    rows: list[list[Annotated[str, Field(max_length=200)]]] = Field(max_length=MAX_TABLE_ROWS)

    @field_validator("rows")
    @classmethod
    def _as_wide_as_columns(cls, rows: list[list[str]], info: Any) -> list[list[str]]:
        width = len(info.data.get("columns", []))
        if any(len(row) != width for row in rows):
            raise ValueError("table rows must have one cell per column")
        return rows


Block = Annotated[
    ChoicesBlock
    | LinkBlock
    | WorkflowBlock
    | ItemBlock
    | UploadBlock
    | AttachmentBlock
    | ChartBlock
    | TableBlock,
    Field(discriminator="type"),
]
_BLOCKS = TypeAdapter(list[Block])
MAX_BLOCKS = 4
#: Serialized size of one block; keeps a message small whatever a tool returned.
MAX_BLOCK_BYTES = 24_000


def validate_blocks(raw: Any) -> list[dict[str, Any]]:
    """Checked, normalized blocks; raises ValueError for anything off-contract."""
    try:
        blocks = _BLOCKS.validate_python(raw)
    except ValidationError as error:
        raise ValueError(str(error).splitlines()[0]) from error
    if len(blocks) > MAX_BLOCKS:
        raise ValueError(f"at most {MAX_BLOCKS} blocks")
    dumped = [block.model_dump(mode="json") for block in blocks]
    if any(len(json.dumps(block)) > MAX_BLOCK_BYTES for block in dumped):
        raise ValueError(f"a block is over {MAX_BLOCK_BYTES} bytes")
    return dumped


def take_tool_blocks(result: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Blocks a tool built from its own data (a ``blocks`` key in its JSON result).

    Returns the valid ones, and the result without that key so the model is not shown the
    same numbers twice. Only tools the server runs can do this; a model's own reply never
    gets here (see ``model_blocks``). Invalid blocks are dropped.
    """
    found: list[dict[str, Any]] = []
    parts = []
    for part in result.get("content", []):
        try:
            payload = json.loads(part.get("text", ""))
        except (AttributeError, TypeError, ValueError):
            parts.append(part)
            continue
        if isinstance(payload, dict) and "blocks" in payload:
            raw = payload.pop("blocks")
            with suppress(ValueError):
                found += validate_blocks(raw)
            part = {**part, "text": json.dumps(payload, ensure_ascii=False)}
        parts.append(part)
    return found[:MAX_BLOCKS], {**result, "content": parts}


def model_blocks(decoded: dict[str, Any]) -> list[dict[str, Any]]:
    """Blocks a chat model asked for in its JSON reply: plain choices and links only.

    Anything invalid is dropped rather than failing the reply.
    """
    blocks: list[dict[str, Any]] = []
    choices = decoded.get("choices")
    if isinstance(choices, list) and choices:
        with suppress(ValueError):
            blocks += validate_blocks(
                [{"type": "choices", "options": [{"label": str(c)} for c in choices]}]
            )
    links = decoded.get("links")
    if isinstance(links, list):
        for link in links[:2]:
            if not isinstance(link, dict):
                continue
            try:
                blocks += validate_blocks(
                    [{"type": "link", "label": link.get("label"), "href": link.get("href")}]
                )
            except ValueError:
                continue
    return blocks
