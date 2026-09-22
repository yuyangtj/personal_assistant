from pathlib import Path

import pytest

from app.execution.coding import CodingAgentError, CodingPullRequestExecutor
from app.validation import ValidationProfile, ValidationProfileRegistry, ValidationStep


def test_validation_registry_loads_repository_profiles() -> None:
    registry = ValidationProfileRegistry.from_directory("validation-profiles")
    assert registry.get("personal-assistant").steps[0].command[:2] == ("uv", "run")


def test_required_validation_failure_stops_execution(tmp_path: Path) -> None:
    executor = object.__new__(CodingPullRequestExecutor)
    executor.validation_profile = ValidationProfile(
        id="test", name="Test", steps=(ValidationStep(id="fail", command=("false",)),)
    )
    with pytest.raises(CodingAgentError, match="Required validation step failed"):
        executor._validate(tmp_path, lambda: False)


def test_optional_validation_failure_is_reported(tmp_path: Path) -> None:
    executor = object.__new__(CodingPullRequestExecutor)
    executor.validation_profile = ValidationProfile(
        id="test",
        name="Test",
        steps=(ValidationStep(id="optional", command=("false",), required=False),),
    )
    assert executor._validate(tmp_path, lambda: False)[0]["passed"] is False
