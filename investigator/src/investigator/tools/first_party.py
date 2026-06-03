from __future__ import annotations
import subprocess
import time
from pathlib import Path
from investigator.result import ToolResult

_MAX_LINES = 2000
_MAX_MATCHES = 100
_MAX_ENTRIES = 200


def read_file(path: Path, start_line: int = 1, end_line: int | None = None) -> ToolResult:
    t0 = time.monotonic()
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return ToolResult.error(f"File not found: {path}", error_code="FILE_NOT_FOUND")
    except Exception as e:
        return ToolResult.error(str(e), error_code="READ_ERROR")

    start = max(0, start_line - 1)
    end = end_line if end_line else len(lines)
    chunk = lines[start:end]
    truncated = len(chunk) > _MAX_LINES
    chunk = chunk[:_MAX_LINES]

    numbered = "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(chunk))
    ms = int((time.monotonic() - t0) * 1000)
    return ToolResult.ok(data=numbered, duration_ms=ms, truncated=truncated)


def grep(pattern: str, path: str, file_glob: str = "") -> ToolResult:
    t0 = time.monotonic()
    cmd = ["grep", "-rn", "--include", file_glob or "*.py", pattern, path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return ToolResult.error("grep timed out", error_code="TIMEOUT")
    except Exception as e:
        return ToolResult.error(str(e), error_code="GREP_ERROR")

    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    truncated = len(lines) > _MAX_MATCHES
    output = "\n".join(lines[:_MAX_MATCHES])
    ms = int((time.monotonic() - t0) * 1000)
    return ToolResult.ok(data=output, duration_ms=ms, truncated=truncated)


def list_dir(path: Path, max_depth: int = 3) -> ToolResult:
    t0 = time.monotonic()
    p = Path(path)
    if not p.exists():
        return ToolResult.error(f"Directory not found: {path}", error_code="DIR_NOT_FOUND")

    entries = []
    try:
        for item in sorted(p.rglob("*")):
            rel = item.relative_to(p)
            depth = len(rel.parts)
            if depth <= max_depth:
                prefix = "  " * (depth - 1)
                name = item.name + ("/" if item.is_dir() else "")
                entries.append(f"{prefix}{name}")
            if len(entries) >= _MAX_ENTRIES:
                break
    except Exception as e:
        return ToolResult.error(str(e), error_code="LIST_ERROR")

    truncated = len(entries) >= _MAX_ENTRIES
    ms = int((time.monotonic() - t0) * 1000)
    return ToolResult.ok(data="\n".join(entries), duration_ms=ms, truncated=truncated)
