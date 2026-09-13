from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any

from app.manager.model.contracts import ManagerModelRequest, ModelInvocation

ScriptedResponse = str | dict[str, Any] | ModelInvocation | Exception


class ScriptedManagerModelClient:
    """Deterministic model client for contract and orchestration tests."""

    provider = "scripted"
    model = "scripted-manager"

    def __init__(self, responses: Iterable[ScriptedResponse]):
        self._responses = deque(responses)
        self.requests: list[ManagerModelRequest] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def generate(
        self,
        request: ManagerModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelInvocation:
        del timeout_seconds
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("No scripted model response remains")
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        if isinstance(response, ModelInvocation):
            return response
        return ModelInvocation(raw_output=response)
