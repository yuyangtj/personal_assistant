from datetime import UTC, datetime

from app.decision import CodingDecisionEngine, CodingRunnerRegistry, TaskComplexity
from app.execution.coding import CodeAgentReport, FallbackCodeAgentRunner
from app.providers import InMemoryProviderStateStore


class Runner:
    def __init__(self, provider: str):
        self.provider = provider


def _runners() -> list[Runner]:
    return [Runner("kimi-code"), Runner("minimax-claude-code"), Runner("codex-cli")]


def test_decision_engine_prefers_lower_cost_runner_for_simple_work() -> None:
    engine = CodingDecisionEngine(
        CodingRunnerRegistry.from_directory("coding-runners"),
        InMemoryProviderStateStore(),
    )

    decision = engine.decide(_runners(), "Update the README documentation")

    assert decision.profile.complexity == TaskComplexity.SIMPLE
    assert decision.ordered_providers[0] == "kimi-code"


def test_decision_engine_prefers_matching_runner_for_data_work() -> None:
    engine = CodingDecisionEngine(
        CodingRunnerRegistry.from_directory("coding-runners"),
        InMemoryProviderStateStore(),
    )

    decision = engine.decide(_runners(), "Add an analytics metric and SQL query")

    assert decision.profile.complexity == TaskComplexity.STANDARD
    assert "data" in decision.profile.traits
    assert decision.ordered_providers[0] == "minimax-claude-code"


def test_decision_engine_prefers_reasoning_for_complex_security_architecture() -> None:
    engine = CodingDecisionEngine(
        CodingRunnerRegistry.from_directory("coding-runners"),
        InMemoryProviderStateStore(),
    )

    decision = engine.decide(
        _runners(),
        "Design the security architecture and permission migration",
    )

    assert decision.profile.complexity == TaskComplexity.COMPLEX
    assert decision.ordered_providers[0] == "codex-cli"
    assert decision.candidates[0].reasons[-1].startswith("complex_reasoning_fit")


def test_decision_engine_filters_cooling_provider_before_scoring() -> None:
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    store = InMemoryProviderStateStore(clock=lambda: now)
    store.record_failure(
        "coding:codex-cli",
        category="quota_exhausted",
        cooldown_seconds=3600,
    )
    engine = CodingDecisionEngine(
        CodingRunnerRegistry.from_directory("coding-runners"),
        store,
    )

    decision = engine.decide(_runners(), "Design a complex security architecture")

    assert decision.ordered_providers[-1] == "codex-cli"
    codex = next(item for item in decision.candidates if item.provider == "codex-cli")
    assert codex.available is False
    assert "unavailable:cooldown" in codex.reasons


def test_fallback_runner_uses_decision_order_and_records_explanation(tmp_path) -> None:
    calls: list[str] = []

    class SuccessfulRunner:
        def __init__(self, provider: str):
            self.provider = provider

        def run(self, **_kwargs) -> CodeAgentReport:
            calls.append(self.provider)
            return CodeAgentReport(summary="done")

    runners = [SuccessfulRunner(runner.provider) for runner in _runners()]
    store = InMemoryProviderStateStore()
    engine = CodingDecisionEngine(
        CodingRunnerRegistry.from_directory("coding-runners"),
        store,
    )
    fallback = FallbackCodeAgentRunner(
        runners,
        state_store=store,
        decision_engine=engine,
    )

    report = fallback.run(
        worktree=tmp_path,
        request="Update the README documentation",
        timeout_seconds=60,
        is_cancelled=lambda: False,
    )

    assert calls == ["kimi-code"]
    assert fallback.last_decision is not None
    assert fallback.last_decision.ordered_providers[0] == "kimi-code"
    assert report.notes[-1].startswith("Decision engine: complexity=simple")
