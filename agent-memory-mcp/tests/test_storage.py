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


# ── kanban_create ──────────────────────────────────────────────────────────────

def test_kanban_create_creates_board_with_given_columns(tmp_path):
    result = storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    assert "Created" in result
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "## To Do" in content
    assert "## In Progress" in content
    assert "## Done" in content


def test_kanban_create_already_exists_returns_message(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    assert "already exists" in result.lower()


def test_kanban_create_empty_columns_returns_error():
    result = storage.kanban_create("p", [])
    assert "empty" in result.lower()


def test_kanban_create_custom_columns(tmp_path):
    result = storage.kanban_create("p", ["Backlog", "Review", "Shipped"])
    assert "Created" in result
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "## Backlog" in content
    assert "## Review" in content
    assert "## Shipped" in content


# ── kanban_add_column ──────────────────────────────────────────────────────────

def test_kanban_add_column_creates_empty_column(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_add_column("p", "Up Next")
    assert "Up Next" in result
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "## Up Next" in content


def test_kanban_add_column_no_board_returns_error():
    result = storage.kanban_add_column("p", "Up Next")
    assert "no board found" in result.lower()


def test_kanban_add_column_already_exists_returns_message():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_add_column("p", "To Do")
    assert "already exists" in result.lower()


def test_kanban_add_column_case_insensitive_duplicate_check():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_add_column("p", "to do")
    assert "already exists" in result.lower()


def test_kanban_add_column_preserves_existing_cards(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "Existing Card")
    storage.kanban_add_column("p", "Up Next")
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "Existing Card" in content
    assert "## Up Next" in content


def test_kanban_add_column_after_inserts_at_correct_position(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_add_column("p", "Up Next", after="To Do")
    assert "Up Next" in result
    content = (tmp_path / "kanban" / "p.md").read_text()
    todo_pos = content.index("## To Do")
    up_next_pos = content.index("## Up Next")
    in_progress_pos = content.index("## In Progress")
    assert todo_pos < up_next_pos < in_progress_pos


def test_kanban_add_column_after_moves_existing_column(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add_column("p", "Up Next")  # appended at end
    result = storage.kanban_add_column("p", "Up Next", after="To Do")
    assert "Moved" in result
    content = (tmp_path / "kanban" / "p.md").read_text()
    todo_pos = content.index("## To Do")
    up_next_pos = content.index("## Up Next")
    in_progress_pos = content.index("## In Progress")
    assert todo_pos < up_next_pos < in_progress_pos


def test_kanban_add_column_after_unknown_column_returns_error():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_add_column("p", "Up Next", after="Nonexistent")
    assert "not found" in result.lower()


# ── kanban_add ─────────────────────────────────────────────────────────────────

def test_kanban_add_creates_card_on_existing_board(tmp_path):
    storage.kanban_create("myproject", ["To Do", "In Progress", "Done"])
    result = storage.kanban_add("myproject", "To Do", "Fix bug #42")
    assert "Fix bug #42" in result
    assert (tmp_path / "kanban" / "myproject.md").exists()


def test_kanban_add_no_board_returns_error():
    result = storage.kanban_add("p", "To Do", "Card")
    assert "no board found" in result.lower()


def test_kanban_add_writes_card_to_board(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "My Card")
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "My Card" in content


def test_kanban_add_column_not_found_returns_error():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_add("p", "Limbo", "Card")
    assert "not found" in result.lower()


def test_kanban_add_column_match_is_case_insensitive(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_add("p", "to do", "Lower case column")
    assert "Lower case column" in result


def test_kanban_add_with_metadata(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add(
        "p",
        "To Do",
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


# ── kanban_move ────────────────────────────────────────────────────────────────

def test_kanban_move_relocates_card(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "Task A")
    result = storage.kanban_move("p", "Task A", "In Progress")
    assert "Task A" in result
    assert "In Progress" in result

    content = (tmp_path / "kanban" / "p.md").read_text()
    in_progress_pos = content.index("## In Progress")
    todo_pos = content.index("## To Do")
    card_pos = content.index("### Task A")
    # Card sits after the In Progress heading
    assert card_pos > in_progress_pos
    # Card does not appear in the To Do section
    todo_section = content[todo_pos:in_progress_pos]
    assert "### Task A" not in todo_section


def test_kanban_move_no_board_returns_error():
    result = storage.kanban_move("p", "Ghost Card", "Done")
    assert "no board found" in result.lower()


def test_kanban_move_card_not_found_returns_error():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_move("p", "Ghost Card", "Done")
    assert "not found" in result.lower()


def test_kanban_move_target_column_not_found_returns_error():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "Task B")
    result = storage.kanban_move("p", "Task B", "Limbo")
    assert "not found" in result.lower()


def test_kanban_move_case_insensitive_card_and_column(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "My Task")
    result = storage.kanban_move("p", "my task", "done")
    assert "My Task" in result


# ── kanban_delete_card ────────────────────────────────────────────────────────

def test_kanban_delete_card_removes_card(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "Remove Me")
    result = storage.kanban_delete_card("p", "Remove Me")
    assert "Deleted" in result
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "Remove Me" not in content


def test_kanban_delete_card_case_insensitive(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "My Card")
    result = storage.kanban_delete_card("p", "my card")
    assert "Deleted" in result


def test_kanban_delete_card_not_found_returns_error():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_delete_card("p", "Ghost")
    assert "not found" in result.lower()


def test_kanban_delete_card_no_board_returns_error():
    result = storage.kanban_delete_card("p", "Card")
    assert "no board found" in result.lower()


# ── kanban_delete_column ──────────────────────────────────────────────────────

def test_kanban_delete_column_removes_empty_column(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_delete_column("p", "Done")
    assert "Deleted" in result
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "## Done" not in content


def test_kanban_delete_column_with_cards_returns_error():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "Blocking Card")
    result = storage.kanban_delete_column("p", "To Do")
    assert "still has" in result.lower()
    assert "Blocking Card" in result


def test_kanban_delete_column_not_found_returns_error():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_delete_column("p", "Limbo")
    assert "not found" in result.lower()


def test_kanban_delete_column_no_board_returns_error():
    result = storage.kanban_delete_column("p", "To Do")
    assert "no board found" in result.lower()


def test_kanban_delete_column_case_insensitive(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_delete_column("p", "done")
    assert "Deleted" in result


# ── kanban_update_card ────────────────────────────────────────────────────────

def test_kanban_update_card_renames_card(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "Old Title")
    result = storage.kanban_update_card("p", "Old Title", new_title="New Title")
    assert "New Title" in result
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "New Title" in content
    assert "Old Title" not in content


def test_kanban_update_card_updates_due_and_priority(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "My Card")
    storage.kanban_update_card("p", "My Card", due="2027-01-01", priority="high")
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "2027-01-01" in content
    assert "high" in content


def test_kanban_update_card_updates_tags_and_steps(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "My Card")
    storage.kanban_update_card("p", "My Card", tags=["a", "b"], steps=["[ ] step"])
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "a" in content
    assert "step" in content


def test_kanban_update_card_untouched_fields_preserved(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "My Card", due="2026-12-01", priority="low")
    storage.kanban_update_card("p", "My Card", new_title="Renamed")
    content = (tmp_path / "kanban" / "p.md").read_text()
    assert "2026-12-01" in content
    assert "low" in content


def test_kanban_update_card_not_found_returns_error():
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    result = storage.kanban_update_card("p", "Ghost")
    assert "not found" in result.lower()


def test_kanban_update_card_no_board_returns_error():
    result = storage.kanban_update_card("p", "Card")
    assert "no board found" in result.lower()


# ── kanban_list ───────────────────────────────────────────────────────────────

def test_kanban_list_returns_board_names(tmp_path):
    storage.kanban_create("alpha", ["To Do", "Done"])
    storage.kanban_create("beta", ["To Do", "Done"])
    result = storage.kanban_list()
    assert "alpha" in result
    assert "beta" in result


def test_kanban_list_no_boards_returns_message(tmp_path):
    result = storage.kanban_list()
    assert "no boards found" in result.lower()


# ── kanban_read ────────────────────────────────────────────────────────────────

def test_kanban_read_no_board_returns_message():
    result = storage.kanban_read("ghost-project")
    assert "no board found" in result.lower()


def test_kanban_read_returns_board_content(tmp_path):
    storage.kanban_create("p", ["To Do", "In Progress", "Done"])
    storage.kanban_add("p", "To Do", "My Card")
    result = storage.kanban_read("p")
    assert "My Card" in result
    assert "To Do" in result


# ── kanban_delete ─────────────────────────────────────────────────────────────

def test_kanban_delete_removes_board(tmp_path):
    storage.kanban_create("p", ["To Do", "Done"])
    result = storage.kanban_delete("p")
    assert "Deleted" in result
    assert not (tmp_path / "kanban" / "p.md").exists()


def test_kanban_delete_no_board_returns_error():
    result = storage.kanban_delete("ghost")
    assert "no board found" in result.lower()


# ── kanban_rename ─────────────────────────────────────────────────────────────

def test_kanban_rename_renames_board(tmp_path):
    storage.kanban_create("old", ["To Do", "Done"])
    storage.kanban_add("old", "To Do", "My Card")
    result = storage.kanban_rename("old", "new")
    assert "new" in result
    assert not (tmp_path / "kanban" / "old.md").exists()
    content = (tmp_path / "kanban" / "new.md").read_text()
    assert "My Card" in content
    assert "# new" in content


def test_kanban_rename_no_board_returns_error():
    result = storage.kanban_rename("ghost", "new")
    assert "no board found" in result.lower()


def test_kanban_rename_target_exists_returns_error():
    storage.kanban_create("a", ["To Do", "Done"])
    storage.kanban_create("b", ["To Do", "Done"])
    result = storage.kanban_rename("a", "b")
    assert "already exists" in result.lower()


# ── kanban_search ─────────────────────────────────────────────────────────────

def test_kanban_search_finds_card_by_title():
    storage.kanban_create("p", ["To Do", "Done"])
    storage.kanban_add("p", "To Do", "Fix the login bug")
    result = storage.kanban_search("login")
    assert "Fix the login bug" in result
    assert "p" in result


def test_kanban_search_finds_card_by_tag():
    storage.kanban_create("p", ["To Do", "Done"])
    storage.kanban_add("p", "To Do", "My Card", tags=["backend", "urgent"])
    result = storage.kanban_search("urgent")
    assert "My Card" in result


def test_kanban_search_finds_card_by_description():
    storage.kanban_create("p", ["To Do", "Done"])
    storage.kanban_add("p", "To Do", "My Card", description="refactor the auth module")
    result = storage.kanban_search("auth")
    assert "My Card" in result


def test_kanban_search_no_match_returns_message():
    storage.kanban_create("p", ["To Do", "Done"])
    storage.kanban_add("p", "To Do", "My Card")
    result = storage.kanban_search("zzznomatch")
    assert "no cards" in result.lower()


def test_kanban_search_scoped_to_project():
    storage.kanban_create("alpha", ["To Do", "Done"])
    storage.kanban_create("beta", ["To Do", "Done"])
    storage.kanban_add("alpha", "To Do", "Fix bug")
    storage.kanban_add("beta", "To Do", "Fix bug")
    result = storage.kanban_search("fix", project="alpha")
    assert "alpha" in result
    assert "beta" not in result


def test_kanban_search_case_insensitive():
    storage.kanban_create("p", ["To Do", "Done"])
    storage.kanban_add("p", "To Do", "Deploy to Production")
    result = storage.kanban_search("production")
    assert "Deploy to Production" in result


# ── notes ─────────────────────────────────────────────────────────────────────

def test_note_write_creates_note(tmp_path):
    result = storage.note_write("p", "architecture", "We use hexagonal architecture.")
    assert "Written" in result
    assert (tmp_path / "notes" / "p" / "architecture.md").exists()


def test_note_write_includes_title_and_date(tmp_path):
    storage.note_write("p", "arch", "Some content")
    content = (tmp_path / "notes" / "p" / "arch.md").read_text()
    assert content.startswith("# arch\n")
    assert "_Updated:" in content


def test_note_write_overwrites_existing_note(tmp_path):
    storage.note_write("p", "arch", "v1")
    storage.note_write("p", "arch", "v2")
    content = (tmp_path / "notes" / "p" / "arch.md").read_text()
    assert "v2" in content
    assert "v1" not in content


def test_note_read_returns_content():
    storage.note_write("p", "key", "hello world")
    result = storage.note_read("p", "key")
    assert "hello world" in result
    assert "# key" in result


def test_note_read_not_found_returns_error():
    result = storage.note_read("p", "ghost")
    assert "not found" in result.lower()


def test_note_list_returns_keys():
    storage.note_write("p", "alpha", "a")
    storage.note_write("p", "beta", "b")
    result = storage.note_list("p")
    assert "alpha" in result
    assert "beta" in result


def test_note_list_no_notes_returns_message():
    result = storage.note_list("ghost-project")
    assert "no notes" in result.lower()


def test_note_delete_removes_note(tmp_path):
    storage.note_write("p", "key", "content")
    result = storage.note_delete("p", "key")
    assert "Deleted" in result
    assert not (tmp_path / "notes" / "p" / "key.md").exists()


def test_note_delete_not_found_returns_error():
    result = storage.note_delete("p", "ghost")
    assert "not found" in result.lower()


# ── project_summary ───────────────────────────────────────────────────────────

def test_project_summary_includes_kanban_and_decisions():
    storage.kanban_create("p", ["To Do", "Done"])
    storage.kanban_add("p", "To Do", "My Task")
    storage.decision_log("p", "Use postgres", "ACID compliance")
    result = storage.project_summary("p", days=1)
    assert "My Task" in result
    assert "Use postgres" in result


def test_project_summary_includes_notes():
    storage.kanban_create("p", ["To Do", "Done"])
    storage.note_write("p", "arch", "Hexagonal architecture")
    result = storage.project_summary("p")
    assert "Hexagonal architecture" in result
    assert "arch" in result


def test_project_summary_no_board_still_returns():
    result = storage.project_summary("ghost")
    assert "ghost" in result


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
