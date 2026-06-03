import pytest
import tempfile
from pathlib import Path
from investigator.tools.first_party import read_file, grep, list_dir


@pytest.fixture
def tmp_repo(tmp_path):
    (tmp_path / "main.py").write_text("\n".join(f"line {i}" for i in range(1, 2100)))
    (tmp_path / "utils.py").write_text("def helper():\n    pass\n")
    subdir = tmp_path / "pkg"
    subdir.mkdir()
    (subdir / "auth.py").write_text("def authenticate(token):\n    return True\n")
    return tmp_path


def test_read_file_returns_content(tmp_repo):
    result = read_file(tmp_repo / "utils.py")
    assert result.ok
    assert "def helper" in result.data


def test_read_file_includes_line_numbers(tmp_repo):
    result = read_file(tmp_repo / "utils.py")
    assert "1:" in result.data or "1 " in result.data


def test_read_file_caps_at_2000_lines(tmp_repo):
    result = read_file(tmp_repo / "main.py")
    assert result.ok
    assert result.meta["truncated"] is True
    assert result.data.count("\n") <= 2001


def test_read_file_not_found():
    result = read_file(Path("/nonexistent/file.py"))
    assert result.ok is False
    assert result.error_code == "FILE_NOT_FOUND"


def test_grep_finds_pattern(tmp_repo):
    result = grep("authenticate", str(tmp_repo))
    assert result.ok
    assert "auth.py" in result.data
    assert "authenticate" in result.data


def test_grep_caps_at_100_matches(tmp_repo):
    # create a file with 200 matching lines
    big = tmp_repo / "big.py"
    big.write_text("\n".join(["match_me()" for _ in range(200)]))
    result = grep("match_me", str(tmp_repo))
    assert result.ok
    assert result.meta["truncated"] is True


def test_grep_no_matches(tmp_repo):
    result = grep("zzz_no_such_thing", str(tmp_repo))
    assert result.ok
    assert result.data == "" or "no matches" in result.data.lower()


def test_list_dir_returns_tree(tmp_repo):
    result = list_dir(tmp_repo)
    assert result.ok
    assert "utils.py" in result.data
    assert "main.py" in result.data


def test_list_dir_not_found():
    result = list_dir(Path("/nonexistent/dir"))
    assert result.ok is False
    assert result.error_code == "DIR_NOT_FOUND"
