import kuzu
from code_graph_mcp.schema import create_schema, SCHEMA_VERSION, get_stored_version


def test_create_schema_creates_all_node_tables(conn):
    create_schema(conn)
    result = conn.execute("CALL show_tables() RETURN name")
    tables = set()
    while result.has_next():
        tables.add(result.get_next()[0])
    for expected in ("File", "Class", "Function", "Method", "Variable", "Import", "Test", "Module", "IndexMeta"):
        assert expected in tables, f"{expected} table missing"


def test_create_schema_creates_all_rel_tables(conn):
    create_schema(conn)
    result = conn.execute("CALL show_tables() RETURN name, type")
    rel_tables = set()
    while result.has_next():
        row = result.get_next()
        if row[1] == "REL":
            rel_tables.add(row[0])
    for expected in ("CONTAINS", "DEFINES", "CALLS", "IMPORTS", "INHERITS", "REFERENCES", "TESTS", "DECORATES"):
        assert expected in rel_tables, f"{expected} rel table missing"


def test_create_schema_is_idempotent(conn):
    create_schema(conn)
    create_schema(conn)  # must not raise


def test_schema_version_stored_and_retrieved(conn):
    create_schema(conn)
    assert get_stored_version(conn) == SCHEMA_VERSION


def test_schema_version_mismatch_detected(conn):
    create_schema(conn)
    conn.execute("MATCH (m:IndexMeta) SET m.schema_version = 99")
    assert get_stored_version(conn) != SCHEMA_VERSION
