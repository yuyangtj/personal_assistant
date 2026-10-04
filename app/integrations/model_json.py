"""Extracting the JSON object a model was asked to return."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_object(text: str) -> Any:
    """The JSON object in a reply, tolerating reasoning tags, fences, or prose around it."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(cleaned[start : end + 1])
