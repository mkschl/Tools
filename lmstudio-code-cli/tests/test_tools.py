import subprocess
from pathlib import Path
from unittest.mock import patch

from lmstudio_code_cli.tools import execute_tool


# ── read_file ──────────────────────────────────────────────────────────────────

def test_read_file_returns_numbered_lines(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("line1\nline2\nline3\n")
    result = execute_tool("read_file", {"path": str(f)}, str(tmp_path))
    assert "1\tline1\n" in result
    assert "2\tline2\n" in result
    assert "3\tline3\n" in result


def test_read_file_not_found(tmp_path):
    result = execute_tool("read_file", {"path": "missing.txt"}, str(tmp_path))
    assert "not found" in result.lower()


def test_read_file_on_directory_returns_hint(tmp_path):
    result = execute_tool("read_file", {"path": str(tmp_path)}, str(tmp_path))
    assert "directory" in result.lower()


def test_read_file_offset_and_limit(tmp_path):
    f = tmp_path / "ten.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
    result = execute_tool("read_file", {"path": str(f), "offset": 3, "limit": 2}, str(tmp_path))
    assert "3\tline3\n" in result
    assert "4\tline4\n" in result
    assert "line1" not in result
    assert "line5" not in result


def test_read_file_shows_more_lines_hint(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"L{i}" for i in range(10)) + "\n")
    result = execute_tool("read_file", {"path": str(f), "limit": 5}, str(tmp_path))
    assert "more lines" in result


def test_read_file_relative_path_resolved(tmp_path):
    f = tmp_path / "rel.txt"
    f.write_text("content\n")
    result = execute_tool("read_file", {"path": "rel.txt"}, str(tmp_path))
    assert "content" in result


# ── write_file ─────────────────────────────────────────────────────────────────

def test_write_file_creates_file(tmp_path):
    result = execute_tool("write_file", {"path": "out.txt", "content": "hello"}, str(tmp_path))
    assert "Wrote" in result
    assert (tmp_path / "out.txt").read_text() == "hello"


def test_write_file_creates_parent_directories(tmp_path):
    path = str(tmp_path / "nested" / "deep" / "file.txt")
    execute_tool("write_file", {"path": path, "content": "data"}, str(tmp_path))
    assert Path(path).read_text() == "data"


def test_write_file_overwrites_existing(tmp_path):
    f = tmp_path / "overwrite.txt"
    f.write_text("old")
    execute_tool("write_file", {"path": str(f), "content": "new"}, str(tmp_path))
    assert f.read_text() == "new"


# ── edit_file ──────────────────────────────────────────────────────────────────

def test_edit_file_replaces_unique_string(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def foo():\n    pass\n")
    execute_tool(
        "edit_file",
        {"path": str(f), "old_string": "    pass\n", "new_string": "    return 42\n"},
        str(tmp_path),
    )
    assert f.read_text() == "def foo():\n    return 42\n"


def test_edit_file_not_found(tmp_path):
    result = execute_tool(
        "edit_file", {"path": "missing.py", "old_string": "x", "new_string": "y"}, str(tmp_path)
    )
    assert "not found" in result.lower()


def test_edit_file_no_match(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("hello world")
    result = execute_tool(
        "edit_file", {"path": str(f), "old_string": "nothere", "new_string": ""}, str(tmp_path)
    )
    assert "not found" in result.lower()


def test_edit_file_multiple_matches_returns_error(tmp_path):
    f = tmp_path / "dup.py"
    f.write_text("x = 1\nx = 1\n")
    result = execute_tool(
        "edit_file", {"path": str(f), "old_string": "x = 1", "new_string": "x = 2"}, str(tmp_path)
    )
    assert "2" in result and ("locations" in result or "matches" in result)


# ── run_bash ───────────────────────────────────────────────────────────────────

def test_run_bash_returns_stdout(tmp_path):
    result = execute_tool("run_bash", {"command": "echo hello"}, str(tmp_path))
    assert "hello" in result


def test_run_bash_nonzero_exit_code_reported(tmp_path):
    result = execute_tool("run_bash", {"command": "exit 1"}, str(tmp_path))
    assert "exit code 1" in result.lower()


def test_run_bash_stderr_reported(tmp_path):
    result = execute_tool("run_bash", {"command": "echo errline >&2"}, str(tmp_path))
    assert "errline" in result
    assert "[stderr]" in result


def test_run_bash_timeout_returns_message(tmp_path):
    with patch("lmstudio_code_cli.tools.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("sleep", 1)
        result = execute_tool("run_bash", {"command": "sleep 10", "timeout": 1}, str(tmp_path))
    assert "timed out" in result.lower()


# ── list_directory ─────────────────────────────────────────────────────────────

def test_list_directory_shows_dirs_and_files(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("x")
    result = execute_tool("list_directory", {"path": str(tmp_path)}, str(tmp_path))
    assert "sub/" in result
    assert "file.txt" in result


def test_list_directory_not_found(tmp_path):
    result = execute_tool(
        "list_directory", {"path": str(tmp_path / "missing")}, str(tmp_path)
    )
    assert "not found" in result.lower()


def test_list_directory_not_a_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    result = execute_tool("list_directory", {"path": str(f)}, str(tmp_path))
    assert "not a directory" in result.lower()


def test_list_directory_empty(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = execute_tool("list_directory", {"path": str(empty)}, str(tmp_path))
    assert "empty" in result.lower()


def test_list_directory_shows_byte_sizes(tmp_path):
    (tmp_path / "data.bin").write_bytes(b"A" * 100)
    result = execute_tool("list_directory", {"path": str(tmp_path)}, str(tmp_path))
    assert "100" in result


# ── search_files ───────────────────────────────────────────────────────────────

def test_search_files_finds_pattern(tmp_path):
    (tmp_path / "src.py").write_text("def my_function():\n    pass\n")
    result = execute_tool(
        "search_files", {"pattern": "my_function", "path": str(tmp_path)}, str(tmp_path)
    )
    assert "my_function" in result


def test_search_files_no_matches(tmp_path):
    (tmp_path / "src.py").write_text("hello")
    result = execute_tool(
        "search_files", {"pattern": "xyznothere", "path": str(tmp_path)}, str(tmp_path)
    )
    assert "no matches" in result


def test_search_files_case_insensitive(tmp_path):
    (tmp_path / "src.py").write_text("Hello World\n")
    result = execute_tool(
        "search_files",
        {"pattern": "hello", "path": str(tmp_path), "case_sensitive": False},
        str(tmp_path),
    )
    assert "Hello" in result


def test_search_files_file_pattern_filter(tmp_path):
    (tmp_path / "a.py").write_text("needle\n")
    (tmp_path / "b.txt").write_text("needle\n")
    result = execute_tool(
        "search_files",
        {"pattern": "needle", "path": str(tmp_path), "file_pattern": "*.py"},
        str(tmp_path),
    )
    assert "a.py" in result
    assert "b.txt" not in result


# ── glob_files ─────────────────────────────────────────────────────────────────

def test_glob_files_finds_matches(tmp_path):
    (tmp_path / "a.py").touch()
    (tmp_path / "b.py").touch()
    (tmp_path / "c.txt").touch()
    result = execute_tool("glob_files", {"pattern": "*.py", "path": str(tmp_path)}, str(tmp_path))
    assert "a.py" in result
    assert "b.py" in result
    assert "c.txt" not in result


def test_glob_files_no_matches(tmp_path):
    result = execute_tool("glob_files", {"pattern": "*.rs", "path": str(tmp_path)}, str(tmp_path))
    assert "no files" in result


def test_glob_files_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.py").touch()
    result = execute_tool("glob_files", {"pattern": "**/*.py", "path": str(tmp_path)}, str(tmp_path))
    assert "deep.py" in result


# ── unknown tool ───────────────────────────────────────────────────────────────

def test_execute_tool_unknown_name_returns_error(tmp_path):
    result = execute_tool("does_not_exist", {}, str(tmp_path))
    assert "unknown" in result.lower()
