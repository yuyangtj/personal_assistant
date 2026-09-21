from pathlib import Path

import pytest

from app.workflows import WorkflowNotFoundError, WorkflowRegistry, WorkflowStageKind


def test_built_in_workflows_define_explicit_approval_boundaries() -> None:
    registry = WorkflowRegistry.from_directory(Path("workflows"))

    assert [workflow.id for workflow in registry.list()] == [
        "assistant-deployment",
        "coding-change",
        "repository-onboarding",
    ]
    coding = registry.get("coding-change")
    assert coding.required_inputs == ("repository_id", "request")
    assert [stage.kind for stage in coding.stages] == [
        WorkflowStageKind.AGENT,
        WorkflowStageKind.VALIDATION,
        WorkflowStageKind.APPROVAL,
        WorkflowStageKind.TRUSTED_ACTION,
    ]


def test_unknown_workflow_is_rejected() -> None:
    registry = WorkflowRegistry.from_directory(Path("workflows"))

    with pytest.raises(WorkflowNotFoundError):
        registry.get("agent-do-anything")
