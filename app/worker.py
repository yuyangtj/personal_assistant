from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from time import monotonic

from app.abilities import AbilityRegistry, abilities_prompt
from app.assistant.reporter import Reporter
from app.assistant.reporter import fallback_text as report_fallback
from app.assistant.reporter import links as report_links
from app.assistant.reporter import needs_push as report_push
from app.assistant.supervisor import SupervisorTools
from app.assistant.toolbox import Toolbox
from app.assistant.tools import (
    bookkeeping_tools,
    coding_tools,
    job_tools,
    overview_tools,
    repository_tools,
    store_tools,
)
from app.chat.routines import start_routine as run_routine
from app.chat.titles import TitleJob, TitleWriter
from app.coding.base import CodeAgentRunner
from app.coding.executor import CodingPullRequestExecutor, RepositoryCodingExecutor
from app.coding.reconcile import PullRequestReconciler
from app.coding.review import DiffReviewer
from app.coding.runners import (
    DEFAULT_KIMI_CODE_MODEL,
    ClaudeCodeRunner,
    CodexCliRunner,
    FallbackCodeAgentRunner,
    KimiAcpRunner,
    KimiCodeCliRunner,
)
from app.coding.runs import CodingRunStore
from app.coding.spec import SpecWriter
from app.config import Settings
from app.delivery import DeliveryService
from app.deployments import DeploymentRegistry, HostDeployerSpool
from app.execution import ConversationExecutor, Executor, FakeExecutor
from app.execution.fake import ExecutionCancelled
from app.execution.routing import DEFAULT_REQUIREMENTS, adapter_for
from app.execution.tool_agent import ToolAgentExecutor
from app.integrations.factory import chat_client_chain
from app.integrations.github import GitHubClient
from app.notify import Notifier, NullNotifier, build_notifier
from app.persistence.database import Database
from app.persistence.models import ScheduleModel
from app.providers import DatabaseProviderStateStore, ProviderStateStore
from app.repositories import RepositoryRegistry
from app.repositories.briefs import RepositoryBriefs
from app.repositories.reader import RepositoryReader
from app.schedules import Scheduler, ScheduleService
from app.services import TaskService
from app.tools.klarna import KlarnaTools, ToolGateway
from app.tools.mcp import McpGateway, Toolset
from app.tools.search import build_search
from app.validation import ValidationProfileRegistry
from app.work.briefs import BriefWritebackJob, BriefWriter
from app.work.digest import DigestJob, DigestWriter
from app.work.digest import current as current_digest
from app.work.items import WorkItemService, configure_spaces
from app.work.memory import MemoryService
from app.work.notes import NoteService
from app.work.purchases import PurchaseService
from app.work.spaces import SpaceRegistry
from app.worker_liveness import WorkerHeartbeat, container_instance_id
from app.workflows import WorkflowRegistry, WorkflowService

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


def build_coding_executor(
    settings: Settings,
    *,
    provider_state_store: ProviderStateStore | None = None,
) -> RepositoryCodingExecutor | None:
    """Builds the opt-in isolated coding-to-draft-PR workflow."""
    if not settings.code_agent_enabled:
        return None
    if not settings.github_token:
        raise ValueError("Coding agent requires: ASSISTANT_GITHUB_TOKEN")
    github = GitHubClient(
        token=settings.github_token,
        base_url=settings.github_api_base_url,
    )
    runners = _build_code_agent_runners(settings)
    agent = FallbackCodeAgentRunner(
        runners,
        rate_limit_cooldown_seconds=settings.code_agent_rate_limit_cooldown_seconds,
        quota_cooldown_seconds=settings.code_agent_quota_cooldown_seconds,
        state_store=provider_state_store,
    )
    registry = RepositoryRegistry.from_directory(settings.repositories_directory)
    checkpoint_store = (
        CodingRunStore(provider_state_store.database)
        if isinstance(provider_state_store, DatabaseProviderStateStore)
        else None
    )
    validation_registry = ValidationProfileRegistry.from_directory(
        settings.validation_profiles_directory
    )
    planner = (
        chat_client_chain(settings, timeout_seconds=settings.coding_planner_timeout_seconds)
        if settings.coding_spec_enabled or settings.coding_review_enabled
        else None
    )
    spec_writer = SpecWriter(planner) if planner and settings.coding_spec_enabled else None
    reviewer = DiffReviewer(planner) if planner and settings.coding_review_enabled else None
    executors: dict[str, CodingPullRequestExecutor] = {}
    for manifest in registry.list():
        configured_path = os.getenv(manifest.path_env)
        if manifest.id == "personal-assistant" and settings.code_repository_path is not None:
            repository_path = settings.code_repository_path
        elif configured_path:
            repository_path = Path(configured_path).expanduser()
        else:
            continue
        github_repository = (
            settings.github_repository
            if manifest.id == "personal-assistant" and settings.github_repository
            else manifest.github_repository
        )
        base_branch = (
            settings.github_base_branch
            if manifest.id == "personal-assistant" and settings.code_repository_path is not None
            else manifest.base_branch
        )
        remote = (
            settings.github_remote
            if manifest.id == "personal-assistant" and settings.code_repository_path is not None
            else manifest.remote
        )
        executors[manifest.id] = CodingPullRequestExecutor(
            repository_path=repository_path,
            worktree_root=settings.code_worktree_root / manifest.id,
            github_repository=github_repository,
            github=github,
            agent=agent,
            base_branch=base_branch,
            remote=remote,
            timeout_seconds=settings.code_agent_timeout_seconds,
            draft_pull_requests=settings.github_draft_pull_requests,
            validation_profile=validation_registry.get(manifest.validation_profile),
            repository_id=manifest.id,
            checkpoint_store=checkpoint_store,
            spec_writer=spec_writer,
            reviewer=reviewer,
        )
    if not executors:
        expected = ", ".join(manifest.path_env for manifest in registry.list())
        github.close()
        if planner is not None:
            planner.close()
        raise ValueError("Coding agent requires a configured repository path: " + expected)
    configured_default = settings.default_repository_id
    if configured_default is None:
        default = registry.default()
        configured_default = default.id if default and default.id in executors else None
    return RepositoryCodingExecutor(executors, default_repository_id=configured_default)


def codex_logged_in(codex_home: Path | None) -> bool:
    """Whether Codex has a login to use: auth.json in its home (default ~/.codex)."""
    home = codex_home or Path.home() / ".codex"
    return (home / "auth.json").is_file()


def _build_code_agent_runners(settings: Settings) -> list[CodeAgentRunner]:
    providers = tuple(
        dict.fromkeys(
            provider.strip().lower()
            for provider in settings.code_agent_providers.split(",")
            if provider.strip()
        )
    )
    supported = {"claude", "codex", "kimi", "minimax-claude"}
    unknown = [provider for provider in providers if provider not in supported]
    if unknown:
        raise ValueError("Unsupported coding provider: " + ", ".join(unknown))
    runners: list[CodeAgentRunner] = []
    for provider in providers:
        if provider == "kimi":
            if settings.kimi_api_key:
                kimi_runner = (
                    KimiCodeCliRunner if settings.kimi_code_protocol == "prompt" else KimiAcpRunner
                )
                runners.append(
                    kimi_runner(
                        api_key=settings.kimi_api_key,
                        base_url=settings.kimi_base_url,
                        executable=settings.kimi_code_executable,
                        model=settings.kimi_code_model or DEFAULT_KIMI_CODE_MODEL,
                    )
                )
            else:
                logger.warning("Skipping Kimi Code runner: no Kimi API key")
        elif provider == "claude":
            if settings.claude_code_oauth_token:
                runners.append(
                    ClaudeCodeRunner.subscription(
                        oauth_token=settings.claude_code_oauth_token,
                        executable=settings.claude_code_executable,
                    )
                )
            else:
                logger.warning("Skipping Claude Code: no CLAUDE_CODE_OAUTH_TOKEN")
        elif provider == "minimax-claude":
            if settings.minimax_api_key:
                runners.append(
                    ClaudeCodeRunner.minimax(
                        api_key=settings.minimax_api_key,
                        base_url=settings.minimax_anthropic_base_url,
                        executable=settings.claude_code_executable,
                        model=settings.minimax_code_model,
                    )
                )
            else:
                logger.warning("Skipping MiniMax Claude Code runner: no MiniMax API key")
        elif codex_logged_in(settings.codex_home):
            runners.append(
                CodexCliRunner(
                    executable=settings.code_agent_executable,
                    model=settings.code_agent_model,
                    codex_home=settings.codex_home,
                    own_sandbox=settings.codex_own_sandbox,
                )
            )
        else:
            logger.warning("Skipping Codex: no ChatGPT login in CODEX_HOME (auth.json)")
    if not runners:
        raise ValueError("No configured coding runner is usable")
    logger.info("Configured coding runners: %s", ", ".join(runner.provider for runner in runners))
    return runners


def drop_logged_out_runners(executor: RepositoryCodingExecutor) -> None:
    """Leave out a subscription agent whose login doesn't work, rather than fail its runs.

    The others still serve; the log says why, so the login can be renewed.
    """
    agents = {id(c.agent): c.agent for c in executor.executors.values()}
    for agent in agents.values():
        runners = getattr(agent, "runners", None)
        if not runners:
            continue
        kept = []
        for runner in runners:
            check = getattr(runner, "check_login", None)
            problem = check() if callable(check) else None
            if problem:
                logger.warning("Leaving out %s: %s", runner.provider, problem)
            else:
                kept.append(runner)
        if not kept:
            raise ValueError("Coding worker preflight failed: no coding agent can log in")
        agent.runners = tuple(kept)
        agent.provider = "->".join(runner.provider for runner in kept)
        logger.info("Coding agents: %s", ", ".join(runner.provider for runner in kept))


def preflight_coding_executor(executor: RepositoryCodingExecutor) -> None:
    problems: list[str] = []
    checked_executables: set[str] = set()
    for repository_id, configured in executor.executors.items():
        path = configured.repository_path
        if not (path / ".git").exists():
            problems.append(f"{repository_id}: checkout is not a Git repository")
            continue
        for arguments, label in (
            (("status", "--porcelain"), "status"),
            (("remote", "get-url", configured.remote), "remote"),
            (
                (
                    "push",
                    "--dry-run",
                    configured.remote,
                    "HEAD:refs/heads/assistant/preflight-check",
                ),
                "push access",
            ),
        ):
            completed = subprocess.run(
                ["git", "-C", str(path), *arguments],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                problems.append(f"{repository_id}: Git {label} check failed")
            elif label == "status" and completed.stdout.strip():
                problems.append(f"{repository_id}: checkout has uncommitted changes")
            elif label == "remote":
                expected = configured.github_repository.removesuffix(".git")
                actual = completed.stdout.strip().removesuffix(".git")
                if not actual.endswith(expected):
                    problems.append(f"{repository_id}: remote does not match {expected}")
        for step in configured.validation_profile.steps if configured.validation_profile else ():
            executable = step.command[0]
            if executable not in checked_executables and shutil.which(executable) is None:
                problems.append(f"{repository_id}: validation executable not found: {executable}")
            checked_executables.add(executable)
    first_executor = next(iter(executor.executors.values()))
    for runner in getattr(first_executor.agent, "runners", (first_executor.agent,)):
        executable = getattr(runner, "executable", None)
        if isinstance(executable, str) and shutil.which(executable) is None:
            problems.append(f"coding runner executable not found: {executable}")
    if not problems:
        drop_logged_out_runners(executor)
    if problems:
        raise ValueError("Coding worker preflight failed: " + "; ".join(problems))


def build_conversation_executor(settings: Settings) -> ConversationExecutor | None:
    """Builds the preferred conversation provider with an optional fallback."""
    client = chat_client_chain(settings)
    if client is None:
        return None
    logger.info("Conversation provider order: %s", client.provider)
    return ConversationExecutor(client)


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
        deployment_sync_interval_seconds: float = 10.0,
        brief_writeback: BriefWritebackJob | None = None,
        brief_writeback_interval_seconds: float = 30.0,
        digest: DigestJob | None = None,
        titles: TitleJob | None = None,
        scheduler: Scheduler | None = None,
        reporter: Reporter | None = None,
        delivery: DeliveryService | None = None,
        scheduler_interval_seconds: float = 20.0,
        pull_requests: PullRequestReconciler | None = None,
        pull_request_interval_seconds: float = 300.0,
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
        self.deployment_sync_interval_seconds = deployment_sync_interval_seconds
        self._last_deployment_sync: float | None = None
        self.brief_writeback = brief_writeback
        self.brief_writeback_interval_seconds = brief_writeback_interval_seconds
        self._brief_writeback_thread: threading.Thread | None = None
        self.digest = digest
        self._digest_thread: threading.Thread | None = None
        self.titles = titles
        self._titles_thread: threading.Thread | None = None
        self.scheduler = scheduler
        self.reporter = reporter
        self.delivery = delivery
        self.scheduler_interval_seconds = scheduler_interval_seconds
        self._scheduler_thread: threading.Thread | None = None
        self.pull_requests = pull_requests
        self.pull_request_interval_seconds = pull_request_interval_seconds
        self._pull_request_thread: threading.Thread | None = None
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
            self._sync_deployments()
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

            execution_context = dict(task.source_context or {})
            routine = None if self.coding_only else self._routine(task)
            if routine is not None:
                # A routine compares with what its earlier runs found; a watch says whether
                # this result is worth a push.
                execution_context["routine"] = {
                    "watch": routine.quiet,
                    "previous_runs": self.service.routine_results(task),
                }
            if executor.id == "coding-pull-request":
                execution_context["progress"] = ProgressReporter(
                    self.service, task.id, claim_id=claim_id
                )
                execution_context["guidance"] = GuidanceInbox(self.service, task.id)
            if executor.id == "model-conversation":
                # Its tool calls show on the reply's card ("Reading app/main.py").
                execution_context["progress"] = ProgressReporter(
                    self.service, task.id, claim_id=claim_id
                )
            if executor.id == "model-conversation":
                # Their tools act in this chat: a reminder, a list entry, a coding run.
                execution_context["chat_session_id"] = task.chat_session_id
                execution_context["origin_message_id"] = task.origin_message_id
            if executor.id in {"model-conversation", "tool-agent"}:
                work_items = self.service.work_item_context(task)
                if work_items:
                    execution_context["work_items"] = work_items
            # Saved memories (never the private ones), all of them, so the model judges
            # relevance.
            if self.memory_service is not None and executor.id == "model-conversation":
                execution_context["memories"] = self.memory_service.for_model()
                recent = current_digest(self.service.database)
                if recent is not None and recent.content:
                    execution_context["recent"] = recent.content
            # The one "Assistant" conversation: the reply is told it speaks for everything.
            if (
                not self.coding_only
                and task.chat_session_id
                and self.service.is_home_chat(task.chat_session_id)
            ):
                execution_context["home"] = True
            result = executor.execute(
                task_id=task.id,
                request=task.original_request,
                is_cancelled=lambda: (
                    heartbeat.lost.is_set() or self.service.is_cancelled(task.id, claim_id=claim_id)
                ),
                history=() if self.coding_only else self.service.conversation_history(task),
                context=execution_context,
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

    def _sync_deployments(self) -> None:
        """Reconcile running deployments while idle; the coding worker leaves this alone."""
        if self.workflow_service is None or self.coding_only or self.lane == "replies":
            return
        now = monotonic()
        if (
            self._last_deployment_sync is not None
            and now - self._last_deployment_sync < self.deployment_sync_interval_seconds
        ):
            return
        self._last_deployment_sync = now
        try:
            finished = self.workflow_service.sync_running_deployments()
        except Exception:
            logger.exception("Could not synchronize running deployments")
            return
        if finished:
            logger.info("Deployments finished: %s", ", ".join(finished))
            for run_id in finished:
                run = self.workflow_service.get_run(run_id)
                self.notifier.send(
                    f"Deployment {run.status}",
                    f"{str(run.workflow_input.get('commit_sha', ''))[:7]} at stage "
                    f"{run.current_stage or 'deploy'}",
                    priority=3 if run.status == "completed" else 5,
                )

    def start_brief_writeback(self) -> threading.Thread | None:
        """Refresh briefs on a background thread so model calls never delay chat replies."""
        if self.brief_writeback is None or self.coding_only:
            return None
        if self._brief_writeback_thread is None or not self._brief_writeback_thread.is_alive():
            self._brief_writeback_thread = threading.Thread(
                target=self._brief_writeback_loop, name="brief-writeback", daemon=True
            )
            self._brief_writeback_thread.start()
        return self._brief_writeback_thread

    def start_scheduler(self) -> threading.Thread | None:
        """Run due reminders and routines on a background thread (general worker only)."""
        if self.scheduler is None or self.coding_only:
            return None
        if self._scheduler_thread is None or not self._scheduler_thread.is_alive():
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop, name="scheduler", daemon=True
            )
            self._scheduler_thread.start()
        return self._scheduler_thread

    def start_pull_request_sync(self) -> threading.Thread | None:
        """Notice PRs merged or closed on GitHub (general worker only)."""
        if self.pull_requests is None or self.coding_only:
            return None
        if self._pull_request_thread is None or not self._pull_request_thread.is_alive():
            self._pull_request_thread = threading.Thread(
                target=self._pull_request_loop, name="pull-requests", daemon=True
            )
            self._pull_request_thread.start()
        return self._pull_request_thread

    def _pull_request_loop(self) -> None:
        reconciler = self.pull_requests
        if reconciler is None:
            return
        while True:
            try:
                reconciler.run_once()
            except Exception:
                logger.exception("Could not sync pull requests with GitHub")
            if self._stop_event.wait(self.pull_request_interval_seconds):
                return

    def _scheduler_loop(self) -> None:
        scheduler = self.scheduler
        if scheduler is None:
            return
        while not self._stop_event.wait(self.scheduler_interval_seconds):
            try:
                fired = scheduler.run_due()
            except Exception:
                logger.exception("Could not run due schedules")
                fired = 0
            if fired:
                logger.info("Queued %d due schedules", fired)
            if self.reporter is not None:
                # Finished background work, told in the Assistant conversation.
                try:
                    report = self.reporter.run_due()
                except Exception:
                    logger.exception("Could not queue finished-work report")
                else:
                    if report:
                        logger.info("Queued report delivery %s", report)
            if self.delivery is not None:
                try:
                    delivered = self.delivery.run_due()
                except Exception:
                    logger.exception("Could not drain delivery outbox")
                else:
                    if delivered:
                        logger.info("Delivered %d outbox records", delivered)

    def start_digest(self) -> threading.Thread | None:
        """Keep the summary of recent days current, on its own thread (general worker only)."""
        if self.digest is None or self.coding_only:
            return None
        if self._digest_thread is None or not self._digest_thread.is_alive():
            self._digest_thread = threading.Thread(
                target=self._digest_loop, name="recent-days", daemon=True
            )
            self._digest_thread.start()
        return self._digest_thread

    def _digest_loop(self) -> None:
        job = self.digest
        if job is None:
            return
        while not self._stop_event.wait(60):
            try:
                if job.run_due():
                    logger.info("Updated the summary of recent days")
            except Exception:
                logger.exception("Could not update the summary of recent days")

    def start_titles(self) -> threading.Thread | None:
        """Give chats named after a long first message a short title (general worker only)."""
        if self.titles is None or self.coding_only:
            return None
        if self._titles_thread is None or not self._titles_thread.is_alive():
            self._titles_thread = threading.Thread(
                target=self._titles_loop, name="chat-titles", daemon=True
            )
            self._titles_thread.start()
        return self._titles_thread

    def _titles_loop(self) -> None:
        job = self.titles
        if job is None:
            return
        while not self._stop_event.wait(10):
            try:
                job.run_due()
            except Exception:
                logger.exception("Could not write short chat titles")

    def _brief_writeback_loop(self) -> None:
        job = self.brief_writeback
        if job is None:
            return
        while not self._stop_event.wait(self.brief_writeback_interval_seconds):
            try:
                written = job.run_due()
            except Exception:
                logger.exception("Could not write back work item briefs")
                continue
            if written:
                logger.info("Updated %d work item briefs", written)

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
            self.start_brief_writeback()
            self.start_digest()
            self.start_titles()
            self.start_scheduler()
            self.start_pull_request_sync()
            while not self._stop_event.is_set():
                processed = self.run_once()
                if not processed:
                    self._stop_event.wait(self.poll_interval_seconds)
        finally:
            heartbeat.stop()
            logger.info("Worker %s stopped", self.worker_id)

    def stop(self) -> None:
        self._stop_event.set()


def shopping_tools(settings: Settings, database: Database) -> tuple[ToolGateway, Toolset]:
    """Klarna purchases from the database, and the stores through the MCP gateway if set."""
    stores = (
        McpGateway(settings.mcp_gateway_url, settings.mcp_gateway_token)
        if settings.mcp_gateway_url
        else None
    )
    return (
        ToolGateway(KlarnaTools(PurchaseService(database)), stores),
        Toolset.load(settings.toolsets_directory / "groceries.yaml"),
    )


def build_toolbox(
    settings: Settings,
    database: Database,
    task_service: TaskService,
    workflow_service: WorkflowService,
    repositories: RepositoryRegistry,
    github: GitHubClient | None,
    *,
    abilities: set[str],
) -> Toolbox:
    """The chat agent's tools, limited to the abilities this server has."""
    tools = bookkeeping_tools(
        items=WorkItemService(database),
        memory=MemoryService(database),
        schedules=ScheduleService(database),
        notes=NoteService(database),
        tasks=task_service,
    )
    if github is not None:
        tools += repository_tools(RepositoryReader(repositories, github))
    # The shopping tools themselves (Klarna, the stores); not on coding-only workers.
    shopping = (
        [] if settings.worker_coding_only else store_tools(*shopping_tools(settings, database))
    )
    tools += shopping
    tools += job_tools(task_service, research=settings.web_search_available)
    if not settings.worker_coding_only:
        supervisor = SupervisorTools(database, task_service, workflow_service, repositories)
        tools += coding_tools(supervisor)
        tools += overview_tools(database, supervisor)
    return Toolbox(tools, abilities=abilities)


def add_chat_executors(
    executors: dict[str, Executor],
    settings: Settings,
    database: Database,
    task_service: TaskService,
    workflow_service: WorkflowService,
    repositories: RepositoryRegistry,
) -> ConversationExecutor | None:
    """Add the agents that answer chat messages; returns the conversation agent, if any.

    Shared by the worker and by scripts/chat_eval, so the eval runs what production runs.
    """
    conversation_executor = build_conversation_executor(settings)
    if conversation_executor is not None:
        conversation_executor.research_available = settings.web_search_available
        abilities = AbilityRegistry.from_directory(settings.abilities_directory).available(settings)
        conversation_executor.abilities_text = abilities_prompt(abilities)
        conversation_executor.default_timezone = settings.default_timezone
        github = (
            GitHubClient(token=settings.github_token, base_url=settings.github_api_base_url)
            if settings.github_token
            else None
        )
        conversation_executor.repository_briefs = RepositoryBriefs(repositories, github)
        conversation_executor.toolbox = build_toolbox(
            settings,
            database,
            task_service,
            workflow_service,
            repositories,
            github,
            abilities={ability.id for ability in abilities},
        )
        logger.info("Chat agent tools: %s", ", ".join(conversation_executor.toolbox.names))
    if conversation_executor is not None:
        executors[conversation_executor.id] = conversation_executor
    else:
        logger.warning(
            "%s conversation credentials are not set; conversation falls back to fake",
            settings.conversation_model_provider,
        )
    search = build_search(settings)
    if search is not None and conversation_executor is not None:
        executors[ToolAgentExecutor.id] = ToolAgentExecutor(conversation_executor.client, search)
        logger.info("Web research agent enabled with %s search", search.name)
    return conversation_executor


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    configure_spaces(SpaceRegistry.from_directory(settings.spaces_directory))
    database = Database(settings.database_url)
    if settings.auto_create_schema:
        database.create_schema()
    repositories = RepositoryRegistry.from_directory(settings.repositories_directory)
    workflow_service = WorkflowService(
        database,
        WorkflowRegistry.from_directory(settings.workflows_directory),
        repositories,
        DeploymentRegistry.from_directory(settings.deployment_targets_directory),
        host_deployer=(
            HostDeployerSpool(settings.deploy_spool_directory)
            if settings.deploy_spool_directory
            else None
        ),
    )
    task_service = TaskService(database, coding_restricted=settings.worker_coding_only)
    executors: dict[str, Executor] = (
        {}
        if settings.worker_coding_only
        else {"fake": FakeExecutor(delay_seconds=settings.fake_executor_delay_seconds)}
    )
    conversation_executor = None
    if not settings.worker_coding_only:
        conversation_executor = add_chat_executors(
            executors, settings, database, task_service, workflow_service, repositories
        )
    provider_state_store = DatabaseProviderStateStore(database)
    coding_executor = build_coding_executor(
        settings,
        provider_state_store=provider_state_store,
    )
    if coding_executor is not None:
        if settings.code_agent_preflight_enabled:
            preflight_coding_executor(coding_executor)
        executors[coding_executor.id] = coding_executor
        logger.info(
            "Coding pull-request agent enabled for repositories: %s",
            ", ".join(sorted(coding_executor.executors)),
        )
    notifier = build_notifier(
        settings.ntfy_topic_url, token=settings.ntfy_token, public_url=settings.public_url
    )

    def start_routine(schedule, external_key: str) -> str | None:
        return run_routine(
            task_service,
            schedule,
            external_source="schedule",
            external_key=external_key,
        )

    delivery = None
    if not settings.worker_coding_only:
        delivery = DeliveryService(
            database,
            task_service,
            start_run=start_routine,
            notifier=notifier,
        )

    if not settings.worker_coding_only:
        logger.info(
            "Pull request sync with GitHub: %s",
            "on" if settings.github_token else "off (no ASSISTANT_GITHUB_TOKEN)",
        )
    worker = TaskWorker(
        service=task_service,
        executors=executors,
        worker_id=settings.worker_id,
        lease_seconds=settings.worker_lease_seconds,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        workflow_service=workflow_service,
        memory_service=None if settings.worker_coding_only else MemoryService(database),
        supports_coding=coding_executor is not None,
        coding_only=settings.worker_coding_only,
        brief_writeback=(
            BriefWritebackJob(
                database,
                BriefWriter(conversation_executor.client),
                idle_seconds=settings.brief_writeback_idle_seconds,
            )
            if settings.brief_writeback_enabled and conversation_executor is not None
            else None
        ),
        digest=(
            DigestJob(database, DigestWriter(conversation_executor.client))
            if conversation_executor is not None
            else None
        ),
        titles=(
            TitleJob(database, TitleWriter(conversation_executor.client))
            if conversation_executor is not None
            else None
        ),
        scheduler=None if settings.worker_coding_only else Scheduler(database),
        reporter=None if settings.worker_coding_only else Reporter(task_service, database),
        delivery=delivery,
        scheduler_interval_seconds=settings.scheduler_interval_seconds,
        pull_requests=(
            PullRequestReconciler(
                task_service,
                GitHubClient(token=settings.github_token, base_url=settings.github_api_base_url),
                on_change=workflow_service.sync_for_task,
            )
            if settings.github_token and not settings.worker_coding_only
            else None
        ),
        notifier=notifier,
    )

    extra_workers: list[TaskWorker] = []
    # Chat replies get their own claim loop, so research or routines
    # running in the main loop never keep someone waiting for an answer.
    if not settings.worker_coding_only and settings.worker_reply_lane:
        worker.lane = "background"
        extra_workers.append(
            TaskWorker(
                service=task_service,
                executors=executors,
                worker_id=f"{settings.worker_id}-replies",
                lease_seconds=settings.worker_lease_seconds,
                poll_interval_seconds=settings.worker_reply_poll_interval_seconds,
                workflow_service=workflow_service,
                memory_service=MemoryService(database),
                supports_coding=False,
                lane="replies",
                notifier=notifier,
            )
        )
        logger.info("Chat replies have their own lane")

    # More coding agents at once: extra claim loops, each with its own coding executor
    # (the runner chain keeps per-run state) and its own worker id for leases.
    if settings.worker_coding_only and coding_executor is not None:
        for index in range(2, settings.coding_concurrency + 1):
            extra_executor = build_coding_executor(
                settings, provider_state_store=provider_state_store
            )
            if extra_executor is None:
                break
            extra_workers.append(
                TaskWorker(
                    service=task_service,
                    executors={**executors, extra_executor.id: extra_executor},
                    worker_id=f"{settings.worker_id}-{index}",
                    lease_seconds=settings.worker_lease_seconds,
                    poll_interval_seconds=settings.worker_poll_interval_seconds,
                    workflow_service=workflow_service,
                    supports_coding=True,
                    coding_only=True,
                    notifier=notifier,
                )
            )
    extra_threads = [
        threading.Thread(target=extra.run_forever, name=extra.worker_id, daemon=True)
        for extra in extra_workers
    ]
    for thread in extra_threads:
        thread.start()

    def stop_worker(_signum, _frame) -> None:
        worker.stop()
        for extra in extra_workers:
            extra.stop()

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    try:
        worker.run_forever()
        for thread in extra_threads:
            thread.join(timeout=60)
    finally:
        if conversation_executor is not None:
            conversation_executor.client.close()
        if coding_executor is not None:
            coding_executor.github.close()
        database.dispose()


if __name__ == "__main__":
    main()
