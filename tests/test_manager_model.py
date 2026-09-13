from __future__ import annotations

import pytest

from app.capabilities import CapabilityRegistry
from app.manager import ModelAssistedManager
from app.manager.decisions import DelegateDecision
from app.manager.model import (
    ManagerAnalysisError,
    ModelInvocation,
    ScriptedManagerModelClient,
    ValidatedManagerModelAdapter,
)
from app.persistence.models import TaskModel


def task(*, requirements: list[str] | None = None) -> TaskModel:
    return TaskModel(
        id="task-1",
        original_request="Research the available options",
        current_goal="Research the available options",
        status="planning",
        required_capabilities=requirements or [],
    )


def valid_analysis() -> dict:
    return {
        "goal": "Research the available options",
        "required_capabilities": ["task_execution"],
        "constraints": ["Return a concise result"],
        "risk_signals": [],
        "confidence": 0.94,
    }


def test_validated_adapter_returns_analysis_and_usage() -> None:
    registry = CapabilityRegistry.from_directory("capabilities")
    client = ScriptedManagerModelClient(
        [ModelInvocation(raw_output=valid_analysis(), input_tokens=20, output_tokens=10)]
    )
    adapter = ValidatedManagerModelAdapter(client)

    result = adapter.analyze(task(), registry.list())

    assert result.analysis.required_capabilities == ("task_execution",)
    assert result.provider == "scripted"
    assert result.model == "scripted-manager"
    assert result.attempts == 1
    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 10
    assert "untrusted data" in client.requests[0].system_prompt
    assert "fake-executor" in client.requests[0].user_prompt


def test_invalid_json_is_retried_once() -> None:
    registry = CapabilityRegistry.from_directory("capabilities")
    client = ScriptedManagerModelClient(["not json", valid_analysis()])
    adapter = ValidatedManagerModelAdapter(client, max_attempts=2)

    result = adapter.analyze(task(), registry.list())

    assert result.attempts == 2
    assert client.calls == 2
    assert "previous response failed validation" in client.requests[1].user_prompt


def test_unknown_capability_is_rejected_without_echoing_raw_output() -> None:
    registry = CapabilityRegistry.from_directory("capabilities")
    invented = {
        **valid_analysis(),
        "required_capabilities": ["run-any-command"],
    }
    client = ScriptedManagerModelClient([invented, invented])
    adapter = ValidatedManagerModelAdapter(client, max_attempts=2)

    with pytest.raises(ManagerAnalysisError) as captured:
        adapter.analyze(task(), registry.list())

    assert captured.value.failure.attempts == 2
    assert captured.value.failure.reason == (
        "response invented unknown capabilities: ['run-any-command']"
    )
    assert client.calls == 2


def test_explicit_requirements_bypass_model_inference() -> None:
    registry = CapabilityRegistry.from_directory("capabilities")
    client = ScriptedManagerModelClient([])
    manager = ModelAssistedManager(
        registry,
        ValidatedManagerModelAdapter(client),
    )

    outcome = manager.decide(task(requirements=["task_execution"]))

    assert isinstance(outcome.decision, DelegateDecision)
    assert outcome.analysis is None
    assert client.calls == 0


def test_low_confidence_analysis_is_not_executed() -> None:
    registry = CapabilityRegistry.from_directory("capabilities")
    response = {**valid_analysis(), "confidence": 0.2}
    manager = ModelAssistedManager(
        registry,
        ValidatedManagerModelAdapter(ScriptedManagerModelClient([response])),
        minimum_confidence=0.5,
    )

    outcome = manager.decide(task())

    assert outcome.decision.action == "fail"
    assert outcome.decision.code == "analysis_confidence_too_low"
    assert outcome.analysis is not None


def test_repeated_invalid_responses_become_safe_failure_decision() -> None:
    registry = CapabilityRegistry.from_directory("capabilities")
    manager = ModelAssistedManager(
        registry,
        ValidatedManagerModelAdapter(
            ScriptedManagerModelClient(["bad", "still bad"]),
            max_attempts=2,
        ),
    )

    outcome = manager.decide(task())

    assert outcome.decision.action == "fail"
    assert outcome.decision.code == "task_analysis_failed"
    assert outcome.analysis_failure is not None
    assert outcome.analysis_failure.reason == "response was not valid JSON"
