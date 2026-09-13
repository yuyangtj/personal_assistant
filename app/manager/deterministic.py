from __future__ import annotations

from app.capabilities.registry import CapabilityRegistry
from app.manager.decisions import ManagerOutcome
from app.manager.policy import route_requirements
from app.persistence.models import TaskModel

DEFAULT_REQUIREMENTS = ("task_execution",)


class DeterministicManager:
    """Selects the highest-priority enabled capability satisfying task requirements."""

    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    def decide(self, task: TaskModel) -> ManagerOutcome:
        requirements = tuple(task.required_capabilities) or DEFAULT_REQUIREMENTS
        return ManagerOutcome(
            decision=route_requirements(self.registry, requirements),
        )
