"""Replies streamed as they are written, so the phone can start speaking at the first sentence."""

from __future__ import annotations

import json

import httpx
import pytest

from app.execution.conversation import ConversationExecutor
from app.execution.draft import ReplyDraft, partial_reply
from app.integrations.chat import ChatCompletion, ChatMessage, stream_within
from app.integrations.deepseek import DeepSeekChatClient, DeepSeekError


def _events(*chunks: dict) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def _client(body: bytes, status: int = 200) -> httpx.Client:
    return httpx.Client(
        base_url="https://x",
        transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body)),
    )


def test_a_stream_is_assembled_like_a_whole_reply_and_its_text_handed_over() -> None:
    body = _events(
        {"model": "m", "choices": [{"delta": {"content": '{"reply": "Hel'}}]},
        {"choices": [{"delta": {"content": 'lo."}'}}]},
        {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "re"}}]}}
            ]
        },
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "mind"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"a"'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}}]},
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}},
    )
    pieces: list[str] = []

    status, assembled = stream_within(
        _client(body), "/chat/completions", json={}, deadline_seconds=5, on_text=pieces.append
    )

    assert status == 200 and pieces == ['{"reply": "Hel', 'lo."}']
    message = assembled["choices"][0]["message"]
    assert message["content"] == '{"reply": "Hello."}'
    assert message["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "remind", "arguments": '{"a": 1}'}}
    ]
    assert assembled["usage"]["completion_tokens"] == 4 and assembled["model"] == "m"


def test_the_deepseek_client_streams_only_when_asked_and_reports_errors_plainly() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if payload.get("stream"):
            return httpx.Response(
                200, content=_events({"choices": [{"delta": {"content": '{"reply": "Hi."}'}}]})
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "plain"}}]})

    client = DeepSeekChatClient(api_key="k", transport=httpx.MockTransport(handler))
    pieces: list[str] = []
    streamed = client.complete([ChatMessage("user", "hi")], on_text=pieces.append)
    plain = client.complete([ChatMessage("user", "hi")])

    assert (streamed.text, plain.text) == ('{"reply": "Hi."}', "plain")
    assert pieces == ['{"reply": "Hi."}']
    assert seen[0]["stream"] is True and "stream" not in seen[1]
    failing = DeepSeekChatClient(
        api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    with pytest.raises(DeepSeekError, match="HTTP 500"):
        failing.complete([ChatMessage("user", "hi")], on_text=pieces.append)


def test_the_reply_is_read_out_of_its_json_as_it_grows() -> None:
    assert partial_reply('{"emotion": "Warm", "reply": "It\'s 10 \\u00b0C') == ("It's 10 °C", False)
    assert partial_reply('{"reply": "Say \\"hi\\"\\nthen go."}') == ('Say "hi"\nthen go.', True)
    assert partial_reply('{"reply": "half an escape \\') == ("half an escape ", False)
    assert partial_reply('{"tool": "remind"}') == ("", False)

    emitted: list[str] = []
    draft = ReplyDraft(emitted.append)
    for piece in ['{"reply": "Start with xiao', "longbao. Then sheng", "jian bao! And", ' tea."}']:
        draft.feed(piece)
    assert emitted == [
        "Start with xiaolongbao.",
        "Start with xiaolongbao. Then shengjian bao!",
        "Start with xiaolongbao. Then shengjian bao! And tea.",
    ]
    quiet: list[str] = []
    ReplyDraft(quiet.append).feed("<｜DSML｜function_calls>remind ...")  # a tool call as text
    assert quiet == []


class StreamingChat:
    provider = "deepseek"
    model = "m"
    streams = True

    def __init__(self, text: str):
        self.text = text

    def complete(self, messages, *, max_tokens=700, on_text=None, **_):
        for i in range(0, len(self.text), 7):
            if on_text:
                on_text(self.text[i : i + 7])
        return ChatCompletion(text=self.text, model=self.model)

    def close(self) -> None:
        pass


def test_the_agent_reports_the_reply_a_sentence_at_a_time() -> None:
    reported: list[tuple[str, str]] = []
    executor = ConversationExecutor(
        StreamingChat('{"reply": "The museum opens at 10. It closes at 18.", "emotion": "Warm"}')
    )

    result = executor.execute(
        task_id="t",
        request="when is it open?",
        is_cancelled=lambda: False,
        context={"progress": lambda text, kind: reported.append((kind, text))},
    )

    assert result.output["reply"] == "The museum opens at 10. It closes at 18."
    assert [text for kind, text in reported if kind == "draft"] == [
        "The museum opens at 10.",
        "The museum opens at 10. It closes at 18.",
    ]


def test_the_task_api_carries_the_draft_apart_from_the_step(client) -> None:
    service = client.app.state.task_service
    chat = service.create_chat_session().id
    posted = service.append_chat_message(chat, content="hi")
    task = service.create_task_from_message(chat, posted.message.id)
    service.claim_next_task(worker_id="w", lease_seconds=30)
    service.record_progress(task.id, "Looking at Willys", "tool")
    service.record_progress(task.id, "Willys has coffee on offer.", "draft")

    (shown,) = client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]

    assert shown["progress"] == "Looking at Willys"
    assert shown["draft"] == "Willys has coffee on offer."
