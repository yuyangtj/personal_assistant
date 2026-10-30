"""The claim loop: take the next queued task, pick its executor, run it, record the end."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from app.assistant.reporter import fallback_text as report_fallback
from app.assistant.reporter import links as report_links
from app.assistant.reporter import needs_push as report_push
from app.execution import Executor
from app.execution.fake import ExecutionCancelled
from app.execution.routing import DEFAULT_REQUIREMENTS, adapter_for
from app.notify import Notifier, NullNotifier
from app.persistence.models import ScheduleModel, TaskModel
from app.services import TaskService
from app.work.digest import current as current_digest
from app.work.memory import MemoryService
from app.worker.jobs import PeriodicJob
from app.worker_liveness import WorkerHeartbeat, container_instance_id
from app.workflows import WorkflowService

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Turns an agent's progress into task events without flooding them.

    Plans and milestones are always kept (when they change); steps and worktree
    summaries at most every ``interval`` seconds.
    """

    def __init__(
        self,
        service: TaskService,
        task_id: str,
        *,
        claim_id: str | None = None,
        interval: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.service = service
        self.task_id = task_id
        self.claim_id = claim_id
        self.interval = interval
        self.clock = clock
        self._last_step_at: float | None = None
        self._last: dict[str, str] = {}

    def __call__(self, text: str, kind: str) -> None:
        if self._last.get(kind) == text:
            return
        if kind in {"tool", "worktree"}:
            now = self.clock()
            if self._last_step_at is not None and now - self._last_step_at < self.interval:
                return
            self._last_step_at = now
        self._last[kind] = text
        self.service.record_progress(self.task_id, text, kind, claim_id=self.claim_id)


class GuidanceInbox:
    """New instructions the user sent a running coding task, for its agent to pick up."""

    def __init__(self, service: TaskService, task_id: str):
        self.service = service
        self.task_id = task_id
        # Coding tasks only get user messages from steering, so read from the start:
        # notes sent while the run was queued arrive before the agent begins.
        self._seen = 0

    def __call__(self) -> str | None:
        messages = self.service.user_messages_after(self.task_id, self._seen)
        if not messages:
            return None
        self._seen = messages[-1][0]
        return "\n".join(text for _, text in messages if text.strip()) or None


class _LeaseHeartbeat:
    def __init__(
        self,
        service: TaskService,
        *,
        task_id: str,
        worker_id: str,
        claim_id: str,
        lease_seconds: int,
    ):
        self.service = service
        self.task_id = task_id
        self.worker_id = worker_id
        self.claim_id = claim_id
        self.lease_seconds = lease_seconds
        self.stop_event = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not self.stop_event.wait(interval):
            try:
                if not self.service.renew_lease(
                    self.task_id,
                    worker_id=self.worker_id,
                    claim_id=self.claim_id,
                    lease_seconds=self.lease_seconds,
                ):
                    self.lost.set()
                    return
            except Exception:
                logger.exception("Could not renew lease for task %s", self.task_id)


class TaskWorker:
    def __init__(
        self,
        *,
        service: TaskService,
        executors: Mapping[str, Executor],
        worker_id: str,
        lease_seconds: int = 60,
        poll_interval_seconds: float = 1.0,
        workflow_service: WorkflowService | None = None,
        memory_service: MemoryService | None = None,
        supports_coding: bool = True,
        coding_only: bool = False,
        #: "replies": only chat replies (a fast lane nothing else can hold up);
        #: "background": everything else; "all": both, one at a time.
        lane: str = "all",
        #: Background jobs this worker runs alongside its claim loop (the general worker's).
        jobs: list[PeriodicJob] | None = None,
        notifier: Notifier | None = None,
    ):
        self.service = service
        self.executors = dict(executors)
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.workflow_service = workflow_service
        self.memory_service = memory_service
        self.supports_coding = supports_coding
        self.coding_only = coding_only
        self.lane = lane
        self.jobs = list(jobs or [])
        self.notifier: Notifier = notifier or NullNotifier()
        self._stop_event = threading.Event()

    def run_once(self) -> bool:
        recovered = self.service.recover_expired_tasks()
        if recovered:
            logger.warning("Recovered expired tasks: %s", ", ".join(recovered))
        task = self.service.claim_next_task(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            supports_coding=self.supports_coding,
            coding_only=self.coding_only,
            lane=self.lane,
        )
        if task is None:
            return False

        if task.claim_id is None:
            raise RuntimeError(f"Claimed task {task.id} has no claim ID")
        claim_id = task.claim_id
        heartbeat = _LeaseHeartbeat(
            self.service,
            task_id=task.id,
            worker_id=self.worker_id,
            claim_id=claim_id,
            lease_seconds=self.lease_seconds,
        )
        heartbeat.start()
        execution_id: str | None = None
        try:
            adapter = adapter_for(task.required_capabilities or (), self.executors)
            if adapter is None:
                self.service.fail_task(
                    task.id,
                    error="No installed executor provides: "
                    + ", ".join(task.required_capabilities or DEFAULT_REQUIREMENTS),
                    reply="Sorry, I can't do that yet. I don't have a tool for it.",
                    claim_id=claim_id,
                )
                return True
            executor = self.executors[adapter]
            execution_id = self.service.start_execution(
                task.id,
                capability_id=adapter,
                executor_id=executor.id,
                execution_input={"request": task.original_request},
                claim_id=claim_id,
            )
            if execution_id is None:
                return True

            routine = None if self.coding_only else self._routine(task)
            result = executor.execute(
                task_id=task.id,
                request=task.original_request,
                is_cancelled=lambda: (
                    heartbeat.lost.is_set() or self.service.is_cancelled(task.id, claim_id=claim_id)
                ),
                history=() if self.coding_only else self.service.conversation_history(task),
                context=self._context(task, executor.id, claim_id, routine),
            )
            if not self.service.start_validation(
                task.id,
                execution_id,
                output=result.output,
                claim_id=claim_id,
            ):
                return True
            self._sync_workflow(task.id)
            if not result.output or not result.output.get("summary"):
                raise ValueError("Executor produced no summary")
            approval = result.output.get("approval_request")
            if isinstance(approval, dict):
                artifacts = result.output.get("artifacts")
                self.service.request_approval(
                    task.id,
                    execution_id,
                    artifacts=(
                        [artifact for artifact in artifacts if isinstance(artifact, dict)]
                        if isinstance(artifacts, list)
                        else []
                    ),
                    approval=approval,
                    claim_id=claim_id,
                )
                return True  # the Assistant's report tells the user, with a push
            reply = result.output.get("reply") or result.output["summary"]
            report = (task.source_context or {}).get("report")
            blocks = result.output.get("blocks")
            if report:
                blocks = [*(blocks or []), *report_links(report)]
            completed = self.service.complete_task(
                task.id,
                execution_id,
                reply=str(reply),
                emotion=str(result.output.get("emotion", "Warm")),
                action=result.output.get("action"),
                blocks=blocks,
                # A watch that triggered: its report always pushes.
                notable=bool(routine is not None and result.output.get("notable")),
                claim_id=claim_id,
            )
            if report and completed is not None:
                self._push_report(task, report, str(reply), bool(result.output.get("notable")))
        except ExecutionCancelled:
            if execution_id is not None:
                self.service.finish_cancelled_execution(execution_id)
        except Exception as error:
            logger.exception("Task %s failed", task.id)
            user_message = getattr(error, "user_message", None)
            failed = self.service.fail_task(
                task.id,
                error=str(error),
                execution_id=execution_id,
                claim_id=claim_id,
                **({"reply": user_message} if user_message else {}),
            )
            report = (task.source_context or {}).get("report")
            if report and failed:
                # No model could write the report: say it plainly, and still push what must.
                text = report_fallback(report)
                self.service.append_chat_message(
                    task.chat_session_id,
                    content=text,
                    role="assistant",
                    blocks=report_links(report) or None,
                )
                self._push_report(task, report, text, False)
        finally:
            heartbeat.stop()
            self._sync_workflow(task.id)
        return True

    def _context(
        self,
        task: TaskModel,
        executor_id: str,
        claim_id: str,
        routine: ScheduleModel | None,
    ) -> dict[str, Any]:
        """What each executor is given besides the request, in one place."""
        context: dict[str, Any] = dict(task.source_context or {})
        chat = executor_id == "model-conversation"
        if routine is not None:
            # A routine compares with what its earlier runs found; a watch says whether this
            # result is worth a push.
            context["routine"] = {
                "watch": routine.quiet,
                "previous_runs": self.service.routine_results(task),
            }
        if executor_id == "coding-pull-request" or chat:
            # The agent's plan and steps, and a chat reply's tool calls ("Reading app/main.py").
            context["progress"] = ProgressReporter(self.service, task.id, claim_id=claim_id)
        if executor_id == "coding-pull-request":
            context["guidance"] = GuidanceInbox(self.service, task.id)
        if chat:
            # Its tools act in this chat: a reminder, a list entry, a coding run.
            context["chat_session_id"] = task.chat_session_id
            context["origin_message_id"] = task.origin_message_id
        if chat or executor_id == "tool-agent":
            work_items = self.service.work_item_context(task)
            if work_items:
                context["work_items"] = work_items
        if chat and self.memory_service is not None:
            # Saved memories (never the private ones), all of them, so the model judges
            # relevance; and the summary of recent days.
            context["memories"] = self.memory_service.for_model()
            recent = current_digest(self.service.database)
            if recent is not None and recent.content:
                context["recent"] = recent.content
        # The one "Assistant" conversation: the reply is told it speaks for everything.
        if (
            not self.coding_only
            and task.chat_session_id
            and self.service.is_home_chat(task.chat_session_id)
        ):
            context["home"] = True
        return context

    def _push_report(self, task, report: list, text: str, notable: bool) -> None:
        """A report goes to the phone only for the must-knows or when the agent says so."""
        priority = report_push(report, notable=notable)
        if priority is not None:
            self.notifier.send(
                "Assistant",
                text[:300],
                priority=priority,
                path=f"/ui#/chats/{task.chat_session_id}",
            )

    def _routine(self, task) -> ScheduleModel | None:
        schedule_id = (task.source_context or {}).get("schedule_id")
        if not schedule_id:
            return None
        with self.service.database.session() as session:
            return session.get(ScheduleModel, schedule_id)

    def _sync_workflow(self, task_id: str) -> None:
        if self.workflow_service is None:
            return
        try:
            self.workflow_service.sync_for_task(task_id)
        except Exception:
            logger.exception("Could not synchronize workflow for task %s", task_id)

    def run_forever(self) -> None:
        heartbeat = WorkerHeartbeat(
            self.service.database,
            worker_id=self.worker_id,
            instance_id=container_instance_id(),
            lane=self.lane,
        )
        heartbeat.start()
        try:
            logger.info("Worker %s started", self.worker_id)
            for job in self.jobs:
                job.start(self._stop_event)
            while not self._stop_event.is_set():
                processed = self.run_once()
                if not processed:
                    self._stop_event.wait(self.poll_interval_seconds)
        finally:
            heartbeat.stop()
            logger.info("Worker %s stopped", self.worker_id)

    def stop(self) -> None:
        self._stop_event.set()
