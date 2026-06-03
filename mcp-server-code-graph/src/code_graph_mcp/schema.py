import kuzu

SCHEMA_VERSION = 1

_NODE_DDL = [
    """CREATE NODE TABLE IF NOT EXISTS File(
        path STRING, language STRING, loc INT64, hash STRING,
        PRIMARY KEY(path)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Module(
        name STRING, file_path STRING,
        PRIMARY KEY(name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Class(
        qualified_name STRING, name STRING, file_path STRING,
        line_start INT64, line_end INT64, docstring STRING,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Function(
        qualified_name STRING, name STRING, file_path STRING,
        line_start INT64, line_end INT64, signature STRING,
        docstring STRING, is_async BOOLEAN,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Method(
        qualified_name STRING, name STRING, file_path STRING,
        line_start INT64, line_end INT64, signature STRING,
        docstring STRING, is_async BOOLEAN, class_qualified_name STRING,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Variable(
        qualified_name STRING, name STRING, file_path STRING,
        line INT64, is_module_level BOOLEAN,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Import(
        id STRING, from_module STRING, imported_name STRING,
        alias STRING, file_path STRING, line INT64,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Test(
        qualified_name STRING, name STRING, file_path STRING,
        framework STRING, line_start INT64, line_end INT64,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS IndexMeta(
        id STRING, schema_version INT64, last_indexed_at STRING,
        file_count INT64, node_count INT64, edge_count INT64,
        PRIMARY KEY(id)
    )""",
]

_REL_DDL = [
    "CREATE REL TABLE IF NOT EXISTS CONTAINS(FROM File TO Class, FROM File TO Function, FROM File TO Variable)",
    "CREATE REL TABLE IF NOT EXISTS DEFINES(FROM Class TO Method)",
    """CREATE REL TABLE IF NOT EXISTS CALLS(
        FROM Function TO Function,
        FROM Function TO Method,
        FROM Method TO Function,
        FROM Method TO Method
    )""",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM File TO Module)",
    "CREATE REL TABLE IF NOT EXISTS INHERITS(FROM Class TO Class)",
    "CREATE REL TABLE IF NOT EXISTS REFERENCES(FROM Function TO Variable, FROM Method TO Variable)",
    "CREATE REL TABLE IF NOT EXISTS TESTS(FROM Test TO Function, FROM Test TO Method)",
    "CREATE REL TABLE IF NOT EXISTS DECORATES(FROM Function TO Function, FROM Method TO Function)",
]


def create_schema(conn: kuzu.Connection) -> None:
    for stmt in _NODE_DDL:
        conn.execute(stmt)
    for stmt in _REL_DDL:
        conn.execute(stmt)
    conn.execute(
        "MERGE (m:IndexMeta {id: 'singleton'}) "
        "ON CREATE SET m.schema_version = $v, m.last_indexed_at = '', "
        "m.file_count = 0, m.node_count = 0, m.edge_count = 0",
        {"v": SCHEMA_VERSION},
    )


def get_stored_version(conn: kuzu.Connection) -> int:
    res = conn.execute("MATCH (m:IndexMeta {id: 'singleton'}) RETURN m.schema_version")
    if res.has_next():
        return res.get_next()[0]
    return -1
