from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.capabilities.models import IDENTIFIER_PATTERN, CapabilityKind


class TaskAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=20_000)
    required_capabilities: tuple[str, ...] = Field(min_length=1, max_length=20)
    constraints: tuple[str, ...] = Field(default=(), max_length=20)
    risk_signals: tuple[str, ...] = Field(default=(), max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("required_capabilities")
    @classmethod
    def requirements_are_unique_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("required_capabilities must not contain duplicates")
        invalid = [value for value in values if not IDENTIFIER_PATTERN.fullmatch(value)]
        if invalid:
            raise ValueError(f"invalid required capability identifiers: {invalid}")
        return values


class CapabilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: CapabilityKind
    description: str
    provides: tuple[str, ...]
    enabled: bool


class ManagerModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    system_prompt: str
    user_prompt: str
    available_capabilities: tuple[CapabilitySummary, ...]
    response_schema: dict[str, Any]


class ModelInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_output: str | dict[str, Any]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    provider: str | None = None
    model: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis: TaskAnalysis
    provider: str
    model: str
    attempts: int = Field(ge=1)
    latency_ms: int = Field(ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)

    def event_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AnalysisFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    attempts: int = Field(ge=1)
    latency_ms: int = Field(ge=0)
    reason: str

    def event_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ManagerModelClient(Protocol):
    provider: str
    model: str

    def generate(
        self,
        request: ManagerModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelInvocation: ...
