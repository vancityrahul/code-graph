import json
from pathlib import Path
import pytest
import kuzu
from code_graph_mcp.query import index_repo, index_status
from code_graph_mcp.query import find_symbol, get_file_summary

FIXTURE = Path(__file__).parent / "fixtures" / "python_sample"


def test_index_repo_returns_ok(tmp_path):
    result = index_repo(str(FIXTURE), db_path=str(tmp_path / "graph.kuzu"))
    d = json.loads(result)
    assert d["ok"] is True
    assert d["data"]["file_count"] > 0


def test_index_repo_indexes_python_files(tmp_path):
    result = index_repo(str(FIXTURE), db_path=str(tmp_path / "graph.kuzu"))
    d = json.loads(result)
    assert d["data"]["file_count"] >= 2  # auth.py + main.py


def test_index_repo_force_rebuilds(tmp_path):
    db_path = str(tmp_path / "graph.kuzu")
    index_repo(str(FIXTURE), db_path=db_path)
    result = index_repo(str(FIXTURE), db_path=db_path, force=True)
    d = json.loads(result)
    assert d["ok"] is True


def test_index_status_before_index(tmp_path):
    result = index_status(db_path=str(tmp_path / "graph.kuzu"))
    d = json.loads(result)
    assert d["ok"] is True
    assert d["data"]["indexed"] is False


def test_index_status_after_index(tmp_path):
    db_path = str(tmp_path / "graph.kuzu")
    index_repo(str(FIXTURE), db_path=db_path)
    result = index_status(db_path=db_path)
    d = json.loads(result)
    assert d["data"]["indexed"] is True
    assert d["data"]["file_count"] >= 2
    assert d["data"]["schema_version"] == 1


@pytest.fixture
def indexed_db(tmp_path):
    db_path = str(tmp_path / "graph.kuzu")
    index_repo(str(FIXTURE.resolve()), db_path=db_path)
    return db_path


def test_find_symbol_by_name(indexed_db):
    result = json.loads(find_symbol("AuthManager", db_path=indexed_db))
    assert result["ok"] is True
    assert len(result["data"]) >= 1
    assert result["data"][0]["name"] == "AuthManager"


def test_find_symbol_by_kind_class(indexed_db):
    result = json.loads(find_symbol("AuthManager", kind="class", db_path=indexed_db))
    assert result["ok"] is True
    hits = result["data"]
    assert all(h["kind"] == "class" for h in hits)


def test_find_symbol_fuzzy(indexed_db):
    result = json.loads(find_symbol("authmanager", fuzzy=True, db_path=indexed_db))
    assert result["ok"] is True
    assert len(result["data"]) >= 1


def test_find_symbol_limit(indexed_db):
    result = json.loads(find_symbol("a", db_path=indexed_db, limit=2))
    assert len(result["data"]) <= 2


def test_get_file_summary_returns_classes_and_functions(indexed_db):
    auth_path = str((FIXTURE / "auth.py").resolve())
    result = json.loads(get_file_summary(auth_path, db_path=indexed_db))
    assert result["ok"] is True
    d = result["data"]
    assert "classes" in d
    assert "functions" in d
    class_names = [c["name"] for c in d["classes"]]
    assert "AuthManager" in class_names


def test_find_symbol_not_found_returns_empty(indexed_db):
    result = json.loads(find_symbol("zzz_nonexistent_xyz", db_path=indexed_db))
    assert result["ok"] is True
    assert result["data"] == []


from code_graph_mcp.query import get_callers, get_callees, find_path


def test_get_callees_returns_list(indexed_db):
    result = json.loads(get_callees("main.run", db_path=indexed_db))
    assert result["ok"] is True
    assert isinstance(result["data"], list)


def test_get_callers_returns_list(indexed_db):
    result = json.loads(get_callers("auth.AuthManager.hash_password", db_path=indexed_db))
    assert result["ok"] is True
    assert isinstance(result["data"], list)


def test_find_path_returns_ok(indexed_db):
    result = json.loads(find_path(
        "main.run", "auth.AuthManager.hash_password", db_path=indexed_db
    ))
    assert result["ok"] is True
    assert "data" in result


from code_graph_mcp.query import find_references, get_imports, get_test_coverage


def test_get_imports_for_file(indexed_db):
    main_path = str((FIXTURE / "main.py").resolve())
    result = json.loads(get_imports(main_path, db_path=indexed_db))
    assert result["ok"] is True
    imported_names = [i["imported_name"] for i in result["data"]]
    assert "create_auth_manager" in imported_names


def test_find_references_returns_list(indexed_db):
    result = json.loads(find_references("auth.AuthManager", db_path=indexed_db))
    assert result["ok"] is True
    assert isinstance(result["data"], list)


def test_get_test_coverage_returns_list(indexed_db):
    result = json.loads(get_test_coverage("auth.AuthManager.hash_password", db_path=indexed_db))
    assert result["ok"] is True
    assert isinstance(result["data"], list)


from code_graph_mcp.query import query_graph


def test_query_graph_blocked_by_default(indexed_db):
    result = json.loads(query_graph(
        "MATCH (n:Class) RETURN n.name LIMIT 1",
        db_path=indexed_db,
        enabled=False,
    ))
    assert result["ok"] is False
    assert result["error_code"] == "DISABLED"


def test_query_graph_runs_when_enabled(indexed_db):
    result = json.loads(query_graph(
        "MATCH (n:Class) RETURN n.name LIMIT 5",
        db_path=indexed_db,
        enabled=True,
    ))
    assert result["ok"] is True
    assert isinstance(result["data"], list)


def test_query_graph_enforces_limit(indexed_db):
    result = json.loads(query_graph(
        "MATCH (n:Class) RETURN n.name",
        db_path=indexed_db,
        enabled=True,
    ))
    assert result["ok"] is True
    assert len(result["data"]) <= 100
