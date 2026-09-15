from app.domain.enums import TaskStatus


class InvalidTaskTransition(ValueError):
    pass


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}

ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.PLANNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.PLANNING: {
        TaskStatus.WAITING,
        TaskStatus.EXECUTING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING: {
        TaskStatus.EXECUTING,
        TaskStatus.WAITING_FOR_APPROVAL,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.EXECUTING: {
        TaskStatus.WAITING,
        TaskStatus.VALIDATING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.VALIDATING: {
        TaskStatus.WAITING_FOR_APPROVAL,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_FOR_APPROVAL: {
        TaskStatus.EXECUTING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


def ensure_transition(current: TaskStatus | str, target: TaskStatus) -> None:
    current_status = TaskStatus(current)
    if target not in ALLOWED_TRANSITIONS[current_status]:
        raise InvalidTaskTransition(f"Cannot move task from {current_status} to {target}")
