from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.manager.model.contracts import AnalysisFailure, AnalysisResult


class DecisionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DelegateDecision(DecisionBase):
    action: Literal["delegate"] = "delegate"
    capability_id: str
    adapter: str
    required_capabilities: tuple[str, ...]
    rationale: str


class CallToolDecision(DecisionBase):
    action: Literal["call_tool"] = "call_tool"
    capability_id: str
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AskUserDecision(DecisionBase):
    action: Literal["ask_user"] = "ask_user"
    question: str


class ValidateDecision(DecisionBase):
    action: Literal["validate"] = "validate"
    execution_id: str
    validator_id: str


class CompleteDecision(DecisionBase):
    action: Literal["complete"] = "complete"
    summary: str


class FailDecision(DecisionBase):
    action: Literal["fail"] = "fail"
    code: str = "manager_failure"
    reason: str


ManagerDecision = Annotated[
    DelegateDecision
    | CallToolDecision
    | AskUserDecision
    | ValidateDecision
    | CompleteDecision
    | FailDecision,
    Field(discriminator="action"),
]


class ManagerOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: ManagerDecision
    analysis: AnalysisResult | None = None
    analysis_failure: AnalysisFailure | None = None
