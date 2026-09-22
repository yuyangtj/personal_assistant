from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.capabilities.models import IDENTIFIER_PATTERN


class WorkflowStageKind(StrEnum):
    AGENT = "agent"
    VALIDATION = "validation"
    APPROVAL = "approval"
    TRUSTED_ACTION = "trusted_action"


class WorkflowRunStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class WorkflowStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str = Field(min_length=1, max_length=120)
    kind: WorkflowStageKind
    description: str = Field(default="", max_length=500)

    @field_validator("id")
    @classmethod
    def id_is_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("workflow stage id must be a lowercase identifier")
        return value


class WorkflowManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    required_inputs: tuple[str, ...] = ()
    stages: tuple[WorkflowStage, ...] = Field(min_length=1)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def id_is_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("workflow id must be a lowercase identifier")
        return value

    @field_validator("required_inputs")
    @classmethod
    def inputs_are_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("required workflow inputs must be unique")
        if any(not IDENTIFIER_PATTERN.fullmatch(value) for value in values):
            raise ValueError("required workflow inputs must be lowercase identifiers")
        return values

    @field_validator("stages")
    @classmethod
    def stages_are_unique(cls, values: tuple[WorkflowStage, ...]) -> tuple[WorkflowStage, ...]:
        ids = [stage.id for stage in values]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow stage ids must be unique")
        if not any(stage.kind == WorkflowStageKind.APPROVAL for stage in values):
            raise ValueError("a controlled workflow must include an approval stage")
        return values
