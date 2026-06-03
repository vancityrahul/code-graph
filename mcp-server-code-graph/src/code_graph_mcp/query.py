from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import kuzu

from .indexer import parse_python_file, parse_typescript_file, upsert_file
from .result import ToolResult
from .schema import SCHEMA_VERSION, create_schema, get_stored_version

_SUPPORTED = {
    ".py": parse_python_file,
    ".ts": parse_typescript_file,
    ".tsx": parse_typescript_file,
}


def _open_db(db_path: str) -> kuzu.Database:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return kuzu.Database(db_path)


def index_repo(repo_path: str, db_path: str, force: bool = False) -> str:
    db = _open_db(db_path)
    conn = kuzu.Connection(db)

    # get_stored_version throws if IndexMeta table doesn't exist yet
    try:
        stored = get_stored_version(conn)
    except Exception:
        stored = -1

    if stored != SCHEMA_VERSION or stored == -1:
        force = True  # schema mismatch → full rebuild

    create_schema(conn)

    if force:
        # Clear existing data — DETACH DELETE removes nodes and their relationships
        for tbl in ("Method", "Function", "Class", "Test", "Variable", "Import", "File", "Module"):
            try:
                conn.execute(f"MATCH (n:{tbl}) DETACH DELETE n")
            except Exception:
                pass

    root = Path(repo_path)
    files_indexed = 0
    for path in sorted(root.rglob("*")):
        if path.suffix in _SUPPORTED and not any(
            part.startswith(".") for part in path.parts
        ):
            try:
                parser = _SUPPORTED[path.suffix]
                ef = parser(path)
                upsert_file(conn, ef)
                files_indexed += 1
            except Exception as exc:
                import sys
                print(f"[code-graph-mcp] skipping {path}: {exc}", file=sys.stderr)

    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "MATCH (m:IndexMeta {id: 'singleton'}) "
            "SET m.last_indexed_at = $t, m.file_count = $fc, m.schema_version = $sv",
            {"t": now, "fc": files_indexed, "sv": SCHEMA_VERSION},
        )
    except Exception:
        pass

    conn.close()
    db.close()

    return ToolResult.ok(data={
        "file_count": files_indexed,
        "indexed_at": now,
        "schema_version": SCHEMA_VERSION,
    }).to_json()


def index_status(db_path: str) -> str:
    db_file = Path(db_path)
    if not db_file.exists():
        return ToolResult.ok(data={"indexed": False, "schema_version": -1}).to_json()

    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    create_schema(conn)  # ensure schema exists

    res = conn.execute(
        "MATCH (m:IndexMeta {id: 'singleton'}) "
        "RETURN m.last_indexed_at, m.file_count, m.schema_version"
    )

    # Consume result before closing connection/db
    row = res.get_next() if res.has_next() else None
    conn.close()
    db.close()

    if row is not None:
        last_at, fc, sv = row[0], row[1], row[2]
        return ToolResult.ok(data={
            "indexed": bool(last_at),
            "last_indexed_at": last_at,
            "file_count": fc,
            "schema_version": sv,
        }).to_json()

    return ToolResult.ok(data={"indexed": False, "schema_version": SCHEMA_VERSION}).to_json()


def find_symbol(
    name: str,
    db_path: str,
    kind: str | None = None,
    fuzzy: bool = False,
    limit: int = 20,
) -> str:
    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    results = []

    _label_to_kind = {
        "Class": "class",
        "Function": "function",
        "Method": "method",
        "Test": "test",
    }
    _kind_to_label = {v: k for k, v in _label_to_kind.items()}

    search_labels = [_kind_to_label[kind]] if kind and kind in _kind_to_label else list(_label_to_kind.keys())

    for label in search_labels:
        if len(results) >= limit:
            break
        kind_key = _label_to_kind[label]
        if fuzzy:
            cypher = (
                f"MATCH (n:{label}) WHERE lower(n.name) CONTAINS lower($name) "
                "RETURN n.name, n.qualified_name, n.file_path, n.line_start LIMIT $lim"
            )
        else:
            cypher = (
                f"MATCH (n:{label}) WHERE n.name = $name "
                "RETURN n.name, n.qualified_name, n.file_path, n.line_start LIMIT $lim"
            )
        try:
            res = conn.execute(cypher, {"name": name, "lim": limit - len(results)})
            while res.has_next():
                row = res.get_next()
                results.append({
                    "name": row[0],
                    "qualified_name": row[1],
                    "file_path": row[2],
                    "line_start": row[3],
                    "kind": kind_key,
                })
        except Exception:
            pass

    conn.close()
    db.close()
    return ToolResult.ok(data=results[:limit]).to_json()


def get_file_summary(path: str, db_path: str) -> str:
    db = _open_db(db_path)
    conn = kuzu.Connection(db)

    # Check if file node exists in the graph
    file_exists = False
    try:
        res = conn.execute("MATCH (f:File {path: $p}) RETURN count(f)", {"p": path})
        if res.has_next() and res.get_next()[0] > 0:
            file_exists = True
    except Exception:
        pass

    if not file_exists:
        conn.close()
        db.close()
        return ToolResult.error(f"File not indexed: {path}", code="NOT_INDEXED").to_json()

    classes, functions, methods = [], [], []

    try:
        res = conn.execute(
            "MATCH (c:Class {file_path: $p}) RETURN c.name, c.qualified_name, c.line_start, c.line_end",
            {"p": path},
        )
        while res.has_next():
            r = res.get_next()
            classes.append({"name": r[0], "qualified_name": r[1], "line_start": r[2], "line_end": r[3]})
    except Exception:
        pass

    try:
        res = conn.execute(
            "MATCH (f:Function {file_path: $p}) RETURN f.name, f.qualified_name, f.line_start",
            {"p": path},
        )
        while res.has_next():
            r = res.get_next()
            functions.append({"name": r[0], "qualified_name": r[1], "line_start": r[2]})
    except Exception:
        pass

    try:
        res = conn.execute(
            "MATCH (m:Method {file_path: $p}) RETURN m.name, m.qualified_name, m.class_qualified_name, m.line_start",
            {"p": path},
        )
        while res.has_next():
            r = res.get_next()
            methods.append({"name": r[0], "qualified_name": r[1], "class_qn": r[2], "line_start": r[3]})
    except Exception:
        pass

    conn.close()
    db.close()

    return ToolResult.ok(data={"classes": classes, "functions": functions, "methods": methods}).to_json()


def get_callers(qualified_name: str, db_path: str, depth: int = 1) -> str:
    depth = max(1, min(int(depth), 10))  # clamp to [1, 10]
    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    results = []
    for src_label in ("Function", "Method"):
        for tgt_label in ("Function", "Method"):
            try:
                res = conn.execute(
                    f"MATCH (caller:{src_label})-[:CALLS*1..{depth}]->"
                    f"(callee:{tgt_label} {{qualified_name: $qn}}) "
                    "RETURN caller.qualified_name, caller.file_path, caller.line_start",
                    {"qn": qualified_name},
                )
                while res.has_next():
                    r = res.get_next()
                    results.append({
                        "qualified_name": r[0],
                        "file_path": r[1],
                        "line_start": r[2],
                    })
            except Exception:
                pass
    conn.close()
    db.close()
    return ToolResult.ok(data=results).to_json()


def get_callees(qualified_name: str, db_path: str, depth: int = 1) -> str:
    depth = max(1, min(int(depth), 10))  # clamp to [1, 10]
    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    results = []
    for src_label in ("Function", "Method"):
        for tgt_label in ("Function", "Method"):
            try:
                res = conn.execute(
                    f"MATCH (caller:{src_label} {{qualified_name: $qn}})"
                    f"-[:CALLS*1..{depth}]->(callee:{tgt_label}) "
                    "RETURN callee.qualified_name, callee.file_path, callee.line_start",
                    {"qn": qualified_name},
                )
                while res.has_next():
                    r = res.get_next()
                    results.append({
                        "qualified_name": r[0],
                        "file_path": r[1],
                        "line_start": r[2],
                    })
            except Exception:
                pass
    conn.close()
    db.close()
    return ToolResult.ok(data=results).to_json()


def find_path(from_qn: str, to_qn: str, db_path: str, max_depth: int = 5) -> str:
    max_depth = max(1, min(int(max_depth), 10))  # clamp to [1, 10]
    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    path_nodes: list[str] = []

    for src_label in ("Function", "Method"):
        for tgt_label in ("Function", "Method"):
            try:
                res = conn.execute(
                    f"MATCH p = (a:{src_label} {{qualified_name: $from}})"
                    f"-[:CALLS*1..{max_depth}]->(b:{tgt_label} {{qualified_name: $to}}) "
                    "RETURN nodes(p) LIMIT 1",
                    {"from": from_qn, "to": to_qn},
                )
                if res.has_next():
                    row = res.get_next()
                    # nodes(p) returns list; extract qualified_name from each node
                    path_nodes = []
                    for n in row[0]:
                        try:
                            path_nodes.append(n.get("qualified_name", str(n)))
                        except Exception:
                            path_nodes.append(str(n))
                    break
            except Exception:
                pass
        if path_nodes:
            break

    conn.close()
    db.close()

    if path_nodes:
        return ToolResult.ok(data={"path": path_nodes, "length": len(path_nodes)}).to_json()
    return ToolResult.ok(data={"path": [], "message": "No path found"}).to_json()


def find_references(qualified_name: str, db_path: str) -> str:
    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    results = []

    # References via INHERITS (child classes that inherit from this)
    try:
        res = conn.execute(
            "MATCH (a:Class)-[:INHERITS]->(b:Class {qualified_name: $qn}) "
            "RETURN a.qualified_name, a.file_path, a.line_start",
            {"qn": qualified_name},
        )
        while res.has_next():
            r = res.get_next()
            results.append({"kind": "inherits", "qualified_name": r[0], "file_path": r[1], "line_start": r[2]})
    except Exception:
        pass

    # References via CALLS
    for src_label in ("Function", "Method"):
        try:
            res = conn.execute(
                f"MATCH (caller:{src_label})-[:CALLS]->(callee {{qualified_name: $qn}}) "
                "RETURN caller.qualified_name, caller.file_path, caller.line_start",
                {"qn": qualified_name},
            )
            while res.has_next():
                r = res.get_next()
                results.append({"kind": "calls", "qualified_name": r[0], "file_path": r[1], "line_start": r[2]})
        except Exception:
            pass

    conn.close()
    db.close()
    return ToolResult.ok(data=results).to_json()


def get_imports(file_or_module: str, db_path: str) -> str:
    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    results = []
    try:
        res = conn.execute(
            "MATCH (i:Import {file_path: $p}) "
            "RETURN i.from_module, i.imported_name, i.alias, i.line",
            {"p": file_or_module},
        )
        while res.has_next():
            r = res.get_next()
            results.append({
                "from_module": r[0],
                "imported_name": r[1],
                "alias": r[2],
                "line": r[3],
            })
    except Exception:
        pass
    conn.close()
    db.close()
    return ToolResult.ok(data=results).to_json()


def get_test_coverage(qualified_name: str, db_path: str) -> str:
    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    results = []
    for tgt_label in ("Function", "Method"):
        try:
            res = conn.execute(
                f"MATCH (t:Test)-[:TESTS]->(target:{tgt_label} {{qualified_name: $qn}}) "
                "RETURN t.qualified_name, t.file_path, t.line_start, t.framework",
                {"qn": qualified_name},
            )
            while res.has_next():
                r = res.get_next()
                results.append({
                    "test_qn": r[0],
                    "file_path": r[1],
                    "line_start": r[2],
                    "framework": r[3],
                })
        except Exception:
            pass
    conn.close()
    db.close()
    return ToolResult.ok(data=results).to_json()


_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_MAX_ROWS = 100


def query_graph(cypher: str, db_path: str, enabled: bool = False) -> str:
    if not enabled:
        return ToolResult.error(
            "query_graph is disabled. Set enabled=True to allow raw Cypher.",
            code="DISABLED",
        ).to_json()

    if not _LIMIT_RE.search(cypher):
        cypher = cypher.rstrip(";").rstrip() + f" LIMIT {_MAX_ROWS}"

    db = _open_db(db_path)
    conn = kuzu.Connection(db)
    try:
        res = conn.execute(cypher)
        rows = []
        while res.has_next() and len(rows) < _MAX_ROWS:
            rows.append(res.get_next())
        conn.close()
        db.close()
        return ToolResult.ok(data=rows).to_json()
    except Exception as exc:
        conn.close()
        db.close()
        return ToolResult.error(str(exc), code="CYPHER_ERROR").to_json()
