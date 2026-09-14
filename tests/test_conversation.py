from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.capabilities import CapabilityRegistry
from app.domain.enums import TaskStatus
from app.execution import ConversationExecutor, ConversationTurn, FakeExecutor
from app.execution.conversation import parse_reply
from app.execution.fake import ExecutionCancelled
from app.integrations.kimi import ChatCompletion, ChatMessage, KimiChatClient, KimiError
from app.manager import DeterministicManager
from app.service import TaskService
from app.worker import TaskWorker


class RecordingChatClient:
    provider = "kimi"
    model = "test-model"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages: list[ChatMessage], *, max_tokens: int = 700) -> ChatCompletion:
        self.calls.append(messages)
        return ChatCompletion(
            text=self.replies.pop(0), model=self.model, input_tokens=10, output_tokens=5
        )


def test_kimi_client_posts_chat_completion_and_reads_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "kimi-for-coding-highspeed",
                "choices": [{"message": {"content": "Hello there."}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    client = KimiChatClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    completion = client.complete([ChatMessage("user", "Hi")], max_tokens=50)

    assert completion == ChatCompletion("Hello there.", "kimi-for-coding-highspeed", 12, 3)
    assert seen["path"].endswith("/chat/completions")
    assert seen["authorization"] == "Bearer sk-test"
    assert seen["body"]["messages"] == [{"role": "user", "content": "Hi"}]
    assert seen["body"]["max_tokens"] == 50


def test_kimi_errors_do_not_leak_key_or_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key sk-secret"}})

    client = KimiChatClient(api_key="sk-secret", transport=httpx.MockTransport(handler))
    with pytest.raises(KimiError) as error:
        client.complete([ChatMessage("user", "Hi")])
    assert str(error.value) == "Kimi returned HTTP 401"


@pytest.mark.parametrize(
    ("raw", "reply", "emotion"),
    [
        (
            '{"reply": "Sure, let us plan it.", "emotion": "Excited"}',
            "Sure, let us plan it.",
            "Excited",
        ),
        ('```json\n{"reply": "Okay.", "emotion": "concerned"}\n```', "Okay.", "Concerned"),
        ('{"reply": "Hi!", "emotion": "Angry"}', "Hi!", "Warm"),
        ("**Sure!** Here is a *plan*.", "Sure! Here is a plan.", "Warm"),
        ("- First wake up\n- Then stretch", "First wake up Then stretch", "Warm"),
    ],
)
def test_parse_reply_produces_speakable_text(raw: str, reply: str, emotion: str) -> None:
    assert parse_reply(raw) == (reply, emotion)


def test_parse_reply_truncates_long_answers_at_a_sentence() -> None:
    text = "This is a sentence. " * 60
    reply, _ = parse_reply(text)
    assert len(reply) <= 600
    assert reply.endswith(".")


def test_parse_reply_rejects_empty_output() -> None:
    with pytest.raises(ValueError):
        parse_reply('{"reply": "   "}')


def test_executor_sends_history_and_returns_reply() -> None:
    client = RecordingChatClient(['{"reply": "Tomorrow looks clear.", "emotion": "Curious"}'])
    executor = ConversationExecutor(client, now=lambda: datetime(2026, 9, 14, 8, 0, tzinfo=UTC))

    result = executor.execute(
        task_id="t1",
        request="And tomorrow?",
        is_cancelled=lambda: False,
        history=[ConversationTurn("What is today like?", "Today is busy.")],
    )

    roles = [message.role for message in client.calls[0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "Monday 14 September 2026" in client.calls[0][0].content
    assert client.calls[0][-1].content == "And tomorrow?"
    assert result.output["reply"] == "Tomorrow looks clear."
    assert result.output["emotion"] == "Curious"
    assert result.output["usage"] == {"input_tokens": 10, "output_tokens": 5}


def test_executor_does_not_call_model_when_cancelled() -> None:
    client = RecordingChatClient([])
    with pytest.raises(ExecutionCancelled):
        ConversationExecutor(client).execute(task_id="t1", request="Hi", is_cancelled=lambda: True)
    assert client.calls == []


def test_registry_disables_capabilities_without_installed_adapters() -> None:
    registry = CapabilityRegistry.from_directory("capabilities")
    assert registry.select(["task_execution"]).id == "kimi-conversation"
    assert (
        registry.restricted_to_adapters({"fake"}).select(["task_execution"]).id == "fake-executor"
    )


def _conversation_worker(service: TaskService, client: RecordingChatClient) -> TaskWorker:
    executors = {
        "fake": FakeExecutor(delay_seconds=0),
        "kimi-conversation": ConversationExecutor(client),
    }
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters(executors)
    return TaskWorker(
        service=service,
        manager=DeterministicManager(registry),
        executors=executors,
        worker_id="test-worker",
        lease_seconds=30,
        poll_interval_seconds=0.01,
    )


def test_worker_answers_with_model_and_remembers_the_conversation(service: TaskService) -> None:
    client = RecordingChatClient(
        [
            '{"reply": "You have a free morning.", "emotion": "Warm"}',
            '{"reply": "Tomorrow starts at nine.", "emotion": "Excited"}',
            '{"reply": "Hello, stranger.", "emotion": "Neutral"}',
        ]
    )
    worker = _conversation_worker(service, client)
    first = service.create_task(
        request="What does today look like?", source_context={"conversation_id": "c1"}
    )
    assert worker.run_once() is True
    second = service.create_task(request="And tomorrow?", source_context={"conversation_id": "c1"})
    assert worker.run_once() is True
    other = service.create_task(request="Hi", source_context={"conversation_id": "c2"})
    assert worker.run_once() is True

    assert service.get_task(second.id).status == TaskStatus.COMPLETED.value
    second_messages = [(message.role, message.content) for message in client.calls[1]]
    assert ("user", "What does today look like?") in second_messages
    assert ("assistant", json.dumps({"reply": "You have a free morning."})) in second_messages
    assert all(message.role in {"system", "user"} for message in client.calls[2])

    reply = service.list_events(second.id)[-2]
    assert reply.event_type == "ASSISTANT_REPLY"
    assert reply.payload["text"] == "Tomorrow starts at nine."
    assert reply.payload["emotion"] == "Excited"
    assert service.get_task(first.id).status == TaskStatus.COMPLETED.value
    assert service.get_task(other.id).status == TaskStatus.COMPLETED.value
