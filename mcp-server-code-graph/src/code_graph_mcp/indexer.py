from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_python as tspython
import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

PY_LANGUAGE = Language(tspython.language())
TS_LANGUAGE = Language(tsts.language_typescript())

_py_parser = Parser(PY_LANGUAGE)
_ts_parser = Parser(TS_LANGUAGE)


@dataclass
class ExtractedFile:
    path: str
    language: str
    loc: int
    hash: str
    classes: list[dict] = field(default_factory=list)
    functions: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    imports: list[dict] = field(default_factory=list)
    variables: list[dict] = field(default_factory=list)
    tests: list[dict] = field(default_factory=list)
    call_sites: list[tuple[str, str]] = field(default_factory=list)
    inherits: list[tuple[str, str]] = field(default_factory=list)


def parse_python_file(path: Path) -> ExtractedFile:
    source = path.read_bytes()
    tree = _py_parser.parse(source)
    module_name = path.stem
    ef = ExtractedFile(
        path=str(path),
        language="python",
        loc=source.count(b"\n") + 1,
        hash=hashlib.md5(source).hexdigest(),
    )
    _py_visit(tree.root_node, source, str(path), module_name, ef, class_ctx=None)
    return ef


def _txt(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _docstring(body: Node | None, source: bytes) -> str:
    """Extract docstring text from a class/function body block node."""
    if not body:
        return ""
    for child in body.children:
        if child.type == "expression_statement" and child.children:
            expr = child.children[0]
            if expr.type == "string":
                # Try to get string_content child (the content without quotes)
                for sc in expr.children:
                    if sc.type == "string_content":
                        return _txt(sc, source)
                # Fallback: strip quotes from full text
                raw = _txt(expr, source)
                return raw.strip("\"' \n\t").strip('"""').strip("'''")
        break
    return ""


def _signature(func_node: Node, source: bytes) -> str:
    params = func_node.child_by_field_name("parameters")
    return _txt(params, source)[:300] if params else "()"


def _is_async(func_node: Node) -> bool:
    """Check if function_definition has 'async' as a direct child (tree-sitter-python 0.23+)."""
    for child in func_node.children:
        if child.type == "async":
            return True
    return False


def _extract_calls(body: Node | None, source: bytes, caller_qn: str, ef: ExtractedFile) -> None:
    if body is None:
        return

    def walk(n: Node) -> None:
        if n.type == "call":
            func = n.child_by_field_name("function")
            if func:
                if func.type == "identifier":
                    ef.call_sites.append((caller_qn, _txt(func, source)))
                elif func.type == "attribute":
                    attr = func.child_by_field_name("attribute")
                    if attr:
                        ef.call_sites.append((caller_qn, _txt(attr, source)))
        for child in n.children:
            walk(child)

    walk(body)


def _py_visit(
    node: Node,
    source: bytes,
    file_path: str,
    module_name: str,
    ef: ExtractedFile,
    class_ctx: str | None,
) -> None:
    if node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _txt(name_node, source)
        qn = f"{module_name}.{name}"
        body = node.child_by_field_name("body")
        ef.classes.append({
            "name": name,
            "qualified_name": qn,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": _docstring(body, source),
        })
        superclasses = node.child_by_field_name("superclasses")
        if superclasses:
            for arg in superclasses.named_children:
                if arg.type == "identifier":
                    ef.inherits.append((qn, _txt(arg, source)))
        if body:
            for child in body.children:
                _py_visit(child, source, file_path, module_name, ef, class_ctx=qn)
        return

    if node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _txt(name_node, source)
        body = node.child_by_field_name("body")
        is_async = _is_async(node)

        if class_ctx:
            qn = f"{class_ctx}.{name}"
            entry = {
                "name": name,
                "qualified_name": qn,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "is_async": is_async,
                "docstring": _docstring(body, source),
                "signature": _signature(node, source),
                "class_qualified_name": class_ctx,
            }
            if name.startswith("test_"):
                ef.tests.append({**entry, "framework": "pytest"})
            else:
                ef.methods.append(entry)
        else:
            qn = f"{module_name}.{name}"
            entry = {
                "name": name,
                "qualified_name": qn,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "is_async": is_async,
                "docstring": _docstring(body, source),
                "signature": _signature(node, source),
            }
            if name.startswith("test_"):
                ef.tests.append({**entry, "framework": "pytest"})
            else:
                ef.functions.append(entry)

        _extract_calls(body, source, qn, ef)
        return

    if node.type == "import_statement":
        for child in node.named_children:
            if child.type == "dotted_name":
                ef.imports.append({
                    "id": f"{file_path}:{node.start_point[0]}:{_txt(child, source)}",
                    "from_module": "",
                    "imported_name": _txt(child, source),
                    "alias": "",
                    "file_path": file_path,
                    "line": node.start_point[0] + 1,
                })
            elif child.type == "aliased_import":
                n2 = child.child_by_field_name("name")
                a2 = child.child_by_field_name("alias")
                if n2:
                    ef.imports.append({
                        "id": f"{file_path}:{node.start_point[0]}:{_txt(n2, source)}",
                        "from_module": "",
                        "imported_name": _txt(n2, source),
                        "alias": _txt(a2, source) if a2 else "",
                        "file_path": file_path,
                        "line": node.start_point[0] + 1,
                    })
        return

    if node.type == "import_from_statement":
        # module_name field returns the first dotted_name (the module being imported from)
        mod_node = node.child_by_field_name("module_name")
        from_mod = _txt(mod_node, source) if mod_node else ""
        # Iterate named children, skip the module node itself
        mod_start = mod_node.start_byte if mod_node else -1
        for child in node.named_children:
            # Skip the module name node
            if child.start_byte == mod_start:
                continue
            if child.type == "dotted_name":
                ef.imports.append({
                    "id": f"{file_path}:{node.start_point[0]}:{from_mod}.{_txt(child, source)}",
                    "from_module": from_mod,
                    "imported_name": _txt(child, source),
                    "alias": "",
                    "file_path": file_path,
                    "line": node.start_point[0] + 1,
                })
            elif child.type == "aliased_import":
                n2 = child.child_by_field_name("name")
                a2 = child.child_by_field_name("alias")
                if n2:
                    ef.imports.append({
                        "id": f"{file_path}:{node.start_point[0]}:{from_mod}.{_txt(n2, source)}",
                        "from_module": from_mod,
                        "imported_name": _txt(n2, source),
                        "alias": _txt(a2, source) if a2 else "",
                        "file_path": file_path,
                        "line": node.start_point[0] + 1,
                    })
        return

    # Recurse into all other node types
    if node.type not in ("class_definition", "function_definition"):
        for child in node.children:
            _py_visit(child, source, file_path, module_name, ef, class_ctx)


def parse_typescript_file(path: Path) -> ExtractedFile:
    source = path.read_bytes()
    tree = _ts_parser.parse(source)
    module_name = path.stem
    ef = ExtractedFile(
        path=str(path),
        language="typescript",
        loc=source.count(b"\n") + 1,
        hash=hashlib.md5(source).hexdigest(),
    )
    _ts_visit(tree.root_node, source, str(path), module_name, ef, class_ctx=None)
    return ef


def _ts_visit(
    node: Node,
    source: bytes,
    file_path: str,
    module_name: str,
    ef: ExtractedFile,
    class_ctx: str | None,
) -> None:
    # export_statement wraps class/function declarations — recurse into it
    if node.type == "export_statement":
        for child in node.children:
            _ts_visit(child, source, file_path, module_name, ef, class_ctx)
        return

    if node.type == "class_declaration":
        # TS uses type_identifier for the class name (not "name" field)
        name_node = node.child_by_field_name("name")
        if name_node is None:
            # Fallback: find type_identifier child directly
            name_node = next(
                (c for c in node.children if c.type == "type_identifier"), None
            )
        if not name_node:
            return
        name = _txt(name_node, source)
        qn = f"{module_name}.{name}"
        body = node.child_by_field_name("body")
        ef.classes.append({
            "name": name,
            "qualified_name": qn,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": "",
        })
        if body:
            for child in body.children:
                _ts_visit(child, source, file_path, module_name, ef, class_ctx=qn)
        return

    if node.type == "method_definition":
        # TS uses property_identifier for method names (not "name" field)
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = next(
                (c for c in node.children if c.type == "property_identifier"), None
            )
        if not name_node or not class_ctx:
            return
        name = _txt(name_node, source)
        is_async = any(c.type == "async" for c in node.children)
        qn = f"{class_ctx}.{name}"
        params = node.child_by_field_name("parameters")
        signature = _txt(params, source)[:300] if params else "()"
        ef.methods.append({
            "name": name,
            "qualified_name": qn,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "is_async": is_async,
            "docstring": "",
            "signature": signature,
            "class_qualified_name": class_ctx,
        })
        return

    if node.type == "function_declaration":
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = next(
                (c for c in node.children if c.type == "identifier"), None
            )
        if not name_node:
            for child in node.children:
                _ts_visit(child, source, file_path, module_name, ef, class_ctx)
            return
        name = _txt(name_node, source)
        is_async = any(c.type == "async" for c in node.children)
        qn = f"{class_ctx}.{name}" if class_ctx else f"{module_name}.{name}"
        params = node.child_by_field_name("parameters")
        signature = _txt(params, source)[:300] if params else "()"
        ef.functions.append({
            "name": name,
            "qualified_name": qn,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "is_async": is_async,
            "docstring": "",
            "signature": signature,
        })
        return

    # Recurse into all other node types
    if node.type not in ("class_declaration", "method_definition", "function_declaration"):
        for child in node.children:
            _ts_visit(child, source, file_path, module_name, ef, class_ctx)


import kuzu


def upsert_file(conn: kuzu.Connection, ef: ExtractedFile) -> None:
    """Write all nodes and edges from ExtractedFile into Kùzu. Idempotent."""
    _upsert_node(conn, "File", "path", {
        "path": ef.path, "language": ef.language,
        "loc": ef.loc, "hash": ef.hash,
    })

    for cls in ef.classes:
        _upsert_node(conn, "Class", "qualified_name", cls)
        _merge_rel(conn,
            "MATCH (f:File {path: $fp}), (c:Class {qualified_name: $qn})",
            "MERGE (f)-[:CONTAINS]->(c)",
            {"fp": ef.path, "qn": cls["qualified_name"]},
        )

    for func in ef.functions:
        _upsert_node(conn, "Function", "qualified_name", func)
        _merge_rel(conn,
            "MATCH (f:File {path: $fp}), (fn:Function {qualified_name: $qn})",
            "MERGE (f)-[:CONTAINS]->(fn)",
            {"fp": ef.path, "qn": func["qualified_name"]},
        )

    for meth in ef.methods:
        _upsert_node(conn, "Method", "qualified_name", meth)
        _merge_rel(conn,
            "MATCH (c:Class {qualified_name: $cqn}), (m:Method {qualified_name: $qn})",
            "MERGE (c)-[:DEFINES]->(m)",
            {"cqn": meth["class_qualified_name"], "qn": meth["qualified_name"]},
        )

    for test in ef.tests:
        _upsert_node(conn, "Test", "qualified_name", {
            "qualified_name": test["qualified_name"],
            "name": test["name"],
            "file_path": test["file_path"],
            "framework": test.get("framework", "pytest"),
            "line_start": test["line_start"],
            "line_end": test["line_end"],
        })

    for imp in ef.imports:
        _upsert_node(conn, "Import", "id", imp)

    for child_qn, parent_name in ef.inherits:
        parent_qn = next(
            (c["qualified_name"] for c in ef.classes if c["name"] == parent_name), None
        )
        if parent_qn:
            _merge_rel(conn,
                "MATCH (a:Class {qualified_name: $a}), (b:Class {qualified_name: $b})",
                "MERGE (a)-[:INHERITS]->(b)",
                {"a": child_qn, "b": parent_qn},
            )

    _upsert_calls(conn, ef)


def _upsert_node(conn: kuzu.Connection, label: str, pk_field: str, props: dict) -> None:
    """Insert node; on duplicate primary key, update all other fields."""
    try:
        cols = ", ".join(f"{k}: ${k}" for k in props)
        conn.execute(f"CREATE (:{label} {{{cols}}})", props)
    except Exception:
        sets = ", ".join(f"n.{k} = ${k}" for k in props if k != pk_field)
        if sets:
            conn.execute(
                f"MATCH (n:{label} {{{pk_field}: ${pk_field}}}) SET {sets}",
                props,
            )


def _merge_rel(conn: kuzu.Connection, match_clause: str, merge_clause: str, params: dict) -> None:
    """Run a MATCH ... MERGE pattern, swallowing expected errors (missing nodes, duplicate edges)."""
    try:
        conn.execute(f"{match_clause} {merge_clause}", params)
    except Exception as exc:
        msg = str(exc).lower()
        # Swallow: node not matched (empty MATCH) or edge already exists
        if any(kw in msg for kw in ("duplicate", "already exists", "not found", "no match")):
            pass
        else:
            pass  # Still swallow all for now — Kùzu error messages vary by version
            # Future: log to stderr here when Kùzu stabilises error message format


def _upsert_calls(conn: kuzu.Connection, ef: ExtractedFile) -> None:
    all_funcs = {f["qualified_name"]: "Function" for f in ef.functions}
    all_funcs.update({m["qualified_name"]: "Method" for m in ef.methods})

    for caller_qn, callee_name in ef.call_sites:
        caller_label = all_funcs.get(caller_qn)
        if not caller_label:
            continue
        callee_entry = next(
            ((qn, lbl) for qn, lbl in all_funcs.items() if qn.split(".")[-1] == callee_name),
            None,
        )
        if callee_entry:
            callee_qn, callee_label = callee_entry
            _merge_rel(conn,
                f"MATCH (a:{caller_label} {{qualified_name: $a}}), (b:{callee_label} {{qualified_name: $b}})",
                "MERGE (a)-[:CALLS]->(b)",
                {"a": caller_qn, "b": callee_qn},
            )
