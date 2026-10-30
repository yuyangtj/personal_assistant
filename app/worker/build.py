"""Builds the worker from settings: its executors, the chat agent's tools, its jobs."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from app.abilities import AbilityRegistry, abilities_prompt
from app.assistant.reporter import Reporter
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
from app.coding.decision import CodingDecisionEngine, CodingRunnerRegistry
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
from app.execution.tool_agent import ToolAgentExecutor
from app.integrations.chat import ChatClient
from app.integrations.factory import chat_client_chain
from app.integrations.github import GitHubClient
from app.notify import Notifier, build_notifier
from app.persistence.database import Database
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
from app.work.items import WorkItemService, configure_spaces
from app.work.memory import MemoryService
from app.work.notes import NoteService
from app.work.purchases import PurchaseService
from app.work.spaces import SpaceRegistry
from app.worker.jobs import PeriodicJob, deployment_sync
from app.worker.loop import TaskWorker
from app.workflows import WorkflowRegistry, WorkflowService

logger = logging.getLogger(__name__)


#: How often the outbox is drained; cheap (one indexed query), so reminders go out promptly.
DELIVERY_INTERVAL_SECONDS = 5.0
#: How often running deployments are reconciled.
DEPLOYMENT_SYNC_SECONDS = 10.0
#: How often PRs merged or closed on GitHub are noticed.
PULL_REQUEST_SYNC_SECONDS = 300.0


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


def general_jobs(
    settings: Settings,
    database: Database,
    task_service: TaskService,
    workflow_service: WorkflowService,
    notifier: Notifier,
    model: ChatClient | None,
) -> list[PeriodicJob]:
    """The general worker's background jobs; the coding worker runs none."""

    def start_routine(schedule, external_key: str) -> str | None:
        return run_routine(
            task_service, schedule, external_source="schedule", external_key=external_key
        )

    every = settings.scheduler_interval_seconds
    jobs = [
        PeriodicJob("schedules", every, Scheduler(database).run_due, "Queued %s due schedules"),
        # Finished background work, told in the Assistant conversation.
        PeriodicJob("reports", every, Reporter(task_service, database).run_due, "Queued report %s"),
        # Reminders, alarms, routine starts and reports, from the outbox.
        PeriodicJob(
            "deliveries",
            DELIVERY_INTERVAL_SECONDS,
            DeliveryService(
                database, task_service, start_run=start_routine, notifier=notifier
            ).run_due,
            "Delivered %s outbox records",
        ),
        PeriodicJob(
            "deployments",
            DEPLOYMENT_SYNC_SECONDS,
            deployment_sync(workflow_service, notifier),
            "Deployments finished: %s",
        ),
    ]
    if settings.github_token:
        jobs.append(
            PeriodicJob(
                "pull-requests",
                PULL_REQUEST_SYNC_SECONDS,
                PullRequestReconciler(
                    task_service,
                    GitHubClient(
                        token=settings.github_token, base_url=settings.github_api_base_url
                    ),
                    on_change=workflow_service.sync_for_task,
                ).run_once,
                run_at_start=True,
            )
        )
    if model is not None:
        # Model calls, each on its own thread so they never delay a chat reply.
        jobs.append(
            PeriodicJob(
                "recent-days",
                60,
                DigestJob(database, DigestWriter(model)).run_due,
                "Updated the summary of recent days (%s)",
            )
        )
        jobs.append(PeriodicJob("chat-titles", 10, TitleJob(database, TitleWriter(model)).run_due))
        if settings.brief_writeback_enabled:
            jobs.append(
                PeriodicJob(
                    "brief-writeback",
                    30,
                    BriefWritebackJob(
                        database,
                        BriefWriter(model),
                        idle_seconds=settings.brief_writeback_idle_seconds,
                    ).run_due,
                    "Updated %s work item briefs",
                )
            )
    return jobs


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
        jobs=(
            []
            if settings.worker_coding_only
            else general_jobs(
                settings,
                database,
                task_service,
                workflow_service,
                notifier,
                conversation_executor.client if conversation_executor is not None else None,
            )
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
