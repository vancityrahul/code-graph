import pytest
import kuzu
from pathlib import Path


@pytest.fixture
def db(tmp_path):
    """Fresh Kùzu database in a temp directory."""
    db = kuzu.Database(str(tmp_path / "graph.kuzu"))
    yield db
    db.close()


@pytest.fixture
def conn(db):
    """Connection to the fresh db."""
    c = kuzu.Connection(db)
    yield c
    c.close()


@pytest.fixture
def python_sample_path():
    return Path(__file__).parent / "fixtures" / "python_sample"
