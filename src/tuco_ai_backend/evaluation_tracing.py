from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from threading import Lock
from typing import Any, Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TraceEventName = Literal[
    "decision_request",
    "conversation_history",
    "semantic_plan",
    "provider_request",
    "provider_attempts",
    "provider_response",
    "route_decision",
    "normalized_decision",
    "exception",
]

REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = ("token", "api_key", "secret", "password")
_SENSITIVE_HEADER_KEYS = {"authorization", "cookie", "set_cookie"}


class DecisionTraceSink(Protocol):
    def record(self, trace_id: str, event: TraceEventName, payload: Any) -> None: ...


def _normalized_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_HEADER_KEYS or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    )


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        return value
    query = urlencode(
        [
            (key, REDACTED if _is_sensitive_key(key) else item_value)
            for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(key) else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str):
        return _redact_url(value)
    return value


class EvaluationTraceCollector:
    def __init__(self) -> None:
        self._events: dict[str, dict[TraceEventName, Any]] = {}
        self._lock = Lock()

    def record(self, trace_id: str, event: TraceEventName, payload: Any) -> None:
        safe_payload = redact_sensitive_data(deepcopy(payload))
        with self._lock:
            self._events.setdefault(trace_id, {})[event] = safe_payload

    def take(self, trace_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._events.pop(trace_id, {}))
