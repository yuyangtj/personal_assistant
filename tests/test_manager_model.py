from __future__ import annotations

import json

import httpx
import pytest

from app.capabilities import CapabilityRegistry
from app.integrations.kimi import KimiError, KimiManagerModelClient
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


def test_kimi_manager_client_sends_contract_and_reads_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "kimi-manager-test",
                "choices": [
                    {"message": {"content": f"```json\n{json.dumps(valid_analysis())}\n```"}}
                ],
                "usage": {"prompt_tokens": 31, "completion_tokens": 17},
            },
        )

    registry = CapabilityRegistry.from_directory("capabilities")
    client = KimiManagerModelClient(
        api_key="sk-test",
        model="kimi-manager-test",
        transport=httpx.MockTransport(handler),
    )

    result = ValidatedManagerModelAdapter(client).analyze(task(), registry.list())

    assert result.analysis.required_capabilities == ("task_execution",)
    assert result.provider == "kimi"
    assert result.model == "kimi-manager-test"
    assert result.usage.input_tokens == 31
    assert result.usage.output_tokens == 17
    assert seen["path"] == "/coding/v1/chat/completions"
    assert seen["authorization"] == "Bearer sk-test"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "kimi-manager-test"
    assert body["messages"][0]["role"] == "system"
    assert "RESPONSE_JSON_SCHEMA" in body["messages"][1]["content"]
    assert "required_capabilities" in body["messages"][1]["content"]
    client.close()


def test_kimi_manager_errors_do_not_leak_credentials_or_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key sk-manager-secret"})

    client = KimiManagerModelClient(
        api_key="sk-manager-secret",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(KimiError) as captured:
        client.generate(
            ValidatedManagerModelAdapter._build_request(task(), tuple()),
            timeout_seconds=5,
        )

    assert str(captured.value) == "Kimi manager returned HTTP 401"
    assert "sk-manager-secret" not in str(captured.value)
    client.close()


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
    assert "ability names" in client.requests[0].system_prompt
    assert "fake-executor" in client.requests[0].user_prompt
    assert "not manifest IDs" in client.requests[0].user_prompt
    assert client.requests[0].response_schema["properties"]["required_capabilities"]["items"][
        "enum"
    ] == [
        "coding",
        "conversation",
        "pull_request_creation",
        "repository_analysis",
        "shell_execution",
        "task_execution",
        "testing",
    ]


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
