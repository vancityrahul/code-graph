from __future__ import annotations
import os
from contextlib import contextmanager
from typing import Generator


class _NoopSpan:
    def set(self, key: str, value) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _NoopTrace:
    @contextmanager
    def span(self, name: str, **kwargs) -> Generator[_NoopSpan, None, None]:
        yield _NoopSpan()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _LangfuseSpan:
    """Wraps a langfuse v4 span context; metadata stored via update_current_span."""

    def __init__(self, client, span):
        self._client = client
        self._span = span

    def set(self, key: str, value) -> None:
        try:
            self._client.update_current_span(metadata={key: str(value)})
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _LangfuseTrace:
    def __init__(self, client):
        self._client = client

    @contextmanager
    def span(self, name: str, **kwargs) -> Generator[_LangfuseSpan, None, None]:
        metadata = {k: str(v) for k, v in kwargs.items()} if kwargs else None
        with self._client.start_as_current_observation(name=name, metadata=metadata) as s:
            yield _LangfuseSpan(self._client, s)

    def __enter__(self):
        return self

    def __exit__(self, *args):
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

    @contextmanager
    def trace(self, name: str, **kwargs) -> Generator[_NoopTrace | _LangfuseTrace, None, None]:
        if self._client:
            metadata = {k: str(v) for k, v in kwargs.items()} if kwargs else None
            with self._client.start_as_current_observation(name=name, as_type="span", metadata=metadata):
                yield _LangfuseTrace(self._client)
        else:
            yield _NoopTrace()
