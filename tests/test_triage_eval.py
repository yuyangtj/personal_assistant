"""The routing eval script (scripts/triage_eval) keeps working as the router changes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tests.test_triage import ScriptedChat, suggestion

SCRIPT = Path("scripts/triage_eval/run.py")


def _load():
    spec = importlib.util.spec_from_file_location("triage_eval", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ideal(case: dict) -> str:
    """What a perfect router would answer for a labelled case."""
    fields: dict = {"intent": case["intent"]}
    if case.get("action"):
        action = {"type": case["action"], "text": case["m"][:100]}
        if case["action"] in ("remind", "routine"):
            action["at"] = "2026-10-06T09:00"
            action["recurrence"] = "weekly" if case["action"] == "routine" else "none"
        fields["action"] = action
    if case["intent"] == "propose_coding":
        fields["repository_id"] = "personal-assistant"
    fields["about_work"] = bool(case.get("about_work"))
    fields["needs_grocery_tools"] = bool(case.get("groceries"))
    return suggestion(**fields)


def test_a_perfect_router_scores_every_case_and_a_constant_one_does_not() -> None:
    evaluate = _load().evaluate
    cases = json.loads(Path("scripts/triage_eval/cases.json").read_text())

    perfect = evaluate(ScriptedChat(*[_ideal(case) for case in cases]), cases)
    always_answer = evaluate(ScriptedChat(*[suggestion()] * len(cases)), cases)

    assert perfect["accuracy"] == f"{len(cases)}/{len(cases)}"
    assert perfect["invalid_or_failed"] == 0
    assert always_answer["accuracy"] != perfect["accuracy"]


def test_a_failing_model_counts_as_wrong_instead_of_falling_back_to_rules() -> None:
    evaluate = _load().evaluate
    case = {
        "m": "remember that my partner's name is Anna",
        "intent": "direct_action",
        "action": "remember",
    }

    result = evaluate(ScriptedChat(RuntimeError("down"), RuntimeError("down")), [case])

    assert result["accuracy"] == "0/1" and result["invalid_or_failed"] == 1
