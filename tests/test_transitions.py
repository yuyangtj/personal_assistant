import pytest

from app.domain.enums import TaskStatus
from app.domain.transitions import InvalidTaskTransition, ensure_transition


def test_terminal_state_cannot_transition() -> None:
    with pytest.raises(InvalidTaskTransition):
        ensure_transition(TaskStatus.COMPLETED, TaskStatus.EXECUTING)


def test_expected_transition_is_allowed() -> None:
    ensure_transition(TaskStatus.EXECUTING, TaskStatus.VALIDATING)
