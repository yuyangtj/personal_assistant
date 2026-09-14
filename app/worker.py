from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Mapping

from app.capabilities import CapabilityRegistry
from app.config import Settings
from app.execution import ConversationExecutor, Executor, FakeExecutor
from app.execution.fake import ExecutionCancelled
from app.integrations.chat import ChatClient
from app.integrations.fallback import FallbackChatClient, FallbackManagerModelClient
from app.integrations.kimi import KimiChatClient, KimiManagerModelClient
from app.integrations.minimax import MiniMaxChatClient, MiniMaxManagerModelClient
from app.manager import DeterministicManager, ModelAssistedManager, TaskManager
from app.manager.decisions import DelegateDecision, FailDecision
from app.manager.model import ManagerModelClient, ValidatedManagerModelAdapter
from app.persistence.database import Database
from app.service import TaskService

logger = logging.getLogger(__name__)


class ExecutorAdapterNotFoundError(LookupError):
    pass


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
            "Manager analysis requires credentials for a configured provider: "
            + ", ".join(order)
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
    ):
        self.service = service
        self.manager = manager
        self.executors = dict(executors)
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()

    def run_once(self) -> bool:
        task = self.service.claim_next_task(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if task is None:
            return False

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

            result = executor.execute(
                task_id=task.id,
                request=task.original_request,
                is_cancelled=lambda: self.service.is_cancelled(task.id),
                history=self.service.conversation_history(task),
                context=task.source_context or {},
            )
            if not self.service.start_validation(
                task.id,
                execution_id,
                output=result.output,
            ):
                return True
            if not result.output or not result.output.get("summary"):
                raise ValueError("Executor produced no summary")
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
        return True

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
    # Only capabilities whose adapter is installed in this worker can be selected.
    registry = CapabilityRegistry.from_directory(
        settings.capabilities_directory
    ).restricted_to_adapters(executors)
    manager = build_manager(settings, registry)
    worker = TaskWorker(
        service=TaskService(database),
        manager=manager,
        executors=executors,
        worker_id=settings.worker_id,
        lease_seconds=settings.worker_lease_seconds,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
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
        database.dispose()


if __name__ == "__main__":
    main()
