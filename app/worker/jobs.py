"""The general worker's background jobs: each on its own thread, so a slow model call in
one (the summary of recent days, say) never delays chat replies or the others."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from app.notify import Notifier
from app.workflows import WorkflowService

logger = logging.getLogger(__name__)


@dataclass
class PeriodicJob:
    """``run`` every ``interval_seconds`` until the worker stops.

    A failure is logged and the job carries on. When ``run`` returns something truthy and
    ``done`` is set, ``done % result`` is logged ("Queued %s due schedules").
    """

    name: str
    interval_seconds: float
    run: Callable[[], object]
    done: str | None = None
    #: Run once at start instead of waiting a first interval (the PR sync).
    run_at_start: bool = False

    def start(self, stop: threading.Event) -> threading.Thread:
        thread = threading.Thread(target=self._loop, args=(stop,), name=self.name, daemon=True)
        thread.start()
        return thread

    def run_once(self) -> object:
        try:
            result = self.run()
        except Exception:
            logger.exception("Background job %s failed", self.name)
            return None
        if result and self.done:
            logger.info(self.done, result)
        return result

    def _loop(self, stop: threading.Event) -> None:
        if self.run_at_start:
            self.run_once()
        while not stop.wait(self.interval_seconds):
            self.run_once()


def deployment_sync(workflows: WorkflowService, notifier: Notifier) -> Callable[[], list[str]]:
    """Reconcile running deployments, pushing how each finished one ended."""

    def run() -> list[str]:
        finished = workflows.sync_running_deployments()
        for run_id in finished:
            run = workflows.get_run(run_id)
            notifier.send(
                f"Deployment {run.status}",
                f"{str(run.workflow_input.get('commit_sha', ''))[:7]} at stage "
                f"{run.current_stage or 'deploy'}",
                priority=3 if run.status == "completed" else 5,
            )
        return finished

    return run
