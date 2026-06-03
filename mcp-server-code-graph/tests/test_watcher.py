import time
import shutil
from pathlib import Path
import pytest
import json
from code_graph_mcp.query import index_repo, find_symbol
from code_graph_mcp.watcher import RepoWatcher

FIXTURE = Path(__file__).parent / "fixtures" / "python_sample"


@pytest.fixture
def watched_repo(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(str(FIXTURE), str(repo))
    db_path = str(tmp_path / "graph.kuzu")
    index_repo(str(repo), db_path=db_path)
    watcher = RepoWatcher(repo_path=str(repo), db_path=db_path)
    watcher.start()
    yield repo, db_path, watcher
    watcher.stop()


def test_watcher_detects_new_function(watched_repo):
    repo, db_path, watcher = watched_repo
    auth = repo / "auth.py"
    original = auth.read_text()
    auth.write_text(original + "\ndef brand_new_function(): pass\n")
    time.sleep(2.0)  # wait for watchdog event + re-index
    result = json.loads(find_symbol("brand_new_function", db_path=db_path))
    assert result["ok"] is True
    assert len(result["data"]) >= 1


def test_watcher_can_be_stopped(watched_repo):
    _, _, watcher = watched_repo
    watcher.stop()
    assert not watcher.is_alive()
