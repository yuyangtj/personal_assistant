from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")


class CapabilityKind(StrEnum):
    MODEL = "model"
    AGENT = "agent"
    TOOL = "tool"


class Availability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    concurrency: int = Field(default=1, ge=1, le=1000)


class ExecutionConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: str
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)

    @field_validator("adapter")
    @classmethod
    def adapter_is_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("adapter must be a lowercase identifier")
        return value


class RiskConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_level: int = Field(default=0, ge=0, le=5)
    destructive_actions_require_approval: bool = True


class RoutingConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    priority: int = Field(default=0, ge=-1000, le=1000)


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str = Field(min_length=1, max_length=120)
    kind: CapabilityKind
    description: str = Field(min_length=1, max_length=1000)
    provides: tuple[str, ...] = Field(min_length=1)
    availability: Availability = Field(default_factory=Availability)
    execution: ExecutionConfiguration
    risk: RiskConfiguration = Field(default_factory=RiskConfiguration)
    routing: RoutingConfiguration = Field(default_factory=RoutingConfiguration)

    @field_validator("id")
    @classmethod
    def id_is_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("id must be a lowercase identifier")
        return value

    @field_validator("provides")
    @classmethod
    def capabilities_are_unique_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("provides must not contain duplicates")
        invalid = [value for value in values if not IDENTIFIER_PATTERN.fullmatch(value)]
        if invalid:
            raise ValueError(f"invalid capability identifiers: {invalid}")
        return values

    def provides_all(self, requirements: set[str]) -> bool:
        return requirements.issubset(self.provides)
