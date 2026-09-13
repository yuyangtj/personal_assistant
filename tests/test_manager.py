import pytest
from pydantic import TypeAdapter, ValidationError

from app.capabilities import CapabilityManifest, CapabilityRegistry
from app.manager.decisions import DelegateDecision, ManagerDecision
from app.manager.deterministic import DeterministicManager
from app.persistence.models import TaskModel


def test_manager_returns_typed_delegate_decision() -> None:
    capability = CapabilityManifest.model_validate(
        {
            "id": "researcher",
            "name": "Researcher",
            "kind": "agent",
            "description": "Researches topics",
            "provides": ["research"],
            "execution": {"adapter": "research-agent"},
        }
    )
    manager = DeterministicManager(CapabilityRegistry([capability]))
    task = TaskModel(
        id="task-1",
        original_request="Research databases",
        current_goal="Research databases",
        status="planning",
        required_capabilities=["research"],
    )

    outcome = manager.decide(task)

    assert isinstance(outcome.decision, DelegateDecision)
    assert outcome.decision.capability_id == "researcher"
    assert outcome.decision.adapter == "research-agent"


def test_manager_decision_union_rejects_unknown_actions() -> None:
    adapter = TypeAdapter(ManagerDecision)
    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "invent_workflow"})
