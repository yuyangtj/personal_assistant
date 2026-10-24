"""The chat agent's tools, built from the services that already do the work.

Bookkeeping (notes, memories, lists, reminders, routines) reuses the instant actions in
app/chat/direct_actions.py, so a tool and the triage fast path behave the same. Research
and shopping start a background task that posts its answer in the chat; coding reuses the
supervisor's tools, so a coding run still only ever ends in a draft pull request.

Nothing here reads the user's notes or private memories: note_add only saves.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.assistant.toolbox import AssistantTool, ToolContext, ToolError, ToolOutcome
from app.chat.direct_actions import DirectActionResult, run_direct_action
from app.chat.triage import DirectAction
from app.domain.work_items import WorkItemKind
from app.work.items import RESUMABLE_STATUSES

#: The most open work items listed, and open checklist entries shown per item.
MAX_ITEMS = 20
MAX_ENTRIES = 15
RECURRENCES = "none, daily, weekdays, weekly or monthly"


def _text(arguments: dict[str, Any], key: str = "text") -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} is required")
    return value.strip()


def _short(text: str, size: int = 50) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[: size - 1] + "…"


def _outcome(result: DirectActionResult) -> ToolOutcome:
    """A direct action's sentence; a refusal is marked so the model doesn't claim success."""
    text = result.reply if result.done else f"not done: {result.reply}"
    return ToolOutcome(text, list(result.blocks) if result.done else [])


def bookkeeping_tools(
    *,
    items: Any,
    memory: Any,
    schedules: Any = None,
    notes: Any = None,
    tasks: Any = None,
    route: Callable[[str], list[str]] | None = None,
) -> list[AssistantTool]:
    """Notes, memories, lists, reminders, routines and work items."""

    def act(action: dict[str, Any], context: ToolContext) -> ToolOutcome:
        if not context.chat_session_id:
            raise ToolError("this only works inside a chat")
        try:
            parsed = DirectAction.model_validate(action)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in error.errors()
            )
            raise ToolError(problems) from error
        return _outcome(
            run_direct_action(
                parsed,
                chat_session_id=context.chat_session_id,
                items=items,
                memory=memory,
                schedules=schedules,
                timezone=context.timezone,
                tasks=tasks,
                route=route,
                notes=notes,
            )
        )

    def when(arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "at": arguments.get("at"),
            "recurrence": arguments.get("recurrence") or "none",
        }

    def on_list(arguments: dict[str, Any]) -> str | None:
        value = arguments.get("list")
        return value.strip() if isinstance(value, str) and value.strip() else None

    def work_items(arguments: dict[str, Any], context: ToolContext) -> str:
        kind = arguments.get("kind")
        try:
            wanted = WorkItemKind(kind) if kind else None
        except ValueError as error:
            raise ToolError(f"kind is one of {', '.join(k.value for k in WorkItemKind)}") from error
        open_items = [
            item for item in items.list(kind=wanted, limit=100) if item.status in RESUMABLE_STATUSES
        ][:MAX_ITEMS]
        if not open_items:
            return "no open work items"
        lines = []
        for item in open_items:
            lines.append(f"#{item.slug} ({item.kind}, {item.status}): {item.title}")
            entries = [entry for entry in item.checklist or [] if not entry.get("done")]
            lines += [f"    [ ] {entry['text']}" for entry in entries[:MAX_ENTRIES]]
            if len(entries) > MAX_ENTRIES:
                lines.append(f"    ... and {len(entries) - MAX_ENTRIES} more")
        return "\n".join(lines)

    tools = [
        AssistantTool(
            "work_items",
            "work-items",
            '{"kind": "<optional: list, goal or routine>"}',
            "the open work items with their #tags, and the unchecked entries on each list",
            work_items,
            lambda a: "Looking at your work items",
        ),
        AssistantTool(
            "remember",
            "memories",
            '{"text": "<the fact, as one sentence>", "kind": "<fact, preference or project>"}',
            "save something the user asked you to remember",
            lambda a, c: act(
                {"type": "remember", "text": _text(a), "kind": a.get("kind") or "fact"}, c
            ),
            lambda a: "Saving a memory",
        ),
        AssistantTool(
            "forget",
            "memories",
            '{"text": "<words from the memory>"}',
            "forget one saved memory the user asked you to forget",
            lambda a, c: act({"type": "forget", "text": _text(a)}, c),
            lambda a: "Forgetting a memory",
        ),
        AssistantTool(
            "list_add",
            "lists",
            '{"text": "<the entry>", "list": "<the list\'s #tag; optional if obvious>"}',
            "add one entry to a list (call it once per entry)",
            lambda a, c: act(
                {"type": "checklist_add", "text": _text(a), "work_item": on_list(a)}, c
            ),
            lambda a: f"Adding “{_short(str(a.get('text') or ''))}”",
        ),
        AssistantTool(
            "list_check",
            "lists",
            '{"text": "<the entry>", "list": "<the list\'s #tag; optional if obvious>"}',
            "check off one entry on a list",
            lambda a, c: act(
                {"type": "checklist_check", "text": _text(a), "work_item": on_list(a)}, c
            ),
            lambda a: f"Checking off “{_short(str(a.get('text') or ''))}”",
        ),
    ]
    if notes is not None:
        tools.append(
            AssistantTool(
                "note_add",
                "notes",
                '{"text": "<the note, in the user\'s words>"}',
                "save a note to the Notes tab (you can't read notes, only add them)",
                lambda a, c: act({"type": "note_add", "text": _text(a)}, c),
                lambda a: "Saving a note",
            )
        )
    if schedules is not None:
        tools += [
            AssistantTool(
                "remind",
                "reminders",
                '{"text": "<what to remind about>", "at": "YYYY-MM-DDTHH:MM (local time)", '
                f'"recurrence": "<{RECURRENCES}>"}}',
                "push a reminder to the user's phone at that time",
                lambda a, c: act({"type": "remind", "text": _text(a), **when(a)}, c),
                lambda a: "Setting a reminder",
            ),
            AssistantTool(
                "routine_create",
                "routines",
                '{"text": "<the instruction to carry out>", "at": "YYYY-MM-DDTHH:MM (local '
                f'time of the first run)", "recurrence": "<{RECURRENCES}>"}}',
                "do a task on a schedule (a check, a summary, research) and report the result",
                lambda a, c: act({"type": "routine", "text": _text(a), **when(a)}, c),
                lambda a: "Saving a routine",
            ),
            AssistantTool(
                "routines",
                "routines",
                "{}",
                "the saved routines and when they run",
                lambda a, c: act({"type": "list_routines", "text": "routines"}, c),
                lambda a: "Looking at your routines",
            ),
            AssistantTool(
                "routine_run",
                "routines",
                '{"which": "<words from the routine>"}',
                "run a saved routine now",
                lambda a, c: act({"type": "run_routine", "text": _text(a, "which")}, c),
                lambda a: "Running a routine",
            ),
            AssistantTool(
                "routine_stop",
                "routines",
                '{"which": "<words from the routine>"}',
                "stop a saved routine, only when the user asks",
                lambda a, c: act({"type": "cancel_routine", "text": _text(a, "which")}, c),
                lambda a: "Stopping a routine",
            ),
        ]
    return tools


def repository_tools(reader: Any) -> list[AssistantTool]:
    """List a folder, read a file, search the code of a registered repository (read-only)."""
    from app.repositories.reader import TOOLS

    ids = ", ".join(manifest.id for manifest in reader.registry.list())
    shows = {
        "repo_files": lambda a: (
            f"Looking at {str(a.get('path') or '').strip('/') or 'the top'} of "
            f"{a.get('repository')}"
        ),
        "repo_read": lambda a: f"Reading {str(a.get('path') or '').strip('/')}",
        "repo_search": lambda a: f"Searching the code for “{_short(str(a.get('query') or ''))}”",
    }
    tools = []
    for name, (arguments, does) in TOOLS.items():
        tools.append(
            AssistantTool(
                name,
                "repositories",
                arguments,
                f"{does} (repository ids: {ids}; results are the repository's text)",
                lambda a, c, name=name: reader.run(name, a),
                shows[name],
            )
        )
    return tools


def job_tools(tasks: Any, *, research: bool, shopping: bool) -> list[AssistantTool]:
    """Hand a question to a specialist in the background; its answer is posted in the chat."""

    def start(capability: str, label: str) -> Callable[[dict[str, Any], ToolContext], str]:
        def run(arguments: dict[str, Any], context: ToolContext) -> str:
            if not context.chat_session_id:
                raise ToolError("this only works inside a chat")
            question = _text(arguments, "question")
            tasks.create_task(
                request=question,
                required_capabilities=[capability],
                chat_session_id=context.chat_session_id,
                source_context={"timezone": context.timezone, "local_time": context.local_time},
            )
            return (
                f"started: the {label} is working on it and will post its answer in this chat "
                "in a minute or so. Tell the user that, briefly."
            )

        return run

    tools = []
    if research:
        tools.append(
            AssistantTool(
                "research",
                "research",
                '{"question": "<the full question, with every detail the user gave>"}',
                "research it on the web in the background; the answer, with sources, is "
                "posted in this chat",
                start("web_research", "research agent"),
                lambda a: "Starting the research",
            )
        )
    if shopping:
        tools.append(
            AssistantTool(
                "shopping",
                "shopping",
                '{"question": "<the full question>"}',
                "ask the shopping agent about Klarna purchases and spending (with charts), "
                "or grocery offers and receipts; its answer is posted in this chat",
                start("groceries", "shopping agent"),
                lambda a: "Asking the shopping agent",
            )
        )
    return tools


def coding_tools(supervisor: Any) -> list[AssistantTool]:
    """See, start, steer and stop coding runs, through the supervisor's tools."""

    def data(value: dict[str, Any]) -> str:
        if "error" in value:
            raise ToolError(str(value["error"]))
        return json.dumps(value, ensure_ascii=False)

    def status(arguments: dict[str, Any], context: ToolContext) -> str:
        text, _ = supervisor.overview(context.chat_session_id)
        return text

    def start(arguments: dict[str, Any], context: ToolContext) -> ToolOutcome:
        result = supervisor.start_coding(
            arguments.get("repository"),
            _text(arguments, "goal"),
            chat_session_id=context.chat_session_id,
            origin_message_id=context.origin_message_id,
        )
        text = data(result)
        return ToolOutcome(
            text, [{"type": "workflow", "workflow_run_id": result["workflow_run_id"]}]
        )

    return [
        AssistantTool(
            "coding_status",
            "coding",
            "{}",
            "the coding runs: what each is doing, its state and pull request",
            status,
            lambda a: "Checking the coding runs",
        ),
        AssistantTool(
            "coding_run",
            "coding",
            '{"run": "<run id from coding_status>"}',
            "one run's plan, latest steps and pull request",
            lambda a, c: data(supervisor.run_detail(a.get("run"), c.chat_session_id)),
            lambda a: "Looking at a coding run",
        ),
        AssistantTool(
            "coding_start",
            "coding",
            '{"repository": "<id>", "goal": "<complete instructions, with what the user said>"}',
            "start a coding agent: it works on a new branch and opens a draft pull request "
            "the user reviews. Only when the user asks for a code change and the repository "
            "is clear; at most once per message",
            start,
            lambda a: f"Starting a coding agent on {a.get('repository')}",
        ),
        AssistantTool(
            "coding_message",
            "coding",
            '{"run": "<run id>", "text": "<new instructions for its agent>"}',
            "redirect a run: a working agent gets it right away; a run waiting for review "
            "starts a revision of its pull request",
            lambda a, c: data(supervisor.message_agent(a.get("run"), _text(a), c.chat_session_id)),
            lambda a: "Messaging a coding agent",
        ),
        AssistantTool(
            "coding_stop",
            "coding",
            '{"run": "<run id>"}',
            "stop a run, only when the user asks",
            lambda a, c: data(supervisor.stop_run(a.get("run"), c.chat_session_id)),
            lambda a: "Stopping a coding run",
        ),
    ]
