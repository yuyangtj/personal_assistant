"""A detailed plan for a coding task, written by a strong model that reads the code.

The planner (Claude Code on Opus, read-only) explores the checkout and splits the change
into small steps, each with its files, exact instructions and when it is done; a small
change is one step. A cheaper agent then implements one step per session
(``CodingPullRequestExecutor``), seeing only the goal, short notes on the finished
steps, its own step and the titles of the rest. The plan and those notes are saved with
the run (``coding_runs.plan`` and ``.steps``), so a restarted run carries on where it
stopped. The reviewer checks the result against the whole plan. Planning is best
effort: without a usable plan the run falls back to the short spec.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.coding.agent_session import ProgressCallback
from app.coding.base import CodingAgentError
from app.coding.git import restore_clean_worktree
from app.coding.spec import CodingSpec, render_spec
from app.execution.fake import ExecutionCancelled
from app.integrations.model_json import extract_json_object

logger = logging.getLogger(__name__)

MAX_STEPS = 8

PLAN_PROMPT = """You plan a change to this repository; another, less capable coding agent
will implement it one step at a time, seeing the goal, its own step and the titles of
the others.
Do not edit any file: read the code (the files, tests, AGENTS.md or CLAUDE.md) to plan.

Make the plan concrete enough that each step needs no further design:
- A small change (one idea, a few files) is one step. Split a bigger one into up to 8
  small steps, in the order they must be done. Each step is one coherent edit an agent
  can finish and test on its own (say, a function and its test), leaves the repository
  working, and says everything its agent needs: it won't see the other steps' details.
- In each step name the real files to change or add, and say exactly what to do: the
  functions, classes, settings or prompts to add or change, their names and behaviour,
  and which existing code to reuse or follow as the pattern.
- Put tests in the step whose code they test, and name the test file.
- Include the wiring the change needs to work on the server even when the request
  doesn't mention it (AGENTS.md lists it: a setting in compose.remote.yaml and
  .env.remote.example, a migration, a Dockerfile COPY, the docs).
- When the change calls an outside API, list in the step that calls it the API's known
  pitfalls and how to handle and test each: pagination, items only visible with extra
  parameters, error codes that mean more than one thing, rate limits, token expiry.
- Cover only what the request asks for; don't add features.

When done, reply with only a JSON object:
{
  "goal": "one sentence: what the change achieves",
  "acceptance": ["When <situation>, <what> should <result>", ...],
  "out_of_scope": ["what this change must not do or touch"],
  "checks": ["commands to run at the end, such as the test suite and the linter"],
  "steps": [
    {"title": "short imperative title",
     "files": ["path/to/file.py", ...],
     "instructions": "exactly what to do in this step",
     "done_when": "how the agent knows this step is finished"}
  ]
}
1 to 8 acceptance criteria, each observable and testable. Keep the user's language for
the goal."""

STEP_HEADER = (
    "PLAN (written by a planner that read the code; follow it, and treat the request "
    "below as the source of truth if they disagree)"
)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    files: list[str] = Field(default_factory=list, max_length=30)
    instructions: str = Field(min_length=1, max_length=6000)
    done_when: str = Field(default="", max_length=1000)


class CodingPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal: str = Field(min_length=1, max_length=500)
    acceptance: list[str] = Field(min_length=1, max_length=8)
    out_of_scope: list[str] = Field(default_factory=list, max_length=20)
    checks: list[str] = Field(default_factory=list, max_length=10)
    steps: list[PlanStep] = Field(min_length=1, max_length=MAX_STEPS)

    def spec(self) -> CodingSpec:
        files = list(dict.fromkeys(path for step in self.steps for path in step.files))
        return CodingSpec(
            goal=self.goal,
            acceptance=self.acceptance,
            in_scope=files[:20],
            out_of_scope=self.out_of_scope,
            checks=self.checks,
        )


def render_plan(plan: CodingPlan) -> str:
    """The whole plan as text: for the task view, every step's prompt and the reviewer."""
    lines = [render_spec(plan.spec()), "Steps:"]
    for number, step in enumerate(plan.steps, start=1):
        lines.append(
            f"{number}. {step.title}" + (f" ({', '.join(step.files)})" if step.files else "")
        )
        lines.append(f"   {step.instructions}")
        if step.done_when:
            lines.append(f"   Done when: {step.done_when}")
    return "\n".join(lines)


#: How much of a finished step's report the later steps see.
DONE_NOTE_CHARACTERS = 300


def step_request(
    agent_request: str, plan: CodingPlan, number: int, done: list[dict] | None = None
) -> str:
    """The prompt for one step, with only the context it needs.

    The goal and limits, a short note on each finished step, this step in full, and the
    titles of the steps still to come; not the other steps' instructions.
    """
    step = plan.steps[number - 1]
    total = len(plan.steps)
    lines = [f"GOAL: {plan.goal}"]
    if plan.out_of_scope:
        lines.append("Out of scope: " + "; ".join(plan.out_of_scope))
    finished = [entry for entry in (done or []) if int(entry.get("number", 0)) < number]
    if finished:
        lines.append("Done so far (their changes are in the checkout):")
        lines += [
            f"{entry['number']}. {entry.get('title', '')}: "
            + str(entry.get("summary", ""))[:DONE_NOTE_CHARACTERS]
            for entry in finished
        ]
    later = plan.steps[number:]
    if later:
        lines.append(
            "Later steps, done separately (don't start them): "
            + "; ".join(f"{index}. {item.title}" for index, item in enumerate(later, number + 1))
        )
    lines += [
        "",
        f"NOW DO STEP {number} OF {total}: {step.title}",
        "Do only this step, run its tests, and stop.",
    ]
    if step.files:
        lines.append("Files: " + ", ".join(step.files))
    lines.append(step.instructions)
    if step.done_when:
        lines.append(f"Done when: {step.done_when}")
    return f"{STEP_HEADER}\n" + "\n".join(lines) + f"\nEND PLAN\n\n{agent_request}"


class ClaudePlanner:
    """Plans with Claude Code on a strong model, without letting it edit the checkout."""

    def __init__(self, runner, *, timeout_seconds: int = 600):
        self.runner = runner  # a ClaudeCodeRunner set to the planning model
        self.timeout_seconds = timeout_seconds

    def plan(
        self,
        request: str,
        worktree: Path,
        *,
        is_cancelled: Callable[[], bool],
        on_progress: ProgressCallback | None = None,
    ) -> CodingPlan | None:
        try:
            text = self.runner.ask(
                worktree=worktree,
                prompt=f"{PLAN_PROMPT}\n\nREQUEST\n{request.strip()}\nEND REQUEST",
                timeout_seconds=self.timeout_seconds,
                is_cancelled=is_cancelled,
                on_progress=on_progress,
            )
            return CodingPlan.model_validate(extract_json_object(text))
        except ExecutionCancelled:
            raise
        except (ValueError, ValidationError) as error:
            logger.warning("Coding plan reply was not usable: %s", error)
        except CodingAgentError as error:
            logger.warning("Coding plan could not be written (%s): %s", error.category, error)
        except Exception:  # planning is best effort; never fail the task over it
            logger.warning("Coding plan could not be written", exc_info=True)
        finally:
            # Read-only by its tools; this also undoes anything a shell command wrote.
            restore_clean_worktree(worktree)
        return None
