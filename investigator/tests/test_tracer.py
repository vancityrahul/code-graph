import pytest
from investigator.observability.tracer import Tracer


def test_tracer_noop_when_no_env(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tracer = Tracer()
    assert tracer.enabled is False


async def test_noop_trace_context_manager(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    tracer = Tracer()
    async with tracer.trace("test-query", input="hi") as t:
        async with t.span("turn", as_type="span") as s:
            s.finish(metadata={"model": "claude-sonnet"})
        t.finish(output="done", metadata={"turns": 1})
    # should not raise


async def test_noop_span_finish_does_nothing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    tracer = Tracer()
    async with tracer.trace("q") as t:
        async with t.span("tool", as_type="tool", input={"name": "x"}) as s:
            s.finish(output="result")
