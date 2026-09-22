from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.capabilities.models import IDENTIFIER_PATTERN


class RepositoryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    aliases: tuple[str, ...] = ()
    github_repository: str = Field(pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
    path_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    base_branch: str = "main"
    remote: str = "origin"
    validation_profile: str
    enabled: bool = True
    default: bool = False

    @field_validator("id")
    @classmethod
    def id_is_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("repository id must be a lowercase identifier")
        return value

    @field_validator("aliases")
    @classmethod
    def aliases_are_normalized(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(alias.strip().lower() for alias in values if alias.strip())
        if len(set(normalized)) != len(normalized):
            raise ValueError("repository aliases must be unique")
        return normalized
