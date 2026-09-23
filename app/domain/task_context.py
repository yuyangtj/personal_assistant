"""Curated, trusted summaries of a task for follow-up conversations.

A task's event stream stays authoritative and complete, but only a small whitelist of it
belongs in a model prompt or a chat transcript: the request, the goal, the outcome, the
validation verdict and artifact references. Polling events, raw tool responses, diffs,
execution inputs and provider usage never pass through here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.domain.enums import EventType, TaskStatus
from app.persistence.models import TaskEventModel, TaskModel

MAX_TEXT_CHARACTERS = 1200
MAX_ERROR_CHARACTERS = 600
MAX_ARTIFACTS = 8

#: The only artifact fields a follow-up prompt ever sees. Anything else an executor
#: attaches (diffs, logs, credentials) stays in Task Details.
ARTIFACT_FIELDS = (
    "type",
    "url",
    "repository",
    "number",
    "head_branch",
    "head_sha",
    "merge_sha",
)

_VALIDATION_BY_EVENT = {
    EventType.VALIDATION_FAILED: "failed",
    EventType.VALIDATION_SUCCEEDED: "passed",
    EventType.VALIDATION_STARTED: "in_progress",
}


def _clean(value: Any, *, limit: int = MAX_TEXT_CHARACTERS) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _last_payload(
    events: Sequence[TaskEventModel],
    event_type: EventType,
) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.event_type == event_type.value and isinstance(event.payload, dict):
            return event.payload
    return None


def _artifact_payloads(events: Sequence[TaskEventModel]) -> Iterable[dict[str, Any]]:
    output = _last_payload(events, EventType.EXECUTION_OUTPUT_RECEIVED) or {}
    embedded = (output.get("output") or {}).get("artifacts")
    if isinstance(embedded, list):
        for artifact in embedded:
            if isinstance(artifact, dict):
                yield artifact
    for event in events:
        if event.event_type == EventType.ARTIFACT_CREATED.value and isinstance(event.payload, dict):
            yield event.payload


def _artifact(payload: dict[str, Any]) -> dict[str, Any]:
    picked: dict[str, Any] = {}
    for field in ARTIFACT_FIELDS:
        value = payload.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            picked[field] = value
        elif (text := _clean(value, limit=200)) is not None:
            picked[field] = text
    picked.setdefault("type", "artifact")
    return picked


def _artifact_label(artifact: dict[str, Any]) -> str:
    kind = str(artifact.get("type", "artifact"))
    number = artifact.get("number")
    suffix = f" #{number}" if isinstance(number, int) and number > 0 else ""
    if kind == "github_pull_request":
        return f"Pull request{suffix}"
    if kind == "github_pull_request_merge":
        return f"Merged pull request{suffix}"
    return kind.replace("_", " ").capitalize() + suffix


def _validation(events: Sequence[TaskEventModel]) -> str:
    seen = {event.event_type for event in events}
    for event_type, verdict in _VALIDATION_BY_EVENT.items():
        if event_type.value in seen:
            return verdict
    return "not_started"


def build_task_context(
    task: TaskModel,
    events: Sequence[TaskEventModel],
) -> dict[str, Any]:
    """The whitelisted summary of a task that a follow-up conversation may see."""
    output = (_last_payload(events, EventType.EXECUTION_OUTPUT_RECEIVED) or {}).get("output")
    output = output if isinstance(output, dict) else {}
    reply = _last_payload(events, EventType.ASSISTANT_REPLY) or {}
    failure = _last_payload(events, EventType.TASK_FAILED) or {}

    final_answer = _clean(reply.get("text")) or _clean(output.get("reply"))
    summary = _clean(output.get("summary"))
    artifacts = [_artifact(payload) for payload in _artifact_payloads(events)]
    deduplicated: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact not in deduplicated:
            deduplicated.append(artifact)

    return {
        "task_id": task.id,
        "status": TaskStatus(task.status).value,
        "request": _clean(task.original_request) or "",
        "goal": _clean(task.current_goal) or "",
        "chat_session_id": task.chat_session_id,
        "origin_message_id": task.origin_message_id,
        "parent_task_id": task.parent_task_id,
        "superseded_by_task_id": task.superseded_by_task_id,
        "final_answer": final_answer,
        "summary": summary if summary != final_answer else None,
        "validation": _validation(events),
        "artifacts": deduplicated[:MAX_ARTIFACTS],
        "error": _clean(failure.get("error"), limit=MAX_ERROR_CHARACTERS),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def render_task_context(context: dict[str, Any]) -> str:
    """A short plain-text rendering, used for chat references and follow-up prompts."""
    lines = [
        f'Task "{context.get("request", "")}"',
        f"Status: {str(context.get('status', 'unknown')).replace('_', ' ')}",
    ]
    goal = context.get("goal")
    if goal and goal != context.get("request"):
        lines.append(f"Goal: {goal}")
    validation = context.get("validation")
    if validation and validation != "not_started":
        lines.append(f"Validation: {validation.replace('_', ' ')}")
    if answer := context.get("final_answer"):
        lines.append(f"Result: {answer}")
    elif summary := context.get("summary"):
        lines.append(f"Result: {summary}")
    if error := context.get("error"):
        lines.append(f"Error: {error}")
    for artifact in context.get("artifacts", []):
        detail = " · ".join(
            str(artifact[field])
            for field in ("repository", "head_branch", "url")
            if artifact.get(field)
        )
        lines.append(f"Artifact: {_artifact_label(artifact)}{f' — {detail}' if detail else ''}")
    return "\n".join(lines)
