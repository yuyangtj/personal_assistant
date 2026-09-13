from __future__ import annotations

from app.capabilities import CapabilityRegistry
from app.manager.decisions import FailDecision, ManagerOutcome
from app.manager.deterministic import DeterministicManager
from app.manager.model import ManagerAnalysisError, ValidatedManagerModelAdapter
from app.manager.policy import route_requirements
from app.persistence.models import TaskModel


class ModelAssistedManager:
    """Uses a model only to infer requirements; deterministic policy owns routing."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        analyzer: ValidatedManagerModelAdapter,
        *,
        minimum_confidence: float = 0.5,
    ):
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        self.registry = registry
        self.analyzer = analyzer
        self.minimum_confidence = minimum_confidence
        self.deterministic_manager = DeterministicManager(registry)

    def decide(self, task: TaskModel) -> ManagerOutcome:
        if task.required_capabilities:
            return self.deterministic_manager.decide(task)

        try:
            result = self.analyzer.analyze(task, self.registry.list())
        except ManagerAnalysisError as error:
            return ManagerOutcome(
                decision=FailDecision(
                    code="task_analysis_failed",
                    reason=str(error),
                ),
                analysis_failure=error.failure,
            )

        if result.analysis.confidence < self.minimum_confidence:
            return ManagerOutcome(
                decision=FailDecision(
                    code="analysis_confidence_too_low",
                    reason=(
                        f"Task analysis confidence {result.analysis.confidence:.2f} is below "
                        f"the required {self.minimum_confidence:.2f}."
                    ),
                ),
                analysis=result,
            )

        return ManagerOutcome(
            decision=route_requirements(
                self.registry,
                result.analysis.required_capabilities,
            ),
            analysis=result,
        )
