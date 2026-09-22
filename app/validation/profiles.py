from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.capabilities.models import IDENTIFIER_PATTERN


class ValidationStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    required: bool = True

    @field_validator("id")
    @classmethod
    def id_is_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("validation step id must be a lowercase identifier")
        return value


class ValidationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str = Field(min_length=1, max_length=120)
    steps: tuple[ValidationStep, ...] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def id_is_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("validation profile id must be a lowercase identifier")
        return value


class ValidationProfileRegistry:
    def __init__(self, profiles: list[ValidationProfile]):
        self._profiles = {profile.id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("Duplicate validation profile id")

    @classmethod
    def from_directory(cls, directory: str | Path) -> ValidationProfileRegistry:
        root = Path(directory)
        profiles: list[ValidationProfile] = []
        if not root.is_dir():
            raise ValueError(f"Validation profile directory does not exist: {root}")
        for path in sorted(root.glob("*.yaml")):
            try:
                profiles.append(
                    ValidationProfile.model_validate(
                        yaml.safe_load(path.read_text(encoding="utf-8"))
                    )
                )
            except (OSError, yaml.YAMLError, ValidationError) as error:
                raise ValueError(f"Invalid validation profile {path}: {error}") from error
        if not profiles:
            raise ValueError("Validation profile directory contains no YAML manifests")
        return cls(profiles)

    def get(self, profile_id: str) -> ValidationProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as error:
            raise ValueError(f"Unknown validation profile: {profile_id}") from error

    def list(self) -> list[ValidationProfile]:
        return sorted(self._profiles.values(), key=lambda profile: profile.id)
