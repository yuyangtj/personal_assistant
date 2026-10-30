"""What the assistant can do, as one registry (abilities/*.yaml).

Each ability says what it does, whether it is quick or a background job, and its band:
done freely, done and reviewed by the user, done only after asking, or never done. The
assistant's description of itself is generated from this list, so it can only offer what
exists (see docs/assistant-design.md). These are not the executor capabilities in
capabilities/, which route tasks to agents inside the system.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.identifiers import IDENTIFIER_PATTERN


class Band(StrEnum):
    FREE = "free"
    REVIEW = "review"
    ASK = "ask"
    NEVER = "never"


class AbilityKind(StrEnum):
    QUICK = "quick"
    JOB = "job"


#: What an ability needs from the server's configuration to be available.
REQUIREMENTS: dict[str, Callable[[Any], bool]] = {
    "github_token": lambda settings: bool(settings.github_token),
    "web_search": lambda settings: settings.web_search_available,
}

#: How each band is introduced to the model, in this order.
BAND_HEADINGS = {
    Band.FREE: "Done without asking",
    Band.REVIEW: "Done, and the user reviews the result before it takes effect",
    Band.ASK: "Only after the user confirms",
    Band.NEVER: "Never done by the assistant",
}


class Ability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    name: str = Field(min_length=1, max_length=80)
    #: What it does, in the words the assistant uses about itself.
    summary: str = Field(min_length=1, max_length=300)
    example: str | None = Field(default=None, max_length=120)
    kind: AbilityKind
    band: Band
    requires: tuple[Literal["github_token", "web_search"], ...] = ()
    order: int = 100

    def available(self, settings: Any) -> bool:
        return all(REQUIREMENTS[requirement](settings) for requirement in self.requires)


class AbilityRegistryError(ValueError):
    pass


class AbilityRegistry:
    def __init__(self, abilities: Iterable[Ability]):
        self._abilities: dict[str, Ability] = {}
        for ability in abilities:
            if ability.id in self._abilities:
                raise AbilityRegistryError(f"Duplicate ability id: {ability.id}")
            self._abilities[ability.id] = ability

    @classmethod
    def from_directory(cls, directory: str | Path) -> AbilityRegistry:
        root = Path(directory)
        if not root.is_dir():
            raise AbilityRegistryError(f"Ability directory does not exist: {root}")
        abilities = []
        for path in sorted(root.glob("*.yaml")):
            try:
                abilities.append(Ability.model_validate(yaml.safe_load(path.read_text("utf-8"))))
            except (OSError, yaml.YAMLError, ValidationError) as error:
                raise AbilityRegistryError(f"Invalid ability {path}: {error}") from error
        return cls(abilities)

    def list(self) -> list[Ability]:
        return sorted(self._abilities.values(), key=lambda ability: (ability.order, ability.id))

    def available(self, settings: Any) -> list[Ability]:
        return [ability for ability in self.list() if ability.available(settings)]


def abilities_prompt(abilities: Iterable[Ability]) -> str:
    """The assistant's description of itself, for a model's system prompt."""
    abilities = list(abilities)
    lines = [
        "What the assistant can do. This is the complete list: never promise, offer or "
        "invent anything that is not on it (no made-up workflows, tools or steps). If the "
        "user asks for something that is not listed, say the assistant can't do that yet "
        "and offer the nearest thing that is."
    ]
    for band, heading in BAND_HEADINGS.items():
        listed = [ability for ability in abilities if ability.band == band]
        if not listed:
            continue
        lines.append(f"{heading}:")
        for ability in listed:
            example = f' (for example "{ability.example}")' if ability.example else ""
            lines.append(f"- {ability.name}: {ability.summary}{example}")
    return "\n".join(lines)
