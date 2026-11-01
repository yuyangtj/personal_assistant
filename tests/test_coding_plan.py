"""Opus plans the change in small steps; the coding agents implement one step at a time."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.coding.base import CodeAgentReport, CodingAgentError
from app.coding.plan import ClaudePlanner, CodingPlan, PlanStep, render_plan, step_request
from app.coding.runners import ClaudeCodeRunner, FallbackCodeAgentRunner
from tests.test_coding import _Reviewer, _spec_executor, git

TASK = "12345678-1234-1234-1234-123456789abc"

PLAN = {
    "goal": "Greet in two files",
    "acceptance": ["When run, hello.txt and bye.txt should exist"],
    "out_of_scope": ["README.md"],
    "checks": ["pytest -q"],
    "steps": [
        {
            "title": "Add hello.txt",
            "files": ["hello.txt"],
            "instructions": "Write hello",
            "done_when": "hello.txt exists",
        },
        {"title": "Add bye.txt", "files": ["bye.txt"], "instructions": "Write bye"},
    ],
}


class _AskingRunner:
    """Stands in for Claude Code on Opus: answers the planning prompt."""

    def __init__(self, answer: str | Exception, *, scribble: bool = False):
        self.answer = answer
        self.scribble = scribble
        self.prompts: list[str] = []

    def ask(self, *, worktree: Path, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        if self.scribble:  # a shell command in the planning session wrote a file
            (worktree / "scratch.txt").write_text("x", encoding="utf-8")
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Planner:
    def __init__(self, plan: CodingPlan | None):
        self.result = plan
        self.requests: list[str] = []

    def plan(self, request: str, worktree: Path, **_kwargs):
        self.requests.append(request)
        return self.result


class _StepAgent:
    """Writes the file its step names; fails on the steps listed in ``fail_on``."""

    def __init__(self, provider: str, fail_on: tuple[int, ...] = ()):
        self.provider = provider
        self.fail_on = fail_on
        self.requests: list[str] = []

    def run(self, *, worktree: Path, request: str, **_kwargs) -> CodeAgentReport:
        self.requests.append(request)
        number = int(request.split("NOW DO STEP ", 1)[1].split(" ", 1)[0])
        if number in self.fail_on:
            (worktree / "partial.txt").write_text("half done\n", encoding="utf-8")
            raise CodingAgentError("got stuck", category="timeout")
        name = ["hello.txt", "bye.txt"][number - 1]
        (worktree / name).write_text(f"{self.provider}\n", encoding="utf-8")
        return CodeAgentReport(summary=f"{self.provider} did step {number}")


def test_the_planner_reads_a_plan_from_claude_and_leaves_the_checkout_clean(
    tmp_path: Path,
) -> None:
    repository, _executor = _checkout(tmp_path)
    runner = _AskingRunner("Here is the plan:\n" + json.dumps(PLAN), scribble=True)

    plan = ClaudePlanner(runner).plan("Greet", repository, is_cancelled=lambda: False)

    assert plan is not None and [step.title for step in plan.steps] == [
        "Add hello.txt",
        "Add bye.txt",
    ]
    assert "Do not edit any file" in runner.prompts[0]
    assert "REQUEST\nGreet\nEND REQUEST" in runner.prompts[0]
    assert not (repository / "scratch.txt").exists()


@pytest.mark.parametrize(
    "answer",
    ["not json", json.dumps({**PLAN, "steps": []}), CodingAgentError("x", category="quota")],
)
def test_without_a_usable_plan_the_planner_gives_none(tmp_path: Path, answer) -> None:
    repository, _executor = _checkout(tmp_path)

    plan = ClaudePlanner(_AskingRunner(answer)).plan(
        "Greet", repository, is_cancelled=lambda: False
    )

    assert plan is None


def test_planning_runs_claude_read_only_on_the_planning_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = {}

    def fake_process(**kwargs):
        seen.update(kwargs)
        return json.dumps({"type": "result", "is_error": False, "result": '{"goal": "x"}'})

    monkeypatch.setattr("app.coding.runners.run_code_agent_process", fake_process)

    runner = ClaudeCodeRunner.subscription(oauth_token="sk-ant-oat-x", model="opus")
    answer = runner.ask(
        worktree=tmp_path, prompt="plan it", timeout_seconds=60, is_cancelled=lambda: False
    )

    command = seen["command"]
    assert answer == '{"goal": "x"}'
    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--disallowedTools") + 1] == "Edit,MultiEdit,Write,NotebookEdit"
    assert command[-1] == "plan it"


def test_each_step_gets_the_whole_plan_and_only_its_own_task() -> None:
    plan = CodingPlan.model_validate(PLAN)

    second = step_request("Greet the user", plan, 2)

    assert render_plan(plan) in second
    assert "NOW DO STEP 2 OF 2: Add bye.txt" in second
    assert "Steps 1 to 1 are done" in second
    assert second.endswith("Greet the user")
    assert "1. Add hello.txt (hello.txt)" in render_plan(plan)
    assert "Done when: hello.txt exists" in render_plan(plan)


def test_a_planned_run_builds_step_by_step_and_opens_one_commit(tmp_path: Path) -> None:
    from app.coding.review import ReviewVerdict

    agent = _StepAgent("sonnet")
    reviewer = _Reviewer(ReviewVerdict(approved=True))
    planner = _Planner(CodingPlan.model_validate(PLAN))
    executor, github, repository, _remote = _spec_executor(tmp_path, agent, reviewer=reviewer)
    executor.planner = planner
    progress: list[tuple[str, str]] = []

    result = executor.execute(
        task_id=TASK,
        request="Greet",
        is_cancelled=lambda: False,
        context={"progress": lambda text, kind: progress.append((kind, text))},
    )

    assert len(agent.requests) == 2
    assert "NOW DO STEP 1 OF 2" in agent.requests[0]
    assert "NOW DO STEP 2 OF 2" in agent.requests[1]
    assert executor.spec_writer.requests == []  # the plan replaces the short spec
    assert ("milestone", "Step 1/2: Add hello.txt") in progress
    assert reviewer.references[0] == render_plan(planner.result)
    branch = f"assistant/task-{TASK.replace('-', '')[:12]}"
    git(repository, "fetch", "origin")
    assert git(repository, "rev-list", "--count", f"origin/main..origin/{branch}") == "1"
    changed = git(repository, "diff", "--name-only", f"origin/main..origin/{branch}").split()
    assert sorted(changed) == ["bye.txt", "hello.txt"]
    assert "1. Add hello.txt: sonnet did step 1" in result.output["summary"]
    assert github.bodies


def test_when_an_agent_gets_stuck_on_a_step_the_next_one_finishes_it(tmp_path: Path) -> None:
    sonnet = _StepAgent("sonnet", fail_on=(2,))
    kimi = _StepAgent("kimi")
    chain = FallbackCodeAgentRunner([sonnet, kimi])
    executor, _github, repository, _remote = _spec_executor(tmp_path, chain)
    executor.planner = _Planner(CodingPlan.model_validate(PLAN))

    executor.execute(task_id=TASK, request="Greet", is_cancelled=lambda: False)

    assert len(sonnet.requests) == 2  # step 1, then stuck on step 2
    assert [("NOW DO STEP 2" in r) for r in kimi.requests] == [True]
    branch = f"assistant/task-{TASK.replace('-', '')[:12]}"
    git(repository, "fetch", "origin")
    assert git(repository, "show", f"origin/{branch}:hello.txt") == "sonnet"  # step 1 kept
    assert git(repository, "show", f"origin/{branch}:bye.txt") == "kimi"
    files = git(repository, "diff", "--name-only", f"origin/main..origin/{branch}").split()
    assert "partial.txt" not in files


def test_without_a_plan_the_run_uses_the_short_spec_as_before(tmp_path: Path) -> None:
    from tests.test_coding import _RecordingAgent

    agent = _RecordingAgent()
    executor, _github, _repository, _remote = _spec_executor(tmp_path, agent)
    executor.planner = _Planner(None)

    executor.execute(task_id=TASK, request="Implement feature", is_cancelled=lambda: False)

    assert len(agent.requests) == 1
    assert agent.requests[0].startswith("SPEC (follow the acceptance criteria")
    assert executor.spec_writer.requests == ["Implement feature"]


def test_a_plan_step_needs_instructions() -> None:
    with pytest.raises(ValueError):
        PlanStep(title="x", instructions="")


def _checkout(tmp_path: Path):
    from tests.test_coding import _RecordingAgent

    executor, _github, repository, _remote = _spec_executor(tmp_path, _RecordingAgent())
    return repository, executor
