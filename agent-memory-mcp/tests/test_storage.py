import importlib
from datetime import date, timedelta

import pytest

import agent_memory_mcp.storage as storage


@pytest.fixture(autouse=True)
def memory_root(tmp_path, monkeypatch):
    """Redirect all storage I/O to tmp_path instead of ~/.agent-memory/."""
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path)
    return tmp_path


# ── AGENT_MEMORY_DIR validation ────────────────────────────────────────────────

def test_agent_memory_dir_missing_raises_at_import(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path / "nonexistent"))
    with pytest.raises(RuntimeError, match="does not exist"):
        importlib.reload(storage)


def test_agent_memory_dir_valid_sets_storage_root(tmp_path, monkeypatch):
    custom = tmp_path / "custom-memory"
    custom.mkdir()
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(custom))
    importlib.reload(storage)
    assert storage.STORAGE_ROOT == custom


def test_agent_memory_dir_not_writable_raises_at_import(tmp_path, monkeypatch):
    read_only = tmp_path / "read-only"
    read_only.mkdir(mode=0o555)
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(read_only))
    try:
        with pytest.raises(RuntimeError, match="not writable"):
            importlib.reload(storage)
    finally:
        read_only.chmod(0o755)


# ── decision_log ───────────────────────────────────────────────────────────────

def test_decision_log_creates_dated_file(tmp_path):
    storage.decision_log("myproject", "Use SQLite", "Simpler and sufficient")
    path = tmp_path / "decisions" / "myproject" / f"{date.today().isoformat()}.md"
    assert path.exists()
    content = path.read_text()
    assert "Use SQLite" in content
    assert "Simpler and sufficient" in content


def test_decision_log_header_written_once_for_new_file(tmp_path):
    storage.decision_log("p1", "D1", "R1")
    storage.decision_log("p1", "D2", "R2")
    path = tmp_path / "decisions" / "p1" / f"{date.today().isoformat()}.md"
    content = path.read_text()
    # The header line "p1 — YYYY-MM-DD" appears exactly once
    assert content.count(f"p1 — {date.today().isoformat()}") == 1
    assert "D1" in content
    assert "D2" in content


def test_decision_log_appends_to_existing_file(tmp_path):
    for summary in ("First", "Second", "Third"):
        storage.decision_log("p", summary, "reason")
    path = tmp_path / "decisions" / "p" / f"{date.today().isoformat()}.md"
    content = path.read_text()
    assert "First" in content
    assert "Second" in content
    assert "Third" in content


# ── decision_read ──────────────────────────────────────────────────────────────

def test_decision_read_missing_project_returns_message():
    result = storage.decision_read(project="ghost")
    assert "no decisions found" in result.lower()


def test_decision_read_returns_logged_decision():
    storage.decision_log("alpha", "Pick PostgreSQL", "ACID compliance required")
    result = storage.decision_read(project="alpha", days=1)
    assert "Pick PostgreSQL" in result


def test_decision_read_excludes_files_outside_window(tmp_path):
    stale_dir = tmp_path / "decisions" / "beta"
    stale_dir.mkdir(parents=True)
    old_date = (date.today() - timedelta(days=10)).isoformat()
    (stale_dir / f"{old_date}.md").write_text("stale content")
    result = storage.decision_read(project="beta", days=7)
    assert "no recent decisions found" in result.lower()


def test_decision_read_all_projects_when_none_specified():
    storage.decision_log("proj-a", "Decision 1", "R1")
    storage.decision_log("proj-b", "Decision 2", "R2")
    result = storage.decision_read(project=None, days=1)
    assert "Decision 1" in result
    assert "Decision 2" in result


def test_decision_read_no_storage_root_returns_message():
    result = storage.decision_read(project=None)
    assert "no decisions found" in result.lower()


# ── kanban_add ─────────────────────────────────────────────────────────────────

def test_kanban_add_creates_board_file(tmp_path):
    result = storage.kanban_add("myproject", "Backlog", "Fix bug #42")
    assert "Fix bug #42" in result
    assert (tmp_path / "kanban" / "myproject.md").exists()


def test_kanban_add_writes_card_to_board(tmp_path):
    storage.kanban_add("p", "Backlog", "My Card")
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "My Card" in content


def test_kanban_add_column_not_found_returns_error():
    result = storage.kanban_add("p", "Limbo", "Card")
    assert "not found" in result.lower()


def test_kanban_add_column_match_is_case_insensitive(tmp_path):
    result = storage.kanban_add("p", "backlog", "Lower case column")
    assert "Lower case column" in result


def test_kanban_add_with_metadata(tmp_path):
    storage.kanban_add(
        "p",
        "Backlog",
        "Feature X",
        description="Implement the thing",
        due="2026-12-01",
        tags=["frontend", "urgent"],
        priority="high",
        steps=["[ ] step one", "[x] step two"],
    )
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "2026-12-01" in content
    assert "frontend" in content
    assert "high" in content
    assert "step one" in content


def test_kanban_add_default_columns_are_backlog_in_progress_done(tmp_path):
    storage.kanban_add("newproject", "In Progress", "Active work")
    content = (tmp_path / "kanban" / "newproject.md").read_text()
    assert "## Backlog" in content
    assert "## In Progress" in content
    assert "## Done" in content


# ── kanban_move ────────────────────────────────────────────────────────────────

def test_kanban_move_relocates_card(tmp_path):
    storage.kanban_add("p", "Backlog", "Task A")
    result = storage.kanban_move("p", "Task A", "In Progress")
    assert "Task A" in result
    assert "In Progress" in result

    content = (tmp_path / "kanban" / "p.md").read_text()
    in_progress_pos = content.index("## In Progress")
    backlog_pos = content.index("## Backlog")
    card_pos = content.index("### Task A")
    # Card sits after the In Progress heading
    assert card_pos > in_progress_pos
    # Card does not appear in the Backlog section
    backlog_section = content[backlog_pos:in_progress_pos]
    assert "### Task A" not in backlog_section


def test_kanban_move_card_not_found_returns_error():
    result = storage.kanban_move("p", "Ghost Card", "Done")
    assert "not found" in result.lower()


def test_kanban_move_target_column_not_found_returns_error():
    storage.kanban_add("p", "Backlog", "Task B")
    result = storage.kanban_move("p", "Task B", "Limbo")
    assert "not found" in result.lower()


def test_kanban_move_case_insensitive_card_and_column(tmp_path):
    storage.kanban_add("p", "Backlog", "My Task")
    result = storage.kanban_move("p", "my task", "done")
    assert "My Task" in result


# ── kanban_read ────────────────────────────────────────────────────────────────

def test_kanban_read_no_board_returns_message():
    result = storage.kanban_read("ghost-project")
    assert "no board found" in result.lower()


def test_kanban_read_returns_board_content(tmp_path):
    storage.kanban_add("p", "Backlog", "My Card")
    result = storage.kanban_read("p")
    assert "My Card" in result
    assert "Backlog" in result


# ── _parse_board ───────────────────────────────────────────────────────────────

def test_parse_board_extracts_title_columns_and_cards():
    content = """\
# My Project

## Backlog

### Fix login bug

## In Progress

### Refactor auth

## Done

"""
    title, columns = storage._parse_board(content)
    assert title == "My Project"
    assert [c.name for c in columns] == ["Backlog", "In Progress", "Done"]
    assert columns[0].cards[0].title == "Fix login bug"
    assert columns[1].cards[0].title == "Refactor auth"
    assert columns[2].cards == []


def test_parse_board_empty_string():
    title, columns = storage._parse_board("")
    assert title == "Board"
    assert columns == []


def test_parse_board_card_metadata():
    content = """\
# Board

## Backlog

### My Card

  - due: 2026-06-30
  - tags: [backend, urgent]
  - priority: high

"""
    _, columns = storage._parse_board(content)
    card = columns[0].cards[0]
    assert card.due == "2026-06-30"
    assert card.tags == ["backend", "urgent"]
    assert card.priority == "high"


def test_parse_board_card_steps():
    content = """\
# Board

## Backlog

### Stepped Card

  - steps:
      - [x] Done step
      - [ ] Pending step

"""
    _, columns = storage._parse_board(content)
    card = columns[0].cards[0]
    assert "[x] Done step" in card.steps
    assert "[ ] Pending step" in card.steps


def test_parse_board_card_description():
    content = """\
# Board

## Backlog

### Card with description

    ```md
    First line
    Second line
    ```

"""
    _, columns = storage._parse_board(content)
    card = columns[0].cards[0]
    assert "First line" in card.description
    assert "Second line" in card.description


def test_parse_board_multiple_cards_per_column():
    content = """\
# Board

## Backlog

### Card One

### Card Two

### Card Three

"""
    _, columns = storage._parse_board(content)
    titles = [c.title for c in columns[0].cards]
    assert titles == ["Card One", "Card Two", "Card Three"]
