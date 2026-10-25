"""Run whole chat conversations through the assistant, as the console does, and check them.

Routing is only half the story: a message can be routed "right" and still get a wrong or
empty answer, and follow-ups depend on everything said before. Each scenario in
scenarios.json is a short conversation; every turn goes through the real API (triage,
offers, choices) and the real worker and agents, with real models, against a fresh SQLite
database holding a made-up Klarna history. Each turn is then checked: where it was
routed, which tools ran with which arguments, whether a chart came back, and that there
is a real reply.

    python scripts/chat_eval/run.py                  # every scenario
    python scripts/chat_eval/run.py klarna-by-week   # only the named ones
    python scripts/chat_eval/run.py --json out.json  # also save every turn
    python scripts/chat_eval/run.py --main-agent     # the one-assistant path, no triage

With --main-agent, a turn's "main_expect" (when it has one) replaces its "expect".

Models and keys come from the environment like the server (ASSISTANT_*, KIMI_API_KEY,
MINIMAX_API_KEY). Unset choices default to production's: Kimi triage on, MiniMax answers.
No store accounts are connected, so the shopping agent has Klarna only.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # capabilities/, toolsets/ and the rest are found relative to the repo

# Production's choices (.env.remote.example), unless the environment says otherwise.
for key, value in {
    "ASSISTANT_TRIAGE_MODEL_ENABLED": "true",
    "ASSISTANT_TRIAGE_PROVIDER": "kimi",
    "ASSISTANT_CONVERSATION_MODEL_PROVIDER": "minimax",
}.items():
    os.environ.setdefault(key, value)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.capabilities import CapabilityRegistry  # noqa: E402
from app.config import Settings  # noqa: E402
from app.execution.tool_agent import ToolAgentExecutor  # noqa: E402
from app.main import create_app  # noqa: E402
from app.persistence.models import ExecutionModel  # noqa: E402
from app.work.memory import MemoryService  # noqa: E402
from app.work.purchases import PurchaseService  # noqa: E402
from app.worker import TaskWorker, add_chat_executors, build_manager  # noqa: E402

SCENARIOS = Path(__file__).with_name("scenarios.json")
#: The phone's clock, now: reminders are checked against the real time ("tomorrow at 9").
CLOCK = {
    "local_time": datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%dT%H:%M:%S"),
    "timezone": "Europe/Stockholm",
}
TURN_TIMEOUT_SECONDS = 180
MERCHANTS = [("Lidl", 180), ("Willys", 420), ("Apotea", 210), ("SJ", 388), ("Uniqlo", 499)]


def klarna_export() -> str:
    """Seven months of made-up purchases, a few a week, with a busy September."""
    rows = ["date,time,merchant,amount,currency,original_amount,payment_type,status,date_display"]
    day = date(2026, 3, 2)
    index = 0
    while day <= date(2026, 10, 5):
        merchant, amount = MERCHANTS[index % len(MERCHANTS)]
        if day.month == 9:
            amount *= 2
        rows.append(f"{day},12:00,{merchant},{amount},SEK,,Pay in 30 days,,{day}")
        day += timedelta(days=2 + index % 3)
        index += 1
    return "\n".join(rows) + "\n"


class Harness:
    """The API and a worker on one fresh database, driven like the console drives them."""

    def __init__(self, directory: Path, *, main_agent: bool = False):
        self.settings = replace(
            Settings.from_env(),
            database_url=f"sqlite+pysqlite:///{directory / 'eval.sqlite3'}",
            auto_create_schema=True,
            mcp_gateway_url=None,
            # Kept when set, so the read-only repository tools can be tested; nothing in the
            # eval pushes (coding runs are never executed here).
            ntfy_topic_url=None,
            brief_writeback_enabled=False,
            main_agent_enabled=main_agent,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app).__enter__()
        state = self.app.state
        executors: dict[str, Any] = {}
        add_chat_executors(
            executors,
            self.settings,
            state.database,
            state.task_service,
            state.workflow_service,
            state.repository_registry,
        )
        registry = CapabilityRegistry.from_directory(
            self.settings.capabilities_directory
        ).restricted_to_adapters(executors)
        self.worker = TaskWorker(
            service=state.task_service,
            manager=build_manager(self.settings, registry),
            executors=executors,
            worker_id="chat-eval",
            workflow_service=state.workflow_service,
            memory_service=MemoryService(state.database),
            supports_coding=False,
        )
        self.search = ToolAgentExecutor.id in executors
        PurchaseService(state.database).import_klarna_csv(klarna_export())

    def messages(self, chat: str) -> list[dict]:
        return self.client.get(f"/chat-sessions/{chat}/messages").json()["messages"]

    def say(self, chat: str, text: str, *, screen: str | None = None) -> dict:
        """One turn: post it, queue a reply if nothing handled it, let the worker finish."""
        started = time.monotonic()
        body: dict[str, Any] = {"content": text, **CLOCK}
        if screen:  # the phone's screen, shared with the message
            body["attachments"] = [{"kind": "screen", "app": "Chrome", "text": screen}]
        posted = self.client.post(f"/chat-sessions/{chat}/messages", json=body).json()
        message_id = posted["message"]["id"]
        if not posted["decision"]["handled"]:
            self.client.post(
                f"/chat-sessions/{chat}/messages/{message_id}/task",
                json={"source_context": {"client": "chat-eval", **CLOCK}},
            )
        deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
        while self.worker.run_once() and time.monotonic() < deadline:
            pass
        seconds = time.monotonic() - started
        messages = self.messages(chat)
        index = next(i for i, m in enumerate(messages) if m["id"] == message_id)
        replies = [m for m in messages[index + 1 :] if m["role"] == "assistant"]
        tasks = [
            task
            for task in self.client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]
            if task["origin_message_id"] == message_id
            or any(m["linked_task_id"] == task["id"] for m in replies)
        ]
        return {
            "said": text,
            "seconds": round(seconds, 1),
            "routing": messages[index].get("routing") or {},
            "replies": [m["content"] for m in replies],
            "blocks": [block for m in replies for block in m["blocks"]],
            "tasks": [
                {"capabilities": task["required_capabilities"], "status": task["status"]}
                for task in tasks
            ],
            "tool_calls": self._tool_calls([task["id"] for task in tasks]),
            "actions": [
                output["action"].get("type")
                for output in self._outputs([task["id"] for task in tasks])
                if isinstance(output.get("action"), dict)
            ],
        }

    def _outputs(self, task_ids: list[str]) -> list[dict]:
        with self.app.state.database.session() as session:
            outputs = session.scalars(
                select(ExecutionModel.output).where(ExecutionModel.task_id.in_(task_ids))
            ).all()
        return [output or {} for output in outputs]

    def tool_names(self) -> list[str]:
        """The chat agent's tools in this harness (store tools need the gateway)."""
        agent = self.worker.executors.get("model-conversation")
        return list(agent.toolbox.names) if agent is not None and agent.toolbox else []

    def _tool_calls(self, task_ids: list[str]) -> list[dict]:
        outputs = self._outputs(task_ids)
        calls = []
        for output in outputs:
            calls += output.get("tool_calls") or []
            calls += [
                {"tool": "search", "arguments": s} for s in (output or {}).get("searches") or []
            ]
        return calls

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.app.state.database.dispose()


def check(turn: dict, expect: dict) -> list[str]:
    """What is wrong with a turn, given what the scenario expects of it."""
    problems = []
    replies = [reply.strip() for reply in turn["replies"] if reply.strip()]
    if not replies:
        problems.append("no reply")
    if any(reply == "(no reply)" for reply in replies):
        problems.append('replied "(no reply)"')
    route = turn["routing"].get("route") or ""
    if "route" in expect and not any(route.startswith(r) for r in _list(expect["route"])):
        problems.append(f"routed to {route!r}, expected {expect['route']}")
    if "route_or_tool" in expect:
        # Done either instantly (that route) or by the agent with that tool.
        wanted_route, wanted_tool = expect["route_or_tool"]
        called = {call.get("tool") for call in turn["tool_calls"]}
        if not route.startswith(wanted_route) and wanted_tool not in called:
            problems.append(f"neither routed to {wanted_route!r} nor called {wanted_tool}")
    if expect.get("no_offer") and route.startswith("offer"):
        problems.append(f"asked instead of answering ({route})")
    capabilities = {c for task in turn["tasks"] for c in task["capabilities"]}
    if "capability" in expect and expect["capability"] not in capabilities:
        problems.append(f"ran {sorted(capabilities) or 'no task'}, expected {expect['capability']}")
    tools = turn["tool_calls"]
    if "tool" in expect:
        # "klarna.spending" in the shopping agent is klarna_spending in the chat agent.
        wanted_tool = expect["tool"].replace(".", "_")
        matching = [c for c in tools if str(c.get("tool")).replace(".", "_") == wanted_tool]
        wanted = expect.get("tool_args") or {}
        if not any(
            all((call.get("arguments") or {}).get(k) == v for k, v in wanted.items())
            for call in matching
        ):
            got = [(c.get("tool"), c.get("arguments")) for c in tools]
            problems.append(f"no {expect['tool']} call with {wanted}; calls: {got}")
    if "tools_all" in expect:
        # Every part of a message that asks for several things, each one carried out.
        done = {call.get("tool") for call in tools if call.get("ok", True)}
        if missing := [tool for tool in expect["tools_all"] if tool not in done]:
            called = [c.get("tool") for c in tools]
            problems.append(f"did not {', '.join(missing)}; calls: {called}")
    if "tools_any" in expect:
        called = {str(c.get("tool")).replace(".", "_") for c in tools if c.get("ok", True)}
        if not called.intersection(expect["tools_any"]):
            problems.append(f"called none of {expect['tools_any']}; calls: {sorted(called)}")
    if "action" in expect and expect["action"] not in turn.get("actions", []):
        problems.append(f"proposed no {expect['action']} card; actions: {turn.get('actions')}")
    if expect.get("chart") and not any(b.get("type") == "chart" for b in turn["blocks"]):
        problems.append("no chart")
    text = " ".join(replies).lower()
    # "or_tool": looking it up with that tool is as good as saying those words.
    looked_up = expect.get("or_tool") in {c.get("tool") for c in turn["tool_calls"]}
    if (
        "reply_any" in expect
        and not looked_up
        and not any(word.lower() in text for word in expect["reply_any"])
    ):
        problems.append(f"reply mentions none of {expect['reply_any']}")
    return problems


def _list(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * share))]


def covered_tools(scenarios: list[dict]) -> set[str]:
    """The tools some scenario expects to be called, in the chat agent's names."""
    names: set[str] = set()
    for scenario in scenarios:
        for step in scenario["turns"]:
            for expect in (step.get("expect") or {}, step.get("main_expect") or {}):
                if "tool" in expect:
                    names.add(expect["tool"])
                names.update(expect.get("tools_all", []))
                names.update(expect.get("tools_any", []))
                if "route_or_tool" in expect:
                    names.add(expect["route_or_tool"][1])
    return {name.replace(".", "_") for name in names}


def run_scenarios(args: argparse.Namespace, scenarios: list[dict]) -> dict[str, Any]:
    """One pass over the scenarios on a fresh database."""
    with tempfile.TemporaryDirectory() as directory:
        harness = Harness(Path(directory), main_agent=args.main_agent)
        settings = harness.settings
        triage = settings.triage_provider if settings.triage_model_enabled else "rules"
        if args.main_agent:
            triage = "none (main agent)"
        print(
            f"triage: {triage}, "
            f"answers: {harness.settings.conversation_model_provider}, "
            f"search: {'on' if harness.search else 'off'}"
        )
        results, seconds, passed_scenarios = [], [], 0
        try:
            for scenario in scenarios:
                if scenario.get("needs") == "search" and not harness.search:
                    print(f"- {scenario['name']}: skipped (no search key)")
                    continue
                if scenario.get("main_only") and not args.main_agent:
                    continue  # only the one-assistant path has these tools
                needed = scenario.get("needs_tools", [])
                if missing := [name for name in needed if name not in harness.tool_names()]:
                    print(f"- {scenario['name']}: skipped (no {', '.join(missing)} here)")
                    continue
                chat = harness.client.post("/chat-sessions", json={}).json()["id"]
                for title in scenario.get("lists", []):
                    harness.client.post("/work-items", json={"title": title, "kind": "list"})
                ok = True
                print(f"- {scenario['name']}")
                for step in scenario["turns"]:
                    turn = harness.say(chat, step["say"], screen=step.get("screen"))
                    expect = step.get("expect") or {}
                    if args.main_agent and "main_expect" in step:
                        expect = step["main_expect"]
                    problems = check(turn, expect)
                    ok &= not problems
                    seconds.append(turn["seconds"])
                    results.append({"scenario": scenario["name"], **turn, "problems": problems})
                    mark = "ok  " if not problems else "FAIL"
                    print(
                        f"    {mark} {turn['seconds']:5.1f}s {step['say'][:50]!r} → "
                        f"{turn['routing'].get('route')}"
                    )
                    for problem in problems:
                        print(f"           {problem}")
                    if problems:
                        reply = (turn["replies"] or [""])[-1].replace("\n", " ")
                        print(f"           reply: {reply[:160]!r}")
                passed_scenarios += ok
        finally:
            harness.close()
    turns_ok = sum(not r["problems"] for r in results)
    ran = len({r["scenario"] for r in results})
    summary = {
        "scenarios": f"{passed_scenarios}/{ran}",
        "turns": f"{turns_ok}/{len(results)}",
        "median_s": round(statistics.median(seconds), 1) if seconds else None,
        "p90_s": round(_percentile(seconds, 0.9), 1) if seconds else None,
    }
    print(json.dumps(summary))
    return {"summary": summary, "turns": results, "seconds": seconds}


def coverage(args: argparse.Namespace, scenarios: list[dict]) -> int:
    """Every chat-agent tool in this setup should be expected by some scenario."""
    with tempfile.TemporaryDirectory() as directory:
        harness = Harness(Path(directory), main_agent=True)
        try:
            tools = harness.tool_names()
        finally:
            harness.close()
    covered = covered_tools(scenarios)
    missing = [name for name in tools if name not in covered]
    print(f"{len(tools) - len(missing)}/{len(tools)} chat-agent tools have a scenario")
    for name in missing:
        print(f"  no scenario: {name}")
    return 1 if missing else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="*", help="scenarios to run (default: all)")
    parser.add_argument("--json", type=Path, help="save every turn here")
    parser.add_argument(
        "--main-agent", action="store_true", help="every message to the chat agent, no triage"
    )
    parser.add_argument("--repeat", type=int, default=1, help="run everything this many times")
    parser.add_argument(
        "--coverage", action="store_true", help="list chat-agent tools no scenario tests"
    )
    args = parser.parse_args()
    scenarios = json.loads(SCENARIOS.read_text())
    if args.coverage:
        sys.exit(coverage(args, scenarios))
    if args.names:
        scenarios = [s for s in scenarios if s["name"] in args.names]
    runs = []
    for number in range(args.repeat):
        if args.repeat > 1:
            print(f"== run {number + 1} of {args.repeat}")
        runs.append(run_scenarios(args, scenarios))
    if args.repeat > 1:
        # The release gate: a turn that failed in any run is a flaky one.
        flaky: dict[str, int] = {}
        for run in runs:
            for turn in run["turns"]:
                if turn["problems"]:
                    key = f"{turn['scenario']}: {turn['said'][:60]!r}"
                    flaky[key] = flaky.get(key, 0) + 1
        seconds = [value for run in runs for value in run["seconds"]]
        print(
            json.dumps(
                {
                    "runs": args.repeat,
                    "all_green": not flaky,
                    "median_s": round(statistics.median(seconds), 1) if seconds else None,
                    "p90_s": round(_percentile(seconds, 0.9), 1) if seconds else None,
                }
            )
        )
        for key, count in sorted(flaky.items()):
            print(f"  failed in {count} of {args.repeat} runs: {key}")
    if args.json:
        args.json.write_text(json.dumps([run | {"seconds": None} for run in runs], indent=2))


if __name__ == "__main__":
    main()
