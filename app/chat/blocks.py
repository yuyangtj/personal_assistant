"""Small pieces of UI an assistant message can carry.

The console renders each block type with its own pre-built component, so a reply only
ever carries data: which component, with what values. Nothing here is markup, and a
model can never inject any. Choices hold their server-side payload (goal, repository,
origin message) in the stored message; a client only sends the label it picked back as
an ordinary message.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

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


Block = Annotated[
    ChoicesBlock | LinkBlock | WorkflowBlock | ItemBlock | UploadBlock,
    Field(discriminator="type"),
]
_BLOCKS = TypeAdapter(list[Block])
MAX_BLOCKS = 4


def validate_blocks(raw: Any) -> list[dict[str, Any]]:
    """Checked, normalized blocks; raises ValueError for anything off-contract."""
    try:
        blocks = _BLOCKS.validate_python(raw)
    except ValidationError as error:
        raise ValueError(str(error).splitlines()[0]) from error
    if len(blocks) > MAX_BLOCKS:
        raise ValueError(f"at most {MAX_BLOCKS} blocks")
    return [block.model_dump(mode="json") for block in blocks]


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
