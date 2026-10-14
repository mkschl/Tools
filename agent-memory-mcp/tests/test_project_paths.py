"""Project-first layout: nested folders, path validation, listing and project_move."""

import asyncio

import pytest

import agent_memory_mcp.storage as storage
from agent_memory_mcp import server


@pytest.fixture(autouse=True)
def memory_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def clear_project_locks():
    server._project_locks.clear()
    yield
    server._project_locks.clear()


def make_project(path: str, *, notes=("arch",), decisions=True, board=True):
    if board:
        storage.kanban_create(path, ["To Do", "Done"])
    for key in notes:
        storage.note_write(path, key, f"content of {key}", create=True)
    if decisions:
        storage.decision_log(path, "a decision", "because", create=True)


def snapshot(root):
    """Every markdown file below projects/, relative to it."""
    base = root / "projects"
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*.md"))}


def all_files(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def top_level_names(root):
    """The entries directly below projects/."""
    base = root / "projects"
    return sorted(p.name for p in base.iterdir()) if base.is_dir() else []


# ── Path validation ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "..",
        ".",
        ".hidden",
        "../x",
        "Work/../x",
        "Work/./x",
        "/Work/x",
        "Work/x/",
        "Work//x",
        "Work/with space",
        "Work/ü",
        "Work/x.md",
        "Work/---",
        "Work/.hidden",
        "a\\b",
    ],
)
def test_invalid_names_are_rejected(name):
    with pytest.raises(storage.ProjectPathError):
        storage._split_project(name)


@pytest.mark.parametrize(
    "name",
    ["notes", "Decisions", "board", "Work/notes", "Work/Board", "a/b/decisions"],
)
def test_reserved_names_are_rejected_at_every_level(name):
    with pytest.raises(storage.ProjectPathError, match="reserved"):
        storage._split_project(name)


def test_names_of_the_old_layout_are_fine_inside_projects():
    assert storage._split_project("kanban") == ["kanban"]
    assert storage._split_project("projects") == ["projects"]


def test_names_that_merely_contain_a_reserved_word_are_fine():
    assert storage._split_project("Work/notes-archive") == ["Work", "notes-archive"]
    assert storage._split_project("boardgames") == ["boardgames"]


def test_depth_limit(monkeypatch):
    monkeypatch.setattr(storage, "MAX_DEPTH", 3)
    assert storage._split_project("a/b/c") == ["a", "b", "c"]
    with pytest.raises(storage.ProjectPathError, match="limit is 3"):
        storage._split_project("a/b/c/d")


def test_default_depth_is_five():
    assert storage.MAX_DEPTH == 5


def test_max_depth_env_var(monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_MAX_DEPTH", "2")
    assert storage._read_max_depth() == 2
    for bad in ("0", "abc", "-1"):
        monkeypatch.setenv("AGENT_MEMORY_MAX_DEPTH", bad)
        with pytest.raises(RuntimeError, match="AGENT_MEMORY_MAX_DEPTH"):
            storage._read_max_depth()


def test_single_names_stay_free_text():
    assert storage._split_project("Job Search") == ["Job Search"]
    assert storage._split_project("job-search") == ["job-search"]


def test_project_slug_is_per_segment():
    assert storage._project_slug("Work/My-Project") == "work/myproject"
    assert storage._project_slug("WorkMyProject") != storage._project_slug("Work/MyProject")


def test_symlink_escape_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "projects").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(storage, "STORAGE_ROOT", root)
    (root / "projects" / "Work").symlink_to(outside, target_is_directory=True)

    with pytest.raises(storage.ProjectPathError, match="outside"):
        storage.kanban_create("Work/Example", ["To Do"])
    assert list(outside.iterdir()) == []


def test_hidden_entries_never_match_a_project_name(memory_root):
    (memory_root / "projects" / ".vscode").mkdir(parents=True)
    (memory_root / "projects" / ".vscode" / "settings.json").write_text("{}")
    storage.kanban_create("vscode", ["To Do"])
    assert (memory_root / "projects" / "vscode" / "board.md").is_file()
    assert (memory_root / "projects" / ".vscode" / "settings.json").read_text() == "{}"


# ── Layout ────────────────────────────────────────────────────────────────────


def test_project_is_one_directory(memory_root):
    make_project("JobSearch")
    assert (memory_root / "projects" / "JobSearch" / "board.md").is_file()
    assert (memory_root / "projects" / "JobSearch" / "notes" / "arch.md").is_file()
    assert list((memory_root / "projects" / "JobSearch" / "decisions").glob("*.md"))
    assert top_level_names(memory_root) == ["JobSearch"]


def test_nested_project_lives_in_its_folders(memory_root):
    make_project("Work/Deep/Example")
    base = memory_root / "projects" / "Work" / "Deep" / "Example"
    assert (base / "board.md").is_file()
    assert (base / "notes" / "arch.md").is_file()
    assert list((base / "decisions").glob("*.md"))


def test_board_links_to_notes_are_the_same_at_every_depth(memory_root):
    for name in ("flat", "Work/Deep/Example"):
        make_project(name)
        storage.kanban_add(name, "To Do", "card", notes=["arch"])
        board = memory_root.joinpath("projects", *name.split("/"), "board.md")
        assert "[arch](notes/arch.md)" in board.read_text()
        assert (board.parent / "notes/arch.md").is_file()


def test_nested_board_round_trips_note_links():
    make_project("Work/Example")
    storage.kanban_add("Work/Example", "To Do", "card", notes=["arch"])
    storage.kanban_add("Work/Example", "To Do", "second")
    _, columns = storage._parse_board(storage.kanban_read("Work/Example"))
    assert columns[0].cards[0].notes == ["arch"]


def test_existing_segment_spelling_is_reused(memory_root):
    make_project("Health/General")
    storage.note_write("health/general", "two", "x")
    # macOS is case-insensitive, so look at the real directory names.
    assert top_level_names(memory_root) == ["Health"]
    assert (memory_root / "projects" / "Health" / "General" / "notes" / "two.md").is_file()


def test_unknown_nested_project_needs_create():
    assert "Unknown project" in storage.note_write("Work/Nope", "k", "x")


def test_nested_project_works_through_every_tool():
    make_project("Work/Example")
    assert "arch" in storage.note_list("Work/Example")
    assert "content of arch" in storage.note_read("Work/Example", "arch")
    assert "a decision" in storage.decision_read("Work/Example")
    assert "To Do" in storage.kanban_read("Work/Example")
    assert "Work/Example" in storage.kanban_list()
    assert "Work/Example" in storage.decision_read()


def test_kanban_search_labels_nested_boards():
    make_project("Work/Example")
    storage.kanban_add("Work/Example", "To Do", "findme")
    assert "**Work/Example** / To Do / findme" in storage.kanban_search("findme")


def test_kanban_delete_prunes_empty_folders(memory_root):
    make_project("Work/Example", notes=(), decisions=False)
    storage.kanban_delete("Work/Example")
    assert top_level_names(memory_root) == []


def test_kanban_delete_keeps_the_notes(memory_root):
    make_project("Work/Example", decisions=False)
    storage.kanban_delete("Work/Example")
    assert (memory_root / "projects" / "Work" / "Example" / "notes" / "arch.md").is_file()
    assert not (memory_root / "projects" / "Work" / "Example" / "board.md").exists()


# ── Folder versus project ─────────────────────────────────────────────────────


def test_project_cannot_be_created_on_a_folder():
    make_project("Work/Example")
    with pytest.raises(storage.ProjectPathError, match="is a folder"):
        storage.kanban_create("Work", ["To Do"])
    with pytest.raises(storage.ProjectPathError, match="is a folder"):
        storage.note_write("Work", "k", "x", create=True)
    with pytest.raises(storage.ProjectPathError, match="is a folder"):
        storage.decision_log("Work", "s", "r", create=True)


def test_folder_cannot_be_created_below_a_project():
    make_project("Work")
    with pytest.raises(storage.ProjectPathError, match="is a project"):
        storage.kanban_create("Work/Example", ["To Do"])
    with pytest.raises(storage.ProjectPathError, match="is a project"):
        storage.note_write("Work/Example", "k", "x", create=True)


@pytest.mark.parametrize("what", ["board", "notes", "decisions"])
def test_any_one_entry_makes_a_project(memory_root, what):
    d = memory_root / "projects" / "Work" / "P"
    d.mkdir(parents=True)
    if what == "board":
        (d / "board.md").write_text("# P\n")
    else:
        (d / what).mkdir()
    assert storage._project_exists("Work/P")
    assert not storage._is_folder("Work/P")
    assert not storage._project_exists("Work")
    assert storage._is_folder("Work")
    assert storage._is_folder("work")


def test_empty_directory_is_neither_folder_nor_project(memory_root):
    (memory_root / "projects" / "Empty").mkdir(parents=True)
    assert not storage._project_exists("Empty")
    assert not storage._is_folder("Empty")
    storage.kanban_create("Empty", ["To Do"])
    assert (memory_root / "projects" / "Empty" / "board.md").is_file()


def test_conflicts_come_back_as_messages_through_the_server():
    make_project("Work/Example")

    async def go():
        return await server._run(storage.kanban_create, "Work", ["To Do"], project="Work")

    assert "is a folder" in asyncio.run(go())


def test_invalid_name_comes_back_as_message_through_the_server():
    async def go():
        return await server._run(storage.kanban_read, "../x", project="../x")

    assert "Invalid project path" in asyncio.run(go())


# ── Old layout ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "old_file",
    [
        "kanban/p.md",  # type-first layout
        "decisions/p/2026-01-01.md",
        "notes/p/k.md",
        "Work/p/board.md",  # projects directly at the root, outside projects/
    ],
)
def test_older_layouts_are_refused_and_nothing_is_written(memory_root, old_file):
    path = memory_root / old_file
    path.parent.mkdir(parents=True)
    path.write_text("old data")
    before = all_files(memory_root)

    calls = [
        lambda: storage.kanban_create("p", ["To Do"]),
        lambda: storage.note_write("p", "k", "x", create=True),
        lambda: storage.note_write("", "k", "x"),
        lambda: storage.decision_log("p", "s", "r", create=True),
        lambda: storage.project_list(),
        lambda: storage.kanban_list(),
        lambda: storage.decision_read(),
        lambda: storage.project_move("p", "q", dry_run=False),
        lambda: storage.kanban_read("p"),
        lambda: storage.note_list(""),
    ]
    for call in calls:
        with pytest.raises(storage.LegacyLayoutError, match="older layout"):
            call()

    assert all_files(memory_root) == before


def test_old_layout_message_comes_back_through_the_server(memory_root):
    (memory_root / "kanban").mkdir()
    (memory_root / "kanban" / "p.md").write_text("old data")

    async def go():
        return await server._run(storage.project_list)

    assert "older layout" in asyncio.run(go())


def test_top_level_directories_with_only_hidden_files_do_not_count(memory_root):
    (memory_root / "kanban").mkdir()
    (memory_root / "kanban" / ".markdownlint.json").write_text("{}")
    (memory_root / ".vscode").mkdir()
    storage.kanban_create("p", ["To Do"])
    assert storage.project_list().startswith("p")


def test_projects_and_standalone_notes_are_the_current_layout(memory_root):
    make_project("Work/p")
    storage.note_write("", "loose", "text")
    assert storage._old_layout_parts() == []
    assert sorted(p.name for p in memory_root.iterdir()) == ["notes", "projects"]


# ── Standalone notes ──────────────────────────────────────────────────────────


def test_standalone_notes_live_in_the_top_level_notes_folder(memory_root):
    result = storage.note_write("", "shopping", "milk")
    assert "standalone" in result
    assert (memory_root / "notes" / "shopping.md").is_file()
    assert not (memory_root / "projects").exists()


def test_standalone_notes_can_be_read_listed_and_deleted(memory_root):
    storage.note_write("", "a", "first")
    storage.note_write("", "b", "second")
    assert storage.note_list("") == "a\nb"
    assert "first" in storage.note_read("", "a")
    assert "Deleted" in storage.note_delete("", "a")
    assert storage.note_list("") == "b"
    assert "not found" in storage.note_read("", "a")


def test_standalone_notes_are_separate_from_project_notes(memory_root):
    make_project("p", notes=("arch",))
    storage.note_write("", "arch", "loose one")
    assert "loose one" in storage.note_read("", "arch")
    assert "content of arch" in storage.note_read("p", "arch")
    assert storage.note_list("p") == "arch"


def test_empty_standalone_notes(memory_root):
    assert storage.note_list("") == "No notes found (standalone)."


def test_standalone_notes_show_up_in_project_list(memory_root):
    make_project("p")
    storage.note_write("", "a", "x")
    result = storage.project_list()
    assert "Standalone notes: 1" in result
    assert "Standalone notes" not in storage.project_list("Work")


def test_standalone_notes_do_not_count_as_a_project(memory_root):
    storage.note_write("", "a", "x")
    assert storage.project_list().startswith("No projects found.")
    assert storage.kanban_list() == "No boards found."


@pytest.mark.parametrize("key", ["", " ", "../x", "a/b", ".hidden", "a\\b"])
def test_note_keys_cannot_escape_the_notes_folder(memory_root, key):
    with pytest.raises(storage.ProjectPathError, match="Invalid note key"):
        storage.note_write("", key, "x")
    with pytest.raises(storage.ProjectPathError, match="Invalid note key"):
        storage.note_read("p", key)
    assert all_files(memory_root) == {}


def test_standalone_note_writes_share_one_lock():
    assert (
        server._locks_for(server._STANDALONE_LOCK)[0]
        is server._locks_for(server._STANDALONE_LOCK)[0]
    )
    assert server._locks_for(server._STANDALONE_LOCK)[0] is not server._locks_for("notes-x")[0]


# ── project_list / folder_list / project_summary ──────────────────────────────


def test_project_list_says_what_each_project_has():
    make_project("Both")
    make_project("OnlyBoard", notes=(), decisions=False)
    make_project("OnlyNotes", board=False, decisions=False, notes=("a", "b"))
    make_project("OnlyDecisions", board=False, notes=())
    result = storage.project_list()
    lines = {line.split()[0]: line for line in result.splitlines()}
    assert "board + notes (1) + decisions (1)" in lines["Both"]
    assert "board only" in lines["OnlyBoard"]
    assert "notes only (2)" in lines["OnlyNotes"]
    assert "decisions only (1)" in lines["OnlyDecisions"]


def test_project_list_shows_a_tree():
    make_project("Work/Example")
    make_project("JobSearch")
    lines = storage.project_list().splitlines()
    assert lines[0].startswith("JobSearch")
    assert lines[1] == "Work/"
    assert lines[2].startswith("  Example")
    assert "board + notes (1) + decisions (1)" in lines[2]


def test_project_list_nested_two_levels():
    make_project("Personal/Hobby/3DPrinting")
    lines = storage.project_list().splitlines()
    assert lines[0] == "Personal/"
    assert lines[1] == "  Hobby/"
    assert lines[2].startswith("    3DPrinting")


def test_project_list_folder_filter():
    make_project("Work/Example")
    make_project("Work/Other")
    make_project("Home/Thing")
    result = storage.project_list("Work")
    assert "Example" in result
    assert "Other" in result
    assert "Thing" not in result


def test_project_list_folder_filter_is_case_insensitive():
    make_project("Work/Example")
    assert "Example" in storage.project_list("work")


def test_project_list_unknown_folder():
    make_project("Work/Example")
    assert storage.project_list("Nope") == "No projects found in folder 'Nope'."


def test_project_list_ignores_hidden_entries(memory_root):
    (memory_root / "projects" / ".vscode").mkdir(parents=True)
    (memory_root / "projects" / ".vscode" / "settings.json").write_text("{}")
    (memory_root / ".vscode").mkdir()
    assert storage.project_list() == "No projects found."


def test_project_list_flags_collision(memory_root):
    make_project("Work/Example")
    # The same project under another spelling, as a sync conflict could leave it
    # (a separator variant: a casing variant would be the same directory on macOS).
    (memory_root / "projects" / "Work" / "Exam-ple" / "notes").mkdir(parents=True)
    (memory_root / "projects" / "Work" / "Exam-ple" / "notes" / "k.md").write_text("x")
    assert "collides" in storage.project_list()


def test_project_list_nested_and_single_with_same_letters_do_not_collide(memory_root):
    storage.kanban_create("WorkExample", ["To Do"])
    storage.kanban_create("Work/Example", ["To Do"])
    assert "collides" not in storage.project_list()


def test_project_list_flags_a_project_that_is_also_a_folder(memory_root):
    make_project("JobSearch")
    (memory_root / "projects" / "JobSearch" / "eigenblue" / "notes").mkdir(parents=True)
    (memory_root / "projects" / "JobSearch" / "eigenblue" / "notes" / "k.md").write_text("x")
    result = storage.project_list()
    assert "also holds other projects" in result
    assert "eigenblue" in result


def test_folder_list_counts_projects_below():
    make_project("Work/A")
    make_project("Work/B")
    make_project("Personal/Hobby/C")
    make_project("Flat")
    lines = storage.folder_list().splitlines()
    assert any(line.startswith("Work") and "2 project(s)" in line for line in lines)
    assert any(line.startswith("Personal ") and "1 project(s)" in line for line in lines)
    assert any(line.startswith("Personal/Hobby") and "1 project(s)" in line for line in lines)
    assert not any(line.startswith("Flat") for line in lines)


def test_folder_list_empty():
    make_project("Flat")
    assert storage.folder_list() == "No folders found."


def test_project_summary_of_folder_aggregates_projects():
    make_project("Work/A")
    make_project("Work/B")
    result = storage.project_summary("Work")
    assert "# Work (folder, 2 project(s))" in result
    assert "# Work/A" in result
    assert "# Work/B" in result


def test_project_summary_of_project_is_unchanged():
    make_project("Work/A")
    result = storage.project_summary("Work/A")
    assert result.startswith("# Work/A")
    assert "folder" not in result.splitlines()[0]


# ── project_move ──────────────────────────────────────────────────────────────


def test_dry_run_changes_nothing(memory_root):
    make_project("AzureTraining")
    before = snapshot(memory_root)
    result = storage.project_move("AzureTraining", "Work/AzureTraining")
    assert result.startswith("Dry run")
    assert "arch" in result
    assert "content of arch" not in result
    assert snapshot(memory_root) == before
    assert top_level_names(memory_root) == ["AzureTraining"]


def test_dry_run_is_the_default():
    make_project("p")
    assert storage.project_move("p", "Work/p").startswith("Dry run")
    assert storage._project_exists("p")


def test_move_into_a_folder(memory_root):
    make_project("AzureTraining")
    old = snapshot(memory_root)
    result = storage.project_move("AzureTraining", "Work/AzureTraining", dry_run=False)
    assert result.startswith("Moved project 'AzureTraining' to 'Work/AzureTraining'")
    assert top_level_names(memory_root) == ["Work"]
    base = memory_root / "projects" / "Work" / "AzureTraining"
    assert (base / "board.md").is_file()
    assert (base / "notes" / "arch.md").is_file()
    assert list((base / "decisions").glob("*.md"))
    assert not storage._project_exists("AzureTraining")
    # notes and decisions arrive byte for byte
    moved = snapshot(memory_root)
    for rel, data in old.items():
        if "/notes/" in rel or "/decisions/" in rel:
            assert moved["Work/" + rel] == data


def test_board_links_still_resolve_after_a_move(memory_root):
    make_project("p")
    storage.kanban_add("p", "To Do", "card", notes=["arch"])
    storage.project_move("p", "Work/Deep/p", dry_run=False)
    _, columns = storage._parse_board(storage.kanban_read("Work/Deep/p"))
    assert columns[0].cards[0].notes == ["arch"]
    assert storage._missing_notes("Work/Deep/p", ["arch"]) == []
    assert "Updated" in storage.kanban_update_card("Work/Deep/p", "card", notes=["arch"])


def test_move_out_of_a_folder(memory_root):
    make_project("Work/p")
    storage.project_move("Work/p", "p", dry_run=False)
    assert top_level_names(memory_root) == ["p"]


def test_move_updates_the_board_title_only_when_it_was_the_name(memory_root):
    make_project("p")
    storage.project_move("p", "Work/p", dry_run=False)
    assert storage.kanban_read("Work/p").startswith("# Work/p")

    make_project("q")
    path = memory_root / "projects" / "q" / "board.md"
    path.write_text(path.read_text().replace("# q", "# Custom title", 1))
    storage.project_move("q", "Work/q", dry_run=False)
    assert storage.kanban_read("Work/q").startswith("# Custom title")


def test_move_prunes_emptied_folders_but_keeps_shared_ones(memory_root):
    make_project("Work/A")
    make_project("Work/B")
    storage.project_move("Work/A", "Home/A", dry_run=False)
    assert (memory_root / "projects" / "Work" / "B" / "board.md").is_file()
    storage.project_move("Work/B", "Home/B", dry_run=False)
    assert top_level_names(memory_root) == ["Home"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"board": False, "decisions": False},
        {"notes": (), "decisions": False},
        {"board": False, "notes": ()},
    ],
)
def test_move_handles_projects_with_only_some_parts(memory_root, kwargs):
    make_project("n", **kwargs)
    before = snapshot(memory_root)
    assert storage.project_move("n", "Work/n", dry_run=False).startswith("Moved")
    after = snapshot(memory_root)
    assert {k.removeprefix("Work/") for k in after} == set(before)


def test_move_reuses_the_spelling_of_an_existing_target_folder(memory_root):
    make_project("Health/General")
    make_project("Diabetes")
    storage.project_move("Diabetes", "health/Diabetes", dry_run=False)
    assert top_level_names(memory_root) == ["Health"]
    assert (memory_root / "projects" / "Health" / "Diabetes" / "board.md").is_file()


def test_move_unknown_source():
    assert storage.project_move("Nope", "Work/Nope", dry_run=False) == "No project 'Nope' to move."


def test_move_source_that_is_a_folder():
    make_project("Work/A")
    assert "is a folder" in storage.project_move("Work", "Other", dry_run=False)


def test_move_fails_when_target_exists(memory_root):
    make_project("a")
    make_project("Work/b")
    before = snapshot(memory_root)
    assert "already exists" in storage.project_move("a", "Work/b", dry_run=False)
    assert snapshot(memory_root) == before


def test_move_fails_when_target_is_a_folder(memory_root):
    make_project("a")
    make_project("Work/b")
    with pytest.raises(storage.ProjectPathError, match="is a folder"):
        storage.project_move("a", "Work", dry_run=False)


def test_move_fails_below_an_existing_project(memory_root):
    make_project("a")
    make_project("Work")
    with pytest.raises(storage.ProjectPathError, match="is a project"):
        storage.project_move("a", "Work/a", dry_run=False)


def test_move_into_itself_is_rejected():
    make_project("a")
    with pytest.raises(storage.ProjectPathError, match="is a project"):
        storage.project_move("a", "a/b", dry_run=False)


def test_move_to_the_same_project_is_rejected():
    make_project("Work/a")
    assert "same project" in storage.project_move("Work/a", "work/A", dry_run=False)


def test_move_rejects_invalid_paths():
    make_project("a")
    with pytest.raises(storage.ProjectPathError):
        storage.project_move("a", "../escape", dry_run=False)
    with pytest.raises(storage.ProjectPathError):
        storage.project_move("a", "Work//a", dry_run=False)
    with pytest.raises(storage.ProjectPathError, match="reserved"):
        storage.project_move("a", "Work/notes", dry_run=False)


def test_a_failed_move_changes_nothing(memory_root, monkeypatch):
    make_project("p")
    before = snapshot(memory_root)

    def failing(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(storage, "_rename", failing)
    result = storage.project_move("p", "Work/Deep/p", dry_run=False)

    assert "disk went away" in result
    assert "nothing changed" in result
    assert snapshot(memory_root) == before
    assert top_level_names(memory_root) == ["p"]  # no folders left behind


def test_a_move_is_a_single_rename(memory_root, monkeypatch):
    make_project("p")
    calls = []
    real = storage._rename
    monkeypatch.setattr(storage, "_rename", lambda s, d: (calls.append((s, d)), real(s, d)))
    storage.project_move("p", "Work/p", dry_run=False)
    assert calls == [(memory_root / "projects" / "p", memory_root / "projects" / "Work" / "p")]


# ── kanban_rename ─────────────────────────────────────────────────────────────


def test_kanban_rename_keeps_the_folder(memory_root):
    make_project("Work/Old")
    assert "Renamed" in storage.kanban_rename("Work/Old", "New")
    assert (memory_root / "projects" / "Work" / "New" / "board.md").is_file()
    assert (memory_root / "projects" / "Work" / "New" / "notes" / "arch.md").is_file()
    assert not (memory_root / "projects" / "Work" / "Old").exists()


def test_kanban_rename_changes_the_spelling_on_disk(memory_root):
    make_project("Work/jobsearch")
    assert "Renamed" in storage.kanban_rename("Work/jobsearch", "JobSearch")
    assert [p.name for p in (memory_root / "projects" / "Work").iterdir()] == ["JobSearch"]
    assert (memory_root / "projects" / "Work" / "JobSearch" / "board.md").is_file()


def test_kanban_rename_rejects_a_path_as_new_name():
    make_project("a")
    assert "project_move" in storage.kanban_rename("a", "Work/a")
    assert storage._project_exists("a")


def test_kanban_rename_refuses_an_existing_name():
    make_project("a")
    make_project("b")
    assert "already exists" in storage.kanban_rename("a", "b")


# ── Locking through the server ────────────────────────────────────────────────


def test_move_locks_source_and_target_slugs():
    locks = server._locks_for(("a", "Work/a"))
    assert len(locks) == 2
    assert server._locks_for("Work/A")[0] is server._locks_for("work/a")[0]
    assert server._locks_for("WorkA")[0] is not server._locks_for("Work/A")[0]


def test_concurrent_moves_into_the_same_target_do_not_clobber(memory_root):
    make_project("a")
    make_project("b")

    async def go():
        return await asyncio.gather(
            server._run(storage.project_move, "a", "Work/x", False, project=("a", "Work/x")),
            server._run(storage.project_move, "b", "Work/x", False, project=("b", "Work/x")),
        )

    results = asyncio.run(go())
    assert sum(r.startswith("Moved") for r in results) == 1
    assert sum("already exists" in r for r in results) == 1
    survivors = [p for p in ("a", "b") if storage._project_exists(p)]
    assert len(survivors) == 1
    assert storage.note_list(survivors[0]) == "arch"
