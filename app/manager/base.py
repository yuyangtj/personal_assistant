from __future__ import annotations

from typing import Protocol

from app.manager.decisions import ManagerOutcome
from app.persistence.models import TaskModel


class TaskManager(Protocol):
    def decide(self, task: TaskModel) -> ManagerOutcome: ...
