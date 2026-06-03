from __future__ import annotations

import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from .indexer import parse_python_file, parse_typescript_file, upsert_file
from .schema import create_schema

import kuzu

_SUPPORTED = {
    ".py": parse_python_file,
    ".ts": parse_typescript_file,
    ".tsx": parse_typescript_file,
}


class _Handler(FileSystemEventHandler):
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

    def _reindex_file(self, path_str: str) -> None:
        path = Path(path_str)
        if path.suffix not in _SUPPORTED:
            return
        if not path.exists():
            return
        parser = _SUPPORTED[path.suffix]
        with self._lock:
            try:
                db = kuzu.Database(self._db_path)
                conn = kuzu.Connection(db)
                create_schema(conn)
                ef = parser(path)
                upsert_file(conn, ef)
                conn.close()
                db.close()
            except Exception:
                pass

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._reindex_file(str(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._reindex_file(str(event.src_path))


class RepoWatcher:
    def __init__(self, repo_path: str, db_path: str) -> None:
        self._repo_path = repo_path
        self._db_path = db_path
        self._observer = Observer()
        self._handler = _Handler(db_path)

    def start(self) -> None:
        self._observer.schedule(self._handler, self._repo_path, recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    def is_alive(self) -> bool:
        return self._observer.is_alive()
