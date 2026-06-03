import pytest
from investigator.observability.tracer import Tracer


def test_tracer_noop_when_no_env(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tracer = Tracer()
    assert tracer.enabled is False


def test_noop_trace_context_manager(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    tracer = Tracer()
    with tracer.trace("test-query") as t:
        with t.span("turn", turn=0) as s:
            s.set("model", "claude-sonnet")
    # should not raise


def test_noop_span_set_does_nothing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    tracer = Tracer()
    with tracer.trace("q") as t:
        with t.span("tool") as s:
            s.set("tool_name", "find_symbol")
            s.set("duration_ms", 22)
