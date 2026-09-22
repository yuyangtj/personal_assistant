from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.capabilities.models import IDENTIFIER_PATTERN


class DeploymentStrategy(StrEnum):
    COMPOSE_PULL_REDEPLOY = "compose_pull_redeploy"
    GITHUB_ACTIONS = "github_actions"


class DeploymentTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str = Field(min_length=1, max_length=120)
    repository_id: str
    environment: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    strategy: DeploymentStrategy
    requires_approval: bool = True
    enabled: bool = True

    @field_validator("id", "repository_id")
    @classmethod
    def identifiers(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("must be a lowercase identifier")
        return value


class DeploymentRegistry:
    def __init__(self, targets: list[DeploymentTarget]):
        self._targets = {target.id: target for target in targets}
        if len(self._targets) != len(targets):
            raise ValueError("Duplicate deployment target id")

    @classmethod
    def from_directory(cls, directory: str | Path) -> DeploymentRegistry:
        root = Path(directory)
        if not root.is_dir():
            raise ValueError(f"Deployment target directory does not exist: {root}")
        targets: list[DeploymentTarget] = []
        for path in sorted(root.glob("*.yaml")):
            try:
                targets.append(DeploymentTarget.model_validate(yaml.safe_load(path.read_text())))
            except (OSError, yaml.YAMLError, ValidationError) as error:
                raise ValueError(f"Invalid deployment target {path}: {error}") from error
        if not targets:
            raise ValueError("Deployment target directory contains no YAML manifests")
        return cls(targets)

    def get(self, target_id: str) -> DeploymentTarget:
        try:
            return self._targets[target_id]
        except KeyError as error:
            raise LookupError(f"Unknown deployment target: {target_id}") from error

    def list(self) -> list[DeploymentTarget]:
        return sorted(self._targets.values(), key=lambda item: item.id)
