from __future__ import annotations
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator


class _NoopSpan:
    def finish(self, *, output: Any = None, metadata: dict | None = None) -> None:
        pass

    @asynccontextmanager
    async def span(self, name: str, *, as_type: str = "span", input: Any = None) -> AsyncGenerator["_NoopSpan", None]:
        yield _NoopSpan()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _LangfuseSpan:
    def __init__(self, client):
        self._client = client

    def finish(self, *, output: Any = None, metadata: dict | None = None) -> None:
        try:
            self._client.update_current_span(output=output, metadata=metadata or {})
        except Exception:
            pass

    @asynccontextmanager
    async def span(self, name: str, *, as_type: str = "span", input: Any = None) -> AsyncGenerator["_LangfuseSpan", None]:
        try:
            with self._client.start_as_current_observation(name=name, as_type=as_type, input=input):
                yield _LangfuseSpan(self._client)
        except Exception:
            yield _NoopSpan()  # type: ignore[misc]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _LangfuseTrace(_LangfuseSpan):
    def finish(self, *, output: Any = None, metadata: dict | None = None) -> None:
        try:
            self._client.update_current_span(output=output, metadata=metadata or {})
            self._client.set_current_trace_io(output=output)
        except Exception:
            pass


class Tracer:
    def __init__(self):
        key = os.getenv("LANGFUSE_PUBLIC_KEY")
        if key:
            from langfuse import Langfuse
            self._client = Langfuse(
                public_key=key,
                secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
            self.enabled = True
        else:
            self._client = None
            self.enabled = False

    @asynccontextmanager
    async def trace(self, name: str, input: Any = None, **kwargs) -> AsyncGenerator[_NoopSpan | _LangfuseTrace, None]:
        if self._client:
            metadata = {k: str(v) for k, v in kwargs.items()} if kwargs else None
            with self._client.start_as_current_observation(
                name=name,
                as_type="agent",
                input=input,
                metadata=metadata,
            ):
                yield _LangfuseTrace(self._client)
        else:
            yield _NoopSpan()
