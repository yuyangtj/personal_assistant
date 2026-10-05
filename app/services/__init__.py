"""The task service: chat, tasks, executions, approvals and progress, over one database.

Each concern lives in its own module; TaskService combines them, so callers see one
service with the same methods as before.
"""

from app.services.approvals import ApprovalOperations
from app.services.chat import ChatOperations
from app.services.common import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ChatMessageNotFoundError,
    ChatSessionNotFoundError,
    ExecutionNotFoundError,
    MessageHasWorkflowError,
    PostedMessage,
    TaskNotFoundError,
)
from app.services.executions import ExecutionOperations
from app.services.progress import ProgressOperations
from app.services.tasks import TaskOperations


class TaskService(
    ChatOperations, TaskOperations, ExecutionOperations, ApprovalOperations, ProgressOperations
):
    """All task operations over one database."""


__all__ = [
    "ApprovalConflictError",
    "ApprovalNotFoundError",
    "ChatMessageNotFoundError",
    "ChatSessionNotFoundError",
    "ExecutionNotFoundError",
    "MessageHasWorkflowError",
    "PostedMessage",
    "TaskNotFoundError",
    "TaskService",
]
