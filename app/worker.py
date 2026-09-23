from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path

from app.capabilities import CapabilityRegistry
from app.coding_runs import CodingRunStore
from app.config import Settings
from app.decision import CodingDecisionEngine, CodingRunnerRegistry
from app.deployments import DeploymentRegistry
from app.execution import ConversationExecutor, Executor, FakeExecutor
from app.execution.coding import (
    ClaudeCodeMiniMaxRunner,
    CodeAgentRunner,
    CodexCliRunner,
    CodingPullRequestExecutor,
    FallbackCodeAgentRunner,
    KimiCodeCliRunner,
    RepositoryCodingExecutor,
)
from app.execution.fake import ExecutionCancelled
from app.integrations.chat import ChatClient
from app.integrations.fallback import FallbackChatClient, FallbackManagerModelClient
from app.integrations.github import GitHubClient
from app.integrations.kimi import KimiChatClient, KimiManagerModelClient
from app.integrations.minimax import MiniMaxChatClient, MiniMaxManagerModelClient
from app.manager import DeterministicManager, ModelAssistedManager, TaskManager
from app.manager.decisions import DelegateDecision, FailDecision
from app.manager.model import ManagerModelClient, ValidatedManagerModelAdapter
from app.memory import MemoryService
from app.persistence.database import Database
from app.providers import DatabaseProviderStateStore, ProviderStateStore
from app.repositories import RepositoryRegistry
from app.service import TaskService
from app.validation import ValidationProfileRegistry
from app.workflows import WorkflowRegistry, WorkflowService

logger = logging.getLogger(__name__)


class ExecutorAdapterNotFoundError(LookupError):
    pass


class _LeaseHeartbeat:
    def __init__(
        self,
        service: TaskService,
        *,
        task_id: str,
        worker_id: str,
        lease_seconds: int,
    ):
        self.service = service
        self.task_id = task_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.stop_event = threading.Event()
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
                    lease_seconds=self.lease_seconds,
                ):
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
        decision_engine=(
            CodingDecisionEngine(
                CodingRunnerRegistry.from_directory(settings.coding_runners_directory),
                provider_state_store,
            )
            if provider_state_store is not None
            else None
        ),
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
        )
    if not executors:
        expected = ", ".join(manifest.path_env for manifest in registry.list())
        github.close()
        raise ValueError("Coding agent requires a configured repository path: " + expected)
    configured_default = settings.default_repository_id
    if configured_default is None:
        default = registry.default()
        configured_default = default.id if default and default.id in executors else None
    return RepositoryCodingExecutor(executors, default_repository_id=configured_default)


def _build_code_agent_runners(settings: Settings) -> list[CodeAgentRunner]:
    providers = tuple(
        dict.fromkeys(
            provider.strip().lower()
            for provider in settings.code_agent_providers.split(",")
            if provider.strip()
        )
    )
    supported = {"kimi", "minimax-claude", "codex"}
    unknown = [provider for provider in providers if provider not in supported]
    if unknown:
        raise ValueError("Unsupported coding provider: " + ", ".join(unknown))
    runners: list[CodeAgentRunner] = []
    for provider in providers:
        if provider == "kimi":
            runners.append(
                KimiCodeCliRunner(
                    executable=settings.kimi_code_executable,
                    model=settings.kimi_code_model,
                    api_key=settings.kimi_api_key,
                )
            )
        elif provider == "minimax-claude":
            if settings.minimax_api_key:
                runners.append(
                    ClaudeCodeMiniMaxRunner(
                        api_key=settings.minimax_api_key,
                        base_url=settings.minimax_anthropic_base_url,
                        executable=settings.claude_code_executable,
                        model=settings.minimax_code_model,
                    )
                )
            else:
                logger.warning("Skipping MiniMax Claude Code runner: no MiniMax API key")
        else:
            runners.append(
                CodexCliRunner(
                    executable=settings.code_agent_executable,
                    model=settings.code_agent_model,
                )
            )
    if not runners:
        raise ValueError("No configured coding runner is usable")
    logger.info("Configured coding runners: %s", ", ".join(runner.provider for runner in runners))
    return runners


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
        if isinstance(runner, KimiCodeCliRunner) and not runner.api_key:
            problems.append("Kimi coding runner requires KIMI_API_KEY")
    if problems:
        raise ValueError("Coding worker preflight failed: " + "; ".join(problems))


def _provider_order(primary: str, fallback: str | None) -> tuple[str, ...]:
    normalized_primary = primary.strip().lower()
    supported = {"kimi", "minimax"}
    if normalized_primary not in supported:
        raise ValueError(f"Unsupported model provider: {normalized_primary}")
    if fallback is None or fallback.strip().lower() in {"", "none", "off", "disabled"}:
        return (normalized_primary,)
    normalized_fallback = fallback.strip().lower()
    if normalized_fallback == "auto":
        normalized_fallback = "minimax" if normalized_primary == "kimi" else "kimi"
    if normalized_fallback not in supported:
        raise ValueError(f"Unsupported fallback model provider: {normalized_fallback}")
    return tuple(dict.fromkeys((normalized_primary, normalized_fallback)))


def _build_chat_client(
    settings: Settings,
    provider: str,
    *,
    primary: bool,
) -> ChatClient | None:
    base_url_override = settings.conversation_model_base_url if primary else None
    model_override = settings.conversation_model_name if primary else None
    if provider == "kimi":
        if not settings.kimi_api_key:
            return None
        return KimiChatClient(
            api_key=settings.kimi_api_key,
            base_url=base_url_override or settings.kimi_base_url,
            model=model_override or settings.kimi_model,
            timeout_seconds=(
                settings.conversation_model_timeout_seconds or settings.kimi_timeout_seconds
            ),
        )
    if not settings.minimax_api_key:
        return None
    return MiniMaxChatClient(
        api_key=settings.minimax_api_key,
        base_url=base_url_override or settings.minimax_base_url,
        model=model_override or settings.minimax_model,
        timeout_seconds=(
            settings.conversation_model_timeout_seconds or settings.minimax_timeout_seconds
        ),
    )


def build_conversation_executor(settings: Settings) -> ConversationExecutor | None:
    """Builds the preferred conversation provider with an optional fallback."""
    order = _provider_order(
        settings.conversation_model_provider,
        settings.conversation_model_fallback_provider,
    )
    clients = [
        client
        for index, provider in enumerate(order)
        if (client := _build_chat_client(settings, provider, primary=index == 0)) is not None
    ]
    if not clients:
        return None
    client: ChatClient = clients[0] if len(clients) == 1 else FallbackChatClient(clients)
    logger.info("Conversation provider order: %s", client.provider)
    return ConversationExecutor(client)


def _build_manager_client(
    settings: Settings,
    provider: str,
    *,
    primary: bool,
) -> ManagerModelClient | None:
    base_url_override = settings.manager_model_base_url if primary else None
    model_override = settings.manager_model_name if primary else None
    if provider == "kimi":
        if not settings.kimi_api_key:
            return None
        return KimiManagerModelClient(
            api_key=settings.kimi_api_key,
            base_url=base_url_override or settings.kimi_base_url,
            model=model_override or settings.kimi_model,
        )
    if not settings.minimax_api_key:
        return None
    return MiniMaxManagerModelClient(
        api_key=settings.minimax_api_key,
        base_url=base_url_override or settings.minimax_base_url,
        model=model_override or settings.minimax_model,
    )


def build_manager(settings: Settings, registry: CapabilityRegistry) -> TaskManager:
    """Builds the opt-in model-assisted manager or the deterministic default."""
    if not settings.manager_model_enabled:
        return DeterministicManager(registry)
    order = _provider_order(
        settings.manager_model_provider,
        settings.manager_model_fallback_provider,
    )
    clients = [
        client
        for index, provider in enumerate(order)
        if (client := _build_manager_client(settings, provider, primary=index == 0)) is not None
    ]
    if not clients:
        raise ValueError(
            "Manager analysis requires credentials for a configured provider: " + ", ".join(order)
        )
    client: ManagerModelClient = (
        clients[0] if len(clients) == 1 else FallbackManagerModelClient(clients)
    )
    logger.info("Manager provider order: %s", client.provider)
    return ModelAssistedManager(
        registry,
        ValidatedManagerModelAdapter(
            client,
            timeout_seconds=settings.manager_model_timeout_seconds,
        ),
        minimum_confidence=settings.manager_model_minimum_confidence,
    )


class TaskWorker:
    def __init__(
        self,
        *,
        service: TaskService,
        manager: TaskManager,
        executors: Mapping[str, Executor],
        worker_id: str,
        lease_seconds: int = 60,
        poll_interval_seconds: float = 1.0,
        workflow_service: WorkflowService | None = None,
        memory_service: MemoryService | None = None,
        supports_coding: bool = True,
        coding_only: bool = False,
    ):
        self.service = service
        self.manager = manager
        self.executors = dict(executors)
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.workflow_service = workflow_service
        self.memory_service = memory_service
        self.supports_coding = supports_coding
        self.coding_only = coding_only
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
        )
        if task is None:
            return False

        heartbeat = _LeaseHeartbeat(
            self.service,
            task_id=task.id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        heartbeat.start()
        execution_id: str | None = None
        try:
            outcome = self.manager.decide(task)
            decision = outcome.decision
            self.service.record_plan(
                task.id,
                [
                    "Resolve the task's required capabilities",
                    "Select an enabled registered capability",
                    "Execute through its configured adapter",
                    "Validate that a structured result was produced",
                ],
            )
            if outcome.analysis is not None:
                self.service.record_task_analysis(
                    task.id,
                    outcome.analysis.event_payload(),
                )
            if outcome.analysis_failure is not None:
                self.service.record_task_analysis_failure(
                    task.id,
                    outcome.analysis_failure.event_payload(),
                )
            self.service.record_manager_decision(
                task.id,
                decision.model_dump(mode="json"),
            )
            if isinstance(decision, FailDecision):
                self.service.fail_task(
                    task.id,
                    error=decision.reason,
                    reply="Sorry, I can't do that yet. I don't have a tool for it.",
                )
                return True
            if not isinstance(decision, DelegateDecision):
                raise TypeError(f"Unsupported manager decision: {decision.action}")
            executor = self.executors.get(decision.adapter)
            if executor is None:
                raise ExecutorAdapterNotFoundError(
                    f"No executor is installed for adapter: {decision.adapter}"
                )
            execution_id = self.service.start_execution(
                task.id,
                capability_id=decision.capability_id,
                executor_id=executor.id,
                execution_input={"request": task.original_request},
            )
            if execution_id is None:
                return True

            execution_context = dict(task.source_context or {})
            if self.memory_service is not None and executor.id == "model-conversation":
                execution_context["memories"] = [
                    memory.content for memory in self.memory_service.relevant(task.original_request)
                ]
            result = executor.execute(
                task_id=task.id,
                request=task.original_request,
                is_cancelled=lambda: self.service.is_cancelled(task.id),
                history=self.service.conversation_history(task),
                context=execution_context,
            )
            if not self.service.start_validation(
                task.id,
                execution_id,
                output=result.output,
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
                )
                return True
            reply = result.output.get("reply") or result.output["summary"]
            self.service.complete_task(
                task.id,
                execution_id,
                reply=str(reply),
                emotion=str(result.output.get("emotion", "Warm")),
                action=result.output.get("action"),
            )
        except ExecutionCancelled:
            if execution_id is not None:
                self.service.finish_cancelled_execution(execution_id)
        except Exception as error:
            logger.exception("Task %s failed", task.id)
            self.service.fail_task(
                task.id,
                error=str(error),
                execution_id=execution_id,
            )
        finally:
            heartbeat.stop()
            self._sync_workflow(task.id)
        return True

    def _sync_workflow(self, task_id: str) -> None:
        if self.workflow_service is None:
            return
        try:
            self.workflow_service.sync_for_task(task_id)
        except Exception:
            logger.exception("Could not synchronize workflow for task %s", task_id)

    def run_forever(self) -> None:
        logger.info("Worker %s started", self.worker_id)
        while not self._stop_event.is_set():
            processed = self.run_once()
            if not processed:
                self._stop_event.wait(self.poll_interval_seconds)
        logger.info("Worker %s stopped", self.worker_id)

    def stop(self) -> None:
        self._stop_event.set()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    database = Database(settings.database_url)
    if settings.auto_create_schema:
        database.create_schema()
    executors: dict[str, Executor] = {
        "fake": FakeExecutor(delay_seconds=settings.fake_executor_delay_seconds),
    }
    conversation_executor = build_conversation_executor(settings)
    if conversation_executor is not None:
        executors[conversation_executor.id] = conversation_executor
    else:
        logger.warning(
            "%s conversation credentials are not set; conversation falls back to fake",
            settings.conversation_model_provider,
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
    # Only capabilities whose adapter is installed in this worker can be selected.
    registry = CapabilityRegistry.from_directory(
        settings.capabilities_directory
    ).restricted_to_adapters(executors)
    manager = build_manager(settings, registry)
    workflow_service = WorkflowService(
        database,
        WorkflowRegistry.from_directory(settings.workflows_directory),
        RepositoryRegistry.from_directory(settings.repositories_directory),
        DeploymentRegistry.from_directory(settings.deployment_targets_directory),
    )
    worker = TaskWorker(
        service=TaskService(database),
        manager=manager,
        executors=executors,
        worker_id=settings.worker_id,
        lease_seconds=settings.worker_lease_seconds,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        workflow_service=workflow_service,
        memory_service=MemoryService(database),
        supports_coding=coding_executor is not None,
        coding_only=settings.worker_coding_only,
    )

    def stop_worker(_signum, _frame) -> None:
        worker.stop()

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    try:
        worker.run_forever()
    finally:
        if conversation_executor is not None:
            conversation_executor.client.close()
        if isinstance(manager, ModelAssistedManager):
            manager.analyzer.client.close()
        if coding_executor is not None:
            coding_executor.github.close()
        database.dispose()


if __name__ == "__main__":
    main()
