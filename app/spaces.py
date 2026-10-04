"""Space packs: the domains work items live in, declared in ``spaces/*.yaml``.

Each pack names a space and describes it so triage can place new work items. More of a
pack (its tools, agent profiles, and workflows) moves into the YAML as those parts grow.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.work_items import SLUG_PATTERN


class SpaceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=SLUG_PATTERN.pattern, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=500)
    aliases: tuple[str, ...] = ()


class SpaceRegistry:
    def __init__(self, manifests: list[SpaceManifest]):
        self._spaces = {manifest.slug: manifest for manifest in manifests}
        if len(self._spaces) != len(manifests):
            raise ValueError("Duplicate space slug")
        for required in ("general", "coding"):
            if required not in self._spaces:
                raise ValueError(f"The {required} space must be declared")

    @classmethod
    def from_directory(cls, directory: str | Path) -> SpaceRegistry:
        root = Path(directory)
        if not root.is_dir():
            raise ValueError(f"Space directory does not exist: {root}")
        manifests = []
        for path in sorted(root.glob("*.yaml")):
            try:
                manifests.append(SpaceManifest.model_validate(yaml.safe_load(path.read_text())))
            except (OSError, yaml.YAMLError, ValidationError) as error:
                raise ValueError(f"Invalid space manifest {path}: {error}") from error
        return cls(manifests)

    def get(self, slug: str) -> SpaceManifest | None:
        return self._spaces.get(slug)

    def list(self) -> list[SpaceManifest]:
        return sorted(self._spaces.values(), key=lambda manifest: manifest.slug)
