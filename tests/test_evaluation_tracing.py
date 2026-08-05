from __future__ import annotations

from tuco_ai_backend.evaluation_tracing import (
    EvaluationTraceCollector,
    redact_sensitive_data,
)


def test_trace_collector_groups_events_by_trace_id() -> None:
    collector = EvaluationTraceCollector()
    collector.record("trace-1", "provider_request", {"model": "fake"})
    collector.record("trace-1", "provider_response", {"choices": []})

    assert collector.take("trace-1") == {
        "provider_request": {"model": "fake"},
        "provider_response": {"choices": []},
    }
    assert collector.take("trace-1") == {}


def test_trace_collector_copies_redacts_and_replaces_repeated_events() -> None:
    collector = EvaluationTraceCollector()
    payload = {"api_key": "secret", "items": [{"value": 1}]}

    collector.record("trace-1", "provider_request", payload)
    payload["items"][0]["value"] = 2
    collector.record("trace-1", "provider_request", {"model": "latest"})

    assert collector.take("trace-1") == {
        "provider_request": {"model": "latest"},
    }


def test_redact_sensitive_data_handles_headers_fields_and_url_queries() -> None:
    redacted = redact_sensitive_data(
        {
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "X-Trace": "ok",
            },
            "api_key": "secret",
            "nested": {
                "access_token": "secret",
                "password": "secret",
                "client_secret": "secret",
            },
            "url": "https://example.test/v1?token=secret&mode=test#fragment",
        }
    )

    assert redacted["headers"]["Authorization"] == "[REDACTED]"
    assert redacted["headers"]["Cookie"] == "[REDACTED]"
    assert redacted["headers"]["X-Trace"] == "ok"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"] == {
        "access_token": "[REDACTED]",
        "password": "[REDACTED]",
        "client_secret": "[REDACTED]",
    }
    assert redacted["url"] == (
        "https://example.test/v1?token=%5BREDACTED%5D&mode=test#fragment"
    )
