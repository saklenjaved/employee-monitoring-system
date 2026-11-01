"""Scripted fake of requests.Session. Each call pops the next scripted outcome.

An outcome is (status, json_body), or an Exception instance to raise (for dropped connections).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeResponse:
    status_code: int
    body: Any = None

    def json(self) -> Any:
        if isinstance(self.body, str):
            raise ValueError("not json")
        return self.body


@dataclass
class ScriptedSession:
    outcomes: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.outcomes:
            raise AssertionError(f"unexpected request {method} {url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        status, body = outcome
        return FakeResponse(status, body)

    def headers_of(self, index: int) -> dict[str, str]:
        return self.calls[index].get("headers", {})

    def json_body_of(self, index: int) -> Any:
        return self.calls[index].get("json")


def wire_json(obj: Any) -> str:
    return json.dumps(obj)
