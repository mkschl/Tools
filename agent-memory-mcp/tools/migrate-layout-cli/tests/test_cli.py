"""Tests for the layout migration command."""

from pathlib import Path

import pytest

import agent_memory_mcp.storage as storage
from migrate_layout import cli

BOARD = """# {name}

## To Do

### Card
  - notes: [[{key}](../notes/{name}/{key}.md)]
"""


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """An old-layout root shaped like the real one."""
    r = tmp_path / "agent-memory"
    write(r / "kanban" / "Housing.md", "# Housing\n\n## To Do\n")
    write(r / "kanban" / "DockerProjects.md", BOARD.format(name="DockerProjects", key="overview"))
    write(r / "notes" / "DockerProjects" / "overview.md", "docker notes")
    (r / "decisions" / "DockerProjects").mkdir(parents=True)  # empty directory
    write(r / "notes" / "techreference" / "a.md", "a")
    write(r / "notes" / "techreference" / "b.pdf", "pdf")
    write(r / "notes" / "Diabetes" / "n1.md", "private 1")
    write(r / "notes" / "Diabetes" / "n2.md", "private 2")
    write(r / "kanban" / "JobSearch.md", "# JobSearch\n\n## To Do\n")
    write(r / "notes" / "JobSearch" / "index.md", "index")
    write(r / "notes" / "JobSearch" / "eigenblue" / "call.md", "call")
    write(r / "notes" / "JobSearch" / "general" / "base.md", "base")
    write(r / "decisions" / "Templates" / "2026-09-17.md", "decision")
    write(r / "notes" / "Templates" / "brief.md", "brief")
    write(r / "kanban" / "Templates.md", "# Templates\n")
    write(r / "kanban" / ".markdownlint.json", "{}")
    write(r / ".markdownlint.json", "{}")
    write(r / ".vscode" / "settings.json", "{}")
    return r


@pytest.fixture
def mapping(tmp_path):
    f = tmp_path / "mapping.toml"
    f.write_text(
        "[projects]\n"
        'DockerProjects = "Tech/DockerProjects"\n'
        'techreference = "tech/Reference"\n'
        'Diabetes = "Health/Diabetes"\n'
        'JobSearch = "JobSearch/Overview"\n'
        'Housing = "Personal/Housing"\n'
        'Templates = "Templates/Templates"\n'
        'Ghost = "Nowhere/Ghost"\n'
    )
    return f


def files(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def run(root, mapping, *extra):
    return cli.main(["--root", str(root), "--mapping", str(mapping), *extra])


def test_dry_run_changes_nothing_and_lists_the_plan(root, mapping, capsys):
    before = files(root)
    assert run(root, mapping) == 0
    out = capsys.readouterr().out
    assert files(root) == before
    assert "DockerProjects  ->  Tech/DockerProjects" in out
    assert "notes (1): overview" in out
    assert "empty directory, dropped" in out
    assert "Dry run" in out
    assert not list(root.parent.glob("*backup*"))


def test_dry_run_never_prints_note_contents(root, mapping, capsys):
    run(root, mapping)
    out = capsys.readouterr().out
    assert "private 1" not in out
    assert "private 2" not in out
    assert "n1, n2" in out


def test_apply_moves_everything_into_project_directories(root, mapping):
    assert run(root, mapping, "--apply", "--no-backup") == 0
    assert (root / "projects" / "Tech" / "DockerProjects" / "board.md").is_file()
    assert (
        root / "projects" / "Tech" / "DockerProjects" / "notes" / "overview.md"
    ).read_text() == ("docker notes")
    assert not (
        root / "projects" / "Tech" / "DockerProjects" / "decisions"
    ).exists()  # empty, dropped
    assert (
        root / "projects" / "Health" / "Diabetes" / "notes" / "n1.md"
    ).read_text() == "private 1"
    assert (root / "projects" / "Templates" / "Templates" / "decisions" / "2026-09-17.md").is_file()
    assert (root / "projects" / "Personal" / "Housing" / "board.md").is_file()


def test_non_markdown_files_move_too(root, mapping):
    run(root, mapping, "--apply", "--no-backup")
    assert (root / "projects" / "tech" / "Reference" / "notes" / "b.pdf").is_file()


def test_folders_with_different_spelling_are_unified(root, mapping):
    run(root, mapping, "--apply", "--no-backup")
    names = sorted(p.name for p in (root / "projects").iterdir())
    assert "Tech" in names or "tech" in names
    assert not ("Tech" in names and "tech" in names)


def test_board_links_and_title_are_rewritten(root, mapping):
    run(root, mapping, "--apply", "--no-backup")
    board = (root / "projects" / "Tech" / "DockerProjects" / "board.md").read_text()
    assert board.startswith("# Tech/DockerProjects")
    assert "[overview](notes/overview.md)" in board
    assert "../notes" not in board


def test_a_custom_board_title_is_kept(root, mapping):
    write(root / "kanban" / "Housing.md", "# My flat\n\n## To Do\n")
    run(root, mapping, "--apply", "--no-backup")
    assert (
        (root / "projects" / "Personal" / "Housing" / "board.md")
        .read_text()
        .startswith("# My flat")
    )


def test_mixed_name_splits_into_its_own_files_and_sub_projects(root, mapping):
    run(root, mapping, "--apply", "--no-backup")
    assert (root / "projects" / "JobSearch" / "Overview" / "board.md").is_file()
    assert (root / "projects" / "JobSearch" / "Overview" / "notes" / "index.md").is_file()
    assert (root / "projects" / "JobSearch" / "eigenblue" / "notes" / "call.md").is_file()
    assert (root / "projects" / "JobSearch" / "general" / "notes" / "base.md").is_file()


def test_old_directories_are_removed_but_hidden_files_stay(root, mapping):
    run(root, mapping, "--apply", "--no-backup")
    assert not (root / "notes").exists()
    assert not (root / "decisions").exists()
    assert [p.name for p in (root / "kanban").iterdir()] == [".markdownlint.json"]
    assert (root / ".vscode" / "settings.json").is_file()
    assert (root / ".markdownlint.json").is_file()


def test_file_count_is_unchanged_and_reported(root, mapping, capsys):
    before = cli.count_files(root)
    assert run(root, mapping, "--apply", "--no-backup") == 0
    assert cli.count_files(root) == before
    assert f"Files before: {before}, after: {before}" in capsys.readouterr().out


def test_apply_makes_a_backup_first(root, mapping):
    before = files(root)
    assert run(root, mapping, "--apply") == 0
    backups = list(root.parent.glob("agent-memory-backup-*"))
    assert len(backups) == 1
    assert files(backups[0]) == before


def test_unmatched_mapping_entry_is_only_a_warning(root, mapping, capsys):
    assert run(root, mapping) == 0
    assert "mapping entry 'ghost' matches no project" in capsys.readouterr().out


def test_a_mixed_name_without_a_mapping_is_refused(root, tmp_path, capsys):
    f = tmp_path / "m.toml"
    f.write_text('[projects]\nHousing = "Personal/Housing"\n')
    before = files(root)
    assert run(root, f, "--apply", "--no-backup") == 1
    out = capsys.readouterr().out
    assert "both a project and a folder" in out
    assert files(root) == before


def test_duplicate_targets_are_refused(root, tmp_path, capsys):
    f = tmp_path / "m.toml"
    f.write_text(
        '[projects]\nHousing = "Same/Place"\nDiabetes = "same/place"\nJobSearch = "JobSearch/O"\n'
    )
    before = files(root)
    assert run(root, f, "--apply", "--no-backup") == 1
    assert "would both become" in capsys.readouterr().out
    assert files(root) == before


def test_an_existing_target_is_refused(root, mapping, capsys):
    write(root / "projects" / "Tech" / "DockerProjects" / "board.md", "# already here\n")
    before = files(root)
    assert run(root, mapping, "--apply", "--no-backup") == 1
    assert "already exists" in capsys.readouterr().out
    assert files(root) == before


@pytest.mark.parametrize(
    "target",
    ["notes/x", "Work/board", "a/b/c/d/e/f", "Work//x", "../x", "Work/with space", "Work/x.md"],
)
def test_invalid_targets_are_refused(root, tmp_path, capsys, target):
    f = tmp_path / "m.toml"
    f.write_text(f'[projects]\nHousing = "{target}"\nJobSearch = "JobSearch/O"\n')
    assert run(root, f, "--apply", "--no-backup") == 1
    assert "Problems found" in capsys.readouterr().out
    assert (root / "kanban" / "Housing.md").is_file()


def test_no_old_layout_means_nothing_to_do(tmp_path, mapping, capsys):
    r = tmp_path / "new"
    write(r / "projects" / "P" / "board.md", "# P\n")
    write(r / "notes" / "loose.md", "standalone")
    assert run(r, mapping, "--apply") == 0
    assert "nothing to migrate" in capsys.readouterr().out
    assert not list(tmp_path.glob("new-backup-*"))


def test_missing_root_is_an_error(tmp_path, mapping):
    assert run(tmp_path / "nope", mapping) == 2


def test_unmapped_projects_keep_their_path(root, mapping):
    write(root / "kanban" / "Extra.md", "# Extra\n")
    run(root, mapping, "--apply", "--no-backup")
    assert (root / "projects" / "Extra" / "board.md").is_file()


def test_the_server_reads_the_migrated_tree(root, mapping, monkeypatch):
    run(root, mapping, "--apply", "--no-backup")
    monkeypatch.setattr(storage, "STORAGE_ROOT", root)
    listing = storage.project_list()
    assert "notes only (2)" in listing  # Health/Diabetes
    assert "JobSearch/" in listing
    assert "collides" not in listing
    assert "also holds other projects" not in listing
    assert "overview" in storage.note_list("Tech/DockerProjects")
    assert storage._missing_notes("Tech/DockerProjects", ["overview"]) == []
    _, columns = storage._parse_board(storage.kanban_read("Tech/DockerProjects"))
    assert columns[0].cards[0].notes == ["overview"]
    assert "Updated" in storage.kanban_update_card("Tech/DockerProjects", "Card", due="2026-01-01")


def test_help_works_with_short_and_long_flag(capsys):
    for flag in ("-h", "--help"):
        with pytest.raises(SystemExit) as exc:
            cli.main([flag])
        assert exc.value.code == 0
    assert "examples:" in capsys.readouterr().out


# ── projects directly at the root -> projects/ ───────────────────────────────


@pytest.fixture
def flat_root(tmp_path):
    r = tmp_path / "agent-memory"
    write(r / "Health" / "Diabetes" / "notes" / "n1.md", "private")
    write(r / "Work" / "AzureAI" / "board.md", "# Work/AzureAI\n")
    write(r / "Homelab" / "board.md", "# Homelab\n")
    write(r / "kanban" / ".markdownlint.json", "{}")  # only a hidden leftover
    write(r / ".markdownlint.json", "{}")
    write(r / ".vscode" / "settings.json", "{}")
    write(r / "notes" / "loose.md", "standalone")
    return r


def test_projects_at_the_root_are_listed_in_a_dry_run(flat_root, capsys):
    before = files(flat_root)
    assert cli.main(["--root", str(flat_root)]) == 0
    out = capsys.readouterr().out
    assert files(flat_root) == before
    for name in ("Health", "Work", "Homelab"):
        assert f"{name}  ->  projects/{name}" in out
    assert "notes  ->" not in out
    assert "Dry run" in out


def test_projects_at_the_root_move_into_projects(flat_root):
    before = cli.count_files(flat_root)
    assert cli.main(["--root", str(flat_root), "--apply", "--no-backup"]) == 0
    assert (flat_root / "projects" / "Health" / "Diabetes" / "notes" / "n1.md").read_text() == (
        "private"
    )
    assert (flat_root / "projects" / "Work" / "AzureAI" / "board.md").is_file()
    assert (flat_root / "projects" / "Homelab" / "board.md").is_file()
    assert cli.count_files(flat_root) == before
    # standalone notes and hidden entries stay where they are
    assert (flat_root / "notes" / "loose.md").read_text() == "standalone"
    assert (flat_root / ".vscode" / "settings.json").is_file()
    assert (flat_root / "kanban" / ".markdownlint.json").is_file()
    assert not (flat_root / "Health").exists()


def test_projects_at_the_root_get_a_backup_first(flat_root):
    before = files(flat_root)
    assert cli.main(["--root", str(flat_root), "--apply"]) == 0
    backups = list(flat_root.parent.glob("agent-memory-backup-*"))
    assert len(backups) == 1
    assert files(backups[0]) == before


def test_the_server_reads_projects_moved_into_projects(flat_root, monkeypatch):
    cli.main(["--root", str(flat_root), "--apply", "--no-backup"])
    monkeypatch.setattr(storage, "STORAGE_ROOT", flat_root)
    listing = storage.project_list()
    assert "Diabetes" in listing
    assert "notes only (1)" in listing
    assert "Standalone notes: 1" in listing
    assert "board only" in listing


def test_a_second_run_has_nothing_to_do(flat_root, capsys):
    cli.main(["--root", str(flat_root), "--apply", "--no-backup"])
    capsys.readouterr()
    assert cli.main(["--root", str(flat_root), "--apply", "--no-backup"]) == 0
    assert "nothing to migrate" in capsys.readouterr().out


def test_a_non_empty_projects_folder_blocks_the_move(flat_root, capsys):
    write(flat_root / "projects" / "Existing" / "board.md", "# Existing\n")
    before = files(flat_root)
    assert cli.main(["--root", str(flat_root), "--apply", "--no-backup"]) == 1
    assert "already exists and is not empty" in capsys.readouterr().out
    assert files(flat_root) == before


def test_both_older_layouts_at_once_are_refused(flat_root, capsys):
    write(flat_root / "kanban" / "Old.md", "# Old\n")
    before = files(flat_root)
    assert cli.main(["--root", str(flat_root), "--apply", "--no-backup"]) == 1
    assert "one layout at a time" in capsys.readouterr().out
    assert files(flat_root) == before


def test_old_style_migration_keeps_standalone_notes_out_of_the_way(root, mapping):
    """The old notes/ tree is migrated; afterwards notes/ is free for loose notes."""
    run(root, mapping, "--apply", "--no-backup")
    assert not (root / "notes").exists()
    write(root / "notes" / "loose.md", "standalone")
    assert not cli.type_first_present(root)
