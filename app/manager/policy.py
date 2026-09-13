from __future__ import annotations

from collections.abc import Iterable

from app.capabilities import CapabilityNotFoundError, CapabilityRegistry
from app.manager.decisions import DelegateDecision, FailDecision, ManagerDecision


def route_requirements(
    registry: CapabilityRegistry,
    requirements: Iterable[str],
) -> ManagerDecision:
    normalized = tuple(dict.fromkeys(requirements))
    try:
        capability = registry.select(normalized)
    except CapabilityNotFoundError as error:
        return FailDecision(
            code="capability_gap",
            reason=str(error),
        )
    return DelegateDecision(
        capability_id=capability.id,
        adapter=capability.execution.adapter,
        required_capabilities=normalized,
        rationale=(
            f"Selected {capability.id} because it is the highest-priority enabled "
            f"capability providing {', '.join(normalized)}."
        ),
    )
