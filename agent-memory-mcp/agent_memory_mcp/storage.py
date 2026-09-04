"""File-based storage under ~/.agent-memory/."""

import difflib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import os

from agent_memory_mcp import markdownlint


def _check_storage_dir(path: Path, label: str) -> None:
    if not path.is_dir():
        raise RuntimeError(
            f"{label}={str(path)!r} does not exist. "
            "Create the directory (or let your sync service create it) before starting the server."
        )
    probe = path / ".agent-memory-write-check"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(f"{label}={str(path)!r} is not writable: {exc}") from exc


_env_dir = os.environ.get("AGENT_MEMORY_DIR")
if _env_dir:
    STORAGE_ROOT = Path(_env_dir)
    _check_storage_dir(STORAGE_ROOT, "AGENT_MEMORY_DIR")
else:
    STORAGE_ROOT = Path.home() / ".agent-memory"
    STORAGE_ROOT.mkdir(exist_ok=True)
    _check_storage_dir(STORAGE_ROOT, "AGENT_MEMORY_DIR")
_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


# ── Project identity ─────────────────────────────────────────────────────────
#
# `project` is user-supplied free text (e.g. "JobSearch", "job-search", "Job
# Search"). All of those must resolve to the same on-disk project — otherwise
# a client that spells a project name slightly differently silently creates a
# sibling directory instead of finding the existing one.
#
# Rather than force every path to a normalized (lowercased, separator-free)
# slug and track the original spelling in a side file, each path helper below
# *scans* its subsystem directory (kanban/, notes/, decisions/) for an
# existing entry that normalizes to the same slug, and reuses it verbatim —
# casing and all — if found. A brand-new project is created using the exact
# spelling given. This means the on-disk name always IS the display name
# (nothing to keep in sync), at the cost of a directory listing per call —
# negligible at this scale (a handful of personal projects).
#
# One consequence: "first-seen spelling" is tracked per subsystem, not
# globally. If a board is created as "JobSearch" and a note is later written
# under "job-search", the notes directory is named "job-search" (its own
# first-seen spelling), not "JobSearch" — there's no shared registry to say
# otherwise. What matters for the bug this fixes is that a *second* call to
# note_write with any casing variant reuses that same "job-search" directory
# instead of creating yet another sibling.


def _slugify(name: str) -> str:
    """Normalize a project name to a stable, casing/separator-insensitive slug
    for *comparison only* — never used as an on-disk name."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_existing(base_dir: Path, project: str, *, is_file: bool, suffix: str = "") -> Path | None:
    """Look for an existing entry under `base_dir` whose name normalizes to
    the same slug as `project`, returning it with its on-disk casing intact."""
    if not base_dir.exists():
        return None
    slug = _slugify(project)
    if is_file:
        candidates = base_dir.glob(f"*{suffix}")
        return next((p for p in candidates if _slugify(p.stem) == slug), None)
    return next((p for p in base_dir.iterdir() if p.is_dir() and _slugify(p.name) == slug), None)


def _project_exists(project: str) -> bool:
    return (
        _kanban_path(project).exists()
        or _notes_dir(project).exists()
        or _decisions_dir(project).exists()
    )


def _suggest_project(project: str) -> str | None:
    """Suggest an existing project's on-disk spelling for a likely typo/casing miss."""
    known_names: set[str] = set()
    kanban_dir = STORAGE_ROOT / "kanban"
    if kanban_dir.exists():
        known_names |= {p.stem for p in kanban_dir.glob("*.md")}
    notes_root = STORAGE_ROOT / "notes"
    if notes_root.exists():
        known_names |= {p.name for p in notes_root.iterdir() if p.is_dir()}
    decisions_root = STORAGE_ROOT / "decisions"
    if decisions_root.exists():
        known_names |= {p.name for p in decisions_root.iterdir() if p.is_dir()}

    by_slug: dict[str, str] = {}
    for name in known_names:
        by_slug.setdefault(_slugify(name), name)
    by_slug.pop(_slugify(project), None)  # an exact/normalized match isn't a "suggestion"

    match = difflib.get_close_matches(_slugify(project), list(by_slug), n=1, cutoff=0.8)
    return by_slug[match[0]] if match else None


def _require_existing_project(project: str, create: bool) -> str | None:
    """Return an error message if `project` is unknown and `create` wasn't passed."""
    if create or _project_exists(project):
        return None
    suggestion = _suggest_project(project)
    if suggestion:
        return (
            f"Unknown project '{project}'. Did you mean '{suggestion}'? "
            "Pass create=true to create a new project."
        )
    return f"Unknown project '{project}'. Pass create=true to create a new project."


# ── Paths ─────────────────────────────────────────────────────────────────────


def _decisions_dir(project: str) -> Path:
    base = STORAGE_ROOT / "decisions"
    return _find_existing(base, project, is_file=False) or base / project


def _kanban_path(project: str) -> Path:
    base = STORAGE_ROOT / "kanban"
    existing = _find_existing(base, project, is_file=True, suffix=".md")
    return existing or base / f"{project}.md"


def _notes_dir(project: str) -> Path:
    base = STORAGE_ROOT / "notes"
    return _find_existing(base, project, is_file=False) or base / project


# ── Decisions ─────────────────────────────────────────────────────────────────


def decision_log(project: str, summary: str, reasoning: str, create: bool = False) -> str:
    error = _require_existing_project(project, create)
    if error:
        return error
    d = _decisions_dir(project)
    display_name = d.name  # the existing dir's on-disk spelling, or `project` if new
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date.today().isoformat()}.md"
    if not path.exists():
        header = _env.get_template("decision_header.md.j2").render(
            project=display_name,
            date=date.today().isoformat(),
        )
        path.write_text(header)
    entry = _env.get_template("decision_entry.md.j2").render(
        time=datetime.now().strftime("%H:%M"),
        summary=summary,
        reasoning=reasoning,
    )
    with open(path, "a") as f:
        f.write(entry)
    return f"Logged to {path}"


def decision_read(project: str | None = None, days: int = 7) -> str:
    cutoff = date.today() - timedelta(days=days)
    decisions_root = STORAGE_ROOT / "decisions"

    if project:
        roots = [_decisions_dir(project)]
        if not roots[0].exists():
            return f"No decisions found for project '{project}'."
    else:
        if not decisions_root.exists():
            return "No decisions found."
        roots = sorted(p for p in decisions_root.iterdir() if p.is_dir())

    results = []
    for root in roots:
        for f in sorted(root.glob("*.md")):
            try:
                file_date = date.fromisoformat(f.stem)
            except ValueError:
                continue
            if file_date < cutoff:
                continue
            results.append(f"### {root.name} / {f.stem}\n\n{f.read_text()}")

    return "\n---\n".join(results) if results else "No recent decisions found."


# ── Kanban ────────────────────────────────────────────────────────────────────


@dataclass
class Card:
    title: str
    description: str = ""
    due: str = ""
    tags: list[str] = field(default_factory=list)
    priority: str = ""
    steps: list[str] = field(default_factory=list)  # e.g. ["[x] done", "[ ] pending"]
    notes: list[str] = field(default_factory=list)  # note keys in the same project
    extra_meta: list[str] = field(default_factory=list)  # unrecognised lines preserved as-is


@dataclass
class Column:
    name: str
    cards: list[Card] = field(default_factory=list)


def _parse_board(content: str) -> tuple[str, list[Column]]:
    """Parse a holooo markdown-kanban board file."""
    board_title = "Board"
    columns: list[Column] = []
    current_col: Column | None = None
    current_card: Card | None = None
    in_description = False
    in_steps = False
    description_lines: list[str] = []
    extra_meta_lines: list[str] = []

    def flush_card() -> None:
        nonlocal current_card, in_description, in_steps, description_lines, extra_meta_lines
        if current_card is not None and current_col is not None:
            current_card.description = "\n".join(description_lines).strip()
            current_card.extra_meta = list(extra_meta_lines)
            current_col.cards.append(current_card)
        current_card = None
        in_description = False
        in_steps = False
        description_lines = []
        extra_meta_lines = []

    for line in content.splitlines():
        if line.startswith("# ") and not columns and current_card is None:
            board_title = line[2:].strip()
            continue
        if line.startswith("## "):
            flush_card()
            current_col = Column(name=line[3:].strip())
            columns.append(current_col)
            continue
        if line.startswith("### "):
            flush_card()
            current_card = Card(title=line[4:].strip())
            continue
        if current_card is None:
            continue
        if in_description:
            if line.rstrip() == "    ```":
                in_description = False
            else:
                description_lines.append(line[4:] if line.startswith("    ") else line)
            continue
        # Step sub-items sit at 6-space indent; consume them while in_steps mode.
        # Any line that doesn't match resets in_steps before normal processing.
        if in_steps and line.startswith("      "):
            step = line.strip()
            if step.startswith("- "):
                current_card.steps.append(step[2:])
            continue
        in_steps = False
        if line.rstrip() == "    ```md":
            in_description = True
            continue
        stripped = line.strip()
        if stripped.startswith("- due: "):
            current_card.due = stripped[7:]
        elif stripped.startswith("- tags: "):
            tags_str = stripped[8:].strip().strip("[]")
            current_card.tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        elif stripped.startswith("- priority: "):
            current_card.priority = stripped[12:]
        elif stripped.startswith("- notes: "):
            notes_str = stripped[9:].strip().strip("[]")
            current_card.notes = [n.strip() for n in notes_str.split(",") if n.strip()]
        elif stripped == "- steps:":
            in_steps = True
        elif line.startswith("  ") and stripped:
            # preserve unknown fields (workload, defaultExpanded, …)
            extra_meta_lines.append(line)

    flush_card()
    return board_title, columns


def _render_board(title: str, columns: list[Column]) -> str:
    return _env.get_template("kanban_board.md.j2").render(title=title, columns=columns)


def _load_board(project: str) -> tuple[str, list[Column]] | None:
    path = _kanban_path(project)
    if not path.exists():
        return None
    return _parse_board(path.read_text())


def kanban_create(project: str, columns: list[str]) -> str:
    if not columns:
        return "columns must not be empty."
    path = _kanban_path(project)
    if path.exists():
        return f"Board '{project}' already exists."
    _save_board(project, project, [Column(name=c) for c in columns])
    return f"Created board '{project}' with columns: {columns}."


def _save_board(project: str, title: str, columns: list[Column]) -> None:
    path = _kanban_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_board(title, columns))


def kanban_add_column(project: str, column: str, after: str = "") -> str:
    board = _load_board(project)
    if board is None:
        return f"No board found for project '{project}'. Create one first with kanban_create."
    board_title, columns = board

    existing_idx = next(
        (i for i, c in enumerate(columns) if c.name.lower() == column.lower()), None
    )
    new_col = columns[existing_idx] if existing_idx is not None else Column(name=column)

    if after:
        after_idx = next(
            (i for i, c in enumerate(columns) if c.name.lower() == after.lower()), None
        )
        if after_idx is None:
            return f"Column '{after}' not found. Available: {[c.name for c in columns]}"
        if existing_idx is not None:
            columns.pop(existing_idx)
            # recalculate after_idx if the removed column was before it
            if existing_idx <= after_idx:
                after_idx -= 1
        columns.insert(after_idx + 1, new_col)
        _save_board(project, board_title, columns)
        action = "Moved" if existing_idx is not None else "Added"
        return f"{action} column '{new_col.name}' after '{after}'."

    if existing_idx is not None:
        return f"Column '{new_col.name}' already exists."
    columns.append(new_col)
    _save_board(project, board_title, columns)
    return f"Added column '{column}'."


def _missing_notes(project: str, note_keys: list[str]) -> list[str]:
    notes_dir = _notes_dir(project)
    return [key for key in note_keys if not (notes_dir / f"{key}.md").exists()]


def kanban_add(
    project: str,
    column: str,
    title: str,
    description: str = "",
    due: str = "",
    tags: list[str] | None = None,
    priority: str = "",
    steps: list[str] | None = None,
    notes: list[str] | None = None,
) -> str:
    board = _load_board(project)
    if board is None:
        return f"No board found for project '{project}'. Create one first with kanban_create."
    board_title, columns = board
    note_list = notes or []
    missing = _missing_notes(project, note_list)
    if missing:
        return (
            f"Note(s) {missing} not found in project '{project}'. "
            "Write them first with note_write, or check note_list."
        )
    for col in columns:
        if col.name.lower() == column.lower():
            col.cards.append(
                Card(
                    title=title,
                    description=description,
                    due=due,
                    tags=tags or [],
                    priority=priority,
                    steps=steps or [],
                    notes=note_list,
                )
            )
            _save_board(project, board_title, columns)
            return f"Added '{title}' to '{col.name}'."
    return f"Column '{column}' not found. Available: {[c.name for c in columns]}"


def kanban_move(project: str, title: str, to_column: str) -> str:
    board = _load_board(project)
    if board is None:
        return f"No board found for project '{project}'. Create one first with kanban_create."
    board_title, columns = board
    card: Card | None = None
    for col in columns:
        for c in col.cards:
            if c.title.lower() == title.lower():
                card = c
                col.cards.remove(c)
                break
        if card:
            break

    if not card:
        return f"Card '{title}' not found."

    for col in columns:
        if col.name.lower() == to_column.lower():
            col.cards.append(card)
            _save_board(project, board_title, columns)
            return f"Moved '{card.title}' to '{col.name}'."

    return f"Column '{to_column}' not found. Available: {[c.name for c in columns]}"


def kanban_delete_card(project: str, title: str) -> str:
    board = _load_board(project)
    if board is None:
        return f"No board found for project '{project}'. Create one first with kanban_create."
    board_title, columns = board
    for col in columns:
        for card in col.cards:
            if card.title.lower() == title.lower():
                col.cards.remove(card)
                _save_board(project, board_title, columns)
                return f"Deleted '{card.title}' from '{col.name}'."
    return f"Card '{title}' not found."


def kanban_delete_column(project: str, column: str) -> str:
    board = _load_board(project)
    if board is None:
        return f"No board found for project '{project}'. Create one first with kanban_create."
    board_title, columns = board
    idx = next((i for i, c in enumerate(columns) if c.name.lower() == column.lower()), None)
    if idx is None:
        return f"Column '{column}' not found. Available: {[c.name for c in columns]}"
    col = columns[idx]
    if col.cards:
        card_titles = [c.title for c in col.cards]
        return f"Column '{col.name}' still has {len(col.cards)} card(s): {card_titles}. Move or delete them first."
    columns.pop(idx)
    _save_board(project, board_title, columns)
    return f"Deleted column '{col.name}'."


def kanban_update_card(
    project: str,
    title: str,
    new_title: str = "",
    description: str | None = None,
    due: str | None = None,
    tags: list[str] | None = None,
    priority: str | None = None,
    steps: list[str] | None = None,
    notes: list[str] | None = None,
) -> str:
    board = _load_board(project)
    if board is None:
        return f"No board found for project '{project}'. Create one first with kanban_create."
    if notes is not None:
        missing = _missing_notes(project, notes)
        if missing:
            return (
                f"Note(s) {missing} not found in project '{project}'. "
                "Write them first with note_write, or check note_list."
            )
    board_title, columns = board
    for col in columns:
        for card in col.cards:
            if card.title.lower() == title.lower():
                if new_title:
                    card.title = new_title
                if description is not None:
                    card.description = description
                if due is not None:
                    card.due = due
                if tags is not None:
                    card.tags = tags
                if priority is not None:
                    card.priority = priority
                if steps is not None:
                    card.steps = steps
                if notes is not None:
                    card.notes = notes
                _save_board(project, board_title, columns)
                return f"Updated '{card.title}'."
    return f"Card '{title}' not found."


def kanban_list() -> str:
    kanban_dir = STORAGE_ROOT / "kanban"
    if not kanban_dir.exists():
        return "No boards found."
    stems = sorted(p.stem for p in kanban_dir.glob("*.md"))
    if not stems:
        return "No boards found."
    return "\n".join(stems)


def kanban_delete(project: str) -> str:
    path = _kanban_path(project)
    if not path.exists():
        return f"No board found for project '{project}'."
    path.unlink()
    return f"Deleted board '{project}'."


def kanban_rename(project: str, new_name: str) -> str:
    path = _kanban_path(project)
    if not path.exists():
        return f"No board found for project '{project}'."

    # A pure re-casing/re-spelling of the same project (e.g. "JobSearch" ->
    # "jobsearch") must not be rejected as a collision with itself: _kanban_path
    # resolves by normalized slug, so checking it against the not-yet-unlinked
    # original file would otherwise always find that same file.
    same_project = _slugify(new_name) == _slugify(project)
    if not same_project and _kanban_path(new_name).exists():
        return f"Board '{new_name}' already exists."

    board_title, columns = _parse_board(path.read_text())
    path.unlink()
    _save_board(new_name, new_name, columns)

    # A genuine rename (not just a re-casing) also moves this project's notes
    # and decisions along with it — otherwise they stay filed under the old
    # name and become unreachable under the new one, splitting the project
    # right back into the disconnected-directories state this whole
    # normalized-resolution mechanism exists to prevent.
    if not same_project:
        for subsystem, dir_fn in (("notes", _notes_dir), ("decisions", _decisions_dir)):
            src = dir_fn(project)
            if not src.exists():
                continue
            dest = STORAGE_ROOT / subsystem / new_name
            if dest.exists():
                return (
                    f"Renamed board '{project}' to '{new_name}', but couldn't move "
                    f"{subsystem}/{src.name} — a directory named '{new_name}' already "
                    "exists there. Merge them manually."
                )
            src.rename(dest)

    return f"Renamed board '{project}' to '{new_name}'."


def kanban_search(query: str, project: str = "") -> str:
    kanban_dir = STORAGE_ROOT / "kanban"
    if not kanban_dir.exists():
        return "No boards found."
    files = [_kanban_path(project)] if project else sorted(kanban_dir.glob("*.md"))
    if project and not files[0].exists():
        return f"No board found for project '{project}'."
    query_lower = query.lower()
    matches = []
    for f in files:
        _, columns = _parse_board(f.read_text())
        for col in columns:
            for card in col.cards:
                if (
                    query_lower in card.title.lower()
                    or query_lower in card.description.lower()
                    or any(query_lower in t.lower() for t in card.tags)
                ):
                    matches.append(f"**{f.stem}** / {col.name} / {card.title}")
    return "\n".join(matches) if matches else f"No cards matching '{query}'."


def kanban_read(project: str) -> str:
    path = _kanban_path(project)
    if not path.exists():
        return f"No board found for project '{project}'."
    content = path.read_text()
    note_keys = sorted(p.stem for p in _notes_dir(project).glob("*.md"))
    if note_keys:
        content += f"\n---\nNotes in this project: {', '.join(note_keys)}\n"
    return content


# ── Notes ─────────────────────────────────────────────────────────────────────


def note_write(project: str, key: str, content: str, create: bool = False) -> str:
    error = _require_existing_project(project, create)
    if error:
        return error
    d = _notes_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    body = f"# {key}\n\n_Updated: {date.today().isoformat()}_\n\n{content}\n"
    fixed_body, remaining_issues = markdownlint.fix_content(body)
    (d / f"{key}.md").write_text(fixed_body)
    result = f"Written note '{key}' for project '{project}'."
    if remaining_issues:
        result += f"\n\nmarkdownlint issues:\n{markdownlint.format_issues(remaining_issues)}"
    return result


def note_read(project: str, key: str) -> str:
    path = _notes_dir(project) / f"{key}.md"
    if not path.exists():
        return f"Note '{key}' not found for project '{project}'."
    return path.read_text()


def note_list(project: str) -> str:
    d = _notes_dir(project)
    if not d.exists():
        return f"No notes found for project '{project}'."
    keys = sorted(p.stem for p in d.glob("*.md"))
    if not keys:
        return f"No notes found for project '{project}'."
    return "\n".join(keys)


def note_delete(project: str, key: str) -> str:
    path = _notes_dir(project) / f"{key}.md"
    if not path.exists():
        return f"Note '{key}' not found for project '{project}'."
    path.unlink()
    return f"Deleted note '{key}' from project '{project}'."


# ── Project listing ───────────────────────────────────────────────────────────


def project_list() -> str:
    """Enumerate every project directory across boards, notes, and decisions.

    Groups by normalized slug so near-duplicate spellings (e.g. 'JobSearch' vs
    'job-search') show up next to each other, flagged as a collision, instead
    of silently looking like two unrelated empty projects.
    """
    entries: dict[str, dict] = {}

    def entry_for(raw_name: str) -> dict:
        slug = _slugify(raw_name)
        e = entries.setdefault(
            slug, {"raw_names": set(), "board_name": None, "notes": 0, "decisions": 0}
        )
        e["raw_names"].add(raw_name)
        return e

    kanban_dir = STORAGE_ROOT / "kanban"
    if kanban_dir.exists():
        for p in kanban_dir.glob("*.md"):
            entry_for(p.stem)["board_name"] = p.stem

    notes_root = STORAGE_ROOT / "notes"
    if notes_root.exists():
        for d in notes_root.iterdir():
            if d.is_dir():
                entry_for(d.name)["notes"] = len(list(d.glob("*.md")))

    decisions_root = STORAGE_ROOT / "decisions"
    if decisions_root.exists():
        for d in decisions_root.iterdir():
            if d.is_dir():
                entry_for(d.name)["decisions"] = len(list(d.glob("*.md")))

    if not entries:
        return "No projects found."

    lines = []
    for slug in sorted(entries):
        e = entries[slug]
        display = e["board_name"] or sorted(e["raw_names"])[0]
        line = (
            f"{display:<20} board: {'yes' if e['board_name'] else 'no':<4} "
            f"notes: {e['notes']:<3} decisions: {e['decisions']:<3}"
        )
        if len(e["raw_names"]) > 1:
            line += f"  ⚠ collides: {sorted(e['raw_names'])}"
        lines.append(line)
    return "\n".join(lines)


# ── Project summary ───────────────────────────────────────────────────────────


def project_summary(project: str, days: int = 7) -> str:
    parts = [f"# {project}"]

    board = kanban_read(project)
    parts.append(f"## Kanban\n\n{board}")

    decisions = decision_read(project, days)
    parts.append(f"## Recent Decisions (last {days} days)\n\n{decisions}")

    notes_index = note_list(project)
    if "No notes" not in notes_index:
        note_sections = []
        for key in notes_index.splitlines():
            note_sections.append(f"### {key}\n\n{note_read(project, key)}")
        parts.append("## Notes\n\n" + "\n\n---\n\n".join(note_sections))

    return "\n\n---\n\n".join(parts)
