from pathlib import Path
import pytest
from code_graph_mcp.indexer import parse_python_file, ExtractedFile

FIXTURE = Path(__file__).parent / "fixtures" / "python_sample"


def test_parse_finds_classes():
    ef = parse_python_file(FIXTURE / "auth.py")
    class_names = {c["name"] for c in ef.classes}
    assert "AuthManager" in class_names
    assert "TokenValidator" in class_names


def test_parse_finds_methods():
    ef = parse_python_file(FIXTURE / "auth.py")
    method_names = {m["name"] for m in ef.methods}
    assert "hash_password" in method_names
    assert "verify_password" in method_names
    assert "login" in method_names


def test_parse_async_method_flagged():
    ef = parse_python_file(FIXTURE / "auth.py")
    login = next(m for m in ef.methods if m["name"] == "login")
    assert login["is_async"] is True


def test_parse_class_docstring():
    ef = parse_python_file(FIXTURE / "auth.py")
    auth_cls = next(c for c in ef.classes if c["name"] == "AuthManager")
    assert "authentication" in auth_cls["docstring"].lower()


def test_parse_finds_module_functions():
    ef = parse_python_file(FIXTURE / "auth.py")
    func_names = {f["name"] for f in ef.functions}
    assert "create_auth_manager" in func_names


def test_parse_qualified_names():
    ef = parse_python_file(FIXTURE / "auth.py")
    class_qns = {c["qualified_name"] for c in ef.classes}
    assert "auth.AuthManager" in class_qns
    method_qns = {m["qualified_name"] for m in ef.methods}
    assert "auth.AuthManager.hash_password" in method_qns


def test_parse_imports():
    ef = parse_python_file(FIXTURE / "auth.py")
    imported = {i["imported_name"] for i in ef.imports}
    assert "hashlib" in imported


def test_parse_from_imports():
    ef = parse_python_file(FIXTURE / "main.py")
    from_imports = [(i["from_module"], i["imported_name"]) for i in ef.imports]
    assert ("auth", "create_auth_manager") in from_imports
    assert ("auth", "AuthManager") in from_imports


def test_parse_line_numbers_nonzero():
    ef = parse_python_file(FIXTURE / "auth.py")
    cls = next(c for c in ef.classes if c["name"] == "AuthManager")
    assert cls["line_start"] >= 1
    assert cls["line_end"] > cls["line_start"]


def test_parse_detects_test_functions():
    ef = parse_python_file(FIXTURE / "tests" / "test_auth.py")
    test_names = {t["name"] for t in ef.tests}
    assert "test_hash_password" in test_names
    assert "test_verify_password" in test_names


def test_parse_file_hash_and_loc():
    ef = parse_python_file(FIXTURE / "auth.py")
    assert len(ef.hash) == 32  # MD5 hex
    assert ef.loc > 0


import kuzu
from code_graph_mcp.schema import create_schema
from code_graph_mcp.indexer import upsert_file


def test_upsert_creates_file_node(conn, python_sample_path):
    create_schema(conn)
    ef = parse_python_file(python_sample_path / "auth.py")
    upsert_file(conn, ef)
    res = conn.execute("MATCH (f:File {path: $p}) RETURN f.language", {"p": ef.path})
    assert res.has_next()
    assert res.get_next()[0] == "python"


def test_upsert_creates_class_nodes(conn, python_sample_path):
    create_schema(conn)
    ef = parse_python_file(python_sample_path / "auth.py")
    upsert_file(conn, ef)
    res = conn.execute("MATCH (c:Class) WHERE c.file_path = $p RETURN c.name", {"p": ef.path})
    names = set()
    while res.has_next():
        names.add(res.get_next()[0])
    assert "AuthManager" in names


def test_upsert_creates_method_nodes(conn, python_sample_path):
    create_schema(conn)
    ef = parse_python_file(python_sample_path / "auth.py")
    upsert_file(conn, ef)
    res = conn.execute("MATCH (m:Method) WHERE m.file_path = $p RETURN m.name", {"p": ef.path})
    names = set()
    while res.has_next():
        names.add(res.get_next()[0])
    assert "hash_password" in names
    assert "login" in names


def test_upsert_creates_contains_edges(conn, python_sample_path):
    create_schema(conn)
    ef = parse_python_file(python_sample_path / "auth.py")
    upsert_file(conn, ef)
    res = conn.execute(
        "MATCH (f:File)-[:CONTAINS]->(c:Class) WHERE f.path = $p RETURN c.name",
        {"p": ef.path},
    )
    names = set()
    while res.has_next():
        names.add(res.get_next()[0])
    assert "AuthManager" in names


def test_upsert_creates_defines_edges(conn, python_sample_path):
    create_schema(conn)
    ef = parse_python_file(python_sample_path / "auth.py")
    upsert_file(conn, ef)
    res = conn.execute(
        "MATCH (c:Class {name: 'AuthManager'})-[:DEFINES]->(m:Method) RETURN m.name"
    )
    names = set()
    while res.has_next():
        names.add(res.get_next()[0])
    assert "hash_password" in names


def test_upsert_creates_inherits_edges(tmp_path, conn):
    create_schema(conn)
    p = tmp_path / "child.py"
    p.write_text("class Base: pass\nclass Child(Base): pass\n")
    ef = parse_python_file(p)
    upsert_file(conn, ef)
    res = conn.execute("MATCH (a:Class)-[:INHERITS]->(b:Class) RETURN a.qualified_name, b.qualified_name")
    rows = []
    while res.has_next():
        rows.append(tuple(res.get_next()))
    assert ("child.Child", "child.Base") in rows


def test_upsert_idempotent(conn, python_sample_path):
    create_schema(conn)
    ef = parse_python_file(python_sample_path / "auth.py")
    upsert_file(conn, ef)
    upsert_file(conn, ef)  # second upsert must not duplicate
    res = conn.execute("MATCH (c:Class {name: 'AuthManager'}) RETURN count(c)")
    assert res.get_next()[0] == 1


from code_graph_mcp.indexer import parse_typescript_file

TS_FIXTURE = Path(__file__).parent / "fixtures" / "typescript_sample" / "src"


def test_ts_parse_finds_class():
    ef = parse_typescript_file(TS_FIXTURE / "auth.ts")
    names = {c["name"] for c in ef.classes}
    assert "AuthManager" in names


def test_ts_parse_finds_methods():
    ef = parse_typescript_file(TS_FIXTURE / "auth.ts")
    names = {m["name"] for m in ef.methods}
    assert "hashPassword" in names
    assert "login" in names


def test_ts_parse_async_method():
    ef = parse_typescript_file(TS_FIXTURE / "auth.ts")
    login = next(m for m in ef.methods if m["name"] == "login")
    assert login["is_async"] is True


def test_ts_parse_module_function():
    ef = parse_typescript_file(TS_FIXTURE / "auth.ts")
    names = {f["name"] for f in ef.functions}
    assert "createAuthManager" in names
