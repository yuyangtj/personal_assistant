"""What an agent is told when it runs as a routine: earlier results and, for a watch,
when a result deserves a push."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


def routine_prompt(context: Mapping[str, Any]) -> str | None:
    routine = context.get("routine")
    if not isinstance(routine, dict):
        return None
    lines = [
        "This is a scheduled routine run: the user asked for this to be done regularly and "
        "will read the answer later (it is also pushed to their phone)."
    ]
    previous = [run for run in routine.get("previous_runs") or [] if isinstance(run, dict)]
    if previous:
        lines.append("Earlier runs of this routine, newest first; compare with them when useful:")
        lines += [f"- {run.get('date')}: {str(run.get('result'))[:1500]}" for run in previous]
    if routine.get("watch"):
        lines.append(
            'This routine is a watch. Add "notable": true to your JSON only when what the user '
            "asked to be told about has happened (or something changed they would want a "
            'notification for); otherwise "notable": false.'
        )
    return "\n".join(lines)


def notable(text: str) -> bool:
    """Whether the model's JSON reply marked its result as worth a push."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return False
    try:
        decoded = json.loads(match.group(0))
    except json.JSONDecodeError:
        return False
    return isinstance(decoded, dict) and decoded.get("notable") is True
