"""File-based storage under ~/.agent-memory/."""

import contextlib
import difflib
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import os

from agent_memory_mcp import markdownlint


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` as one atomic replace, never a partial file.

    server.py's per-project lock only ever protects concurrent *writers*
    against each other — read-only tools (kanban_read, note_read, ...)
    deliberately take no lock at all, so a wedged write can't block reads for
    the same project forever. That's only safe because every write here goes
    through this helper: a reader can only ever observe the complete old
    file or the complete new one, never bytes from a write in progress.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


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
# slug and track the original spelling in a side file, the path helpers below
# *scan* the directory for an existing entry that normalizes to the same slug,
# and reuse it verbatim — casing and all — if found. A brand-new project is
# created using the exact spelling given. This means the on-disk name always IS
# the display name (nothing to keep in sync), at the cost of a directory
# listing per call — negligible at this scale (a handful of personal projects).


def _slugify(name: str) -> str:
    """Normalize a project name to a stable, casing/separator-insensitive slug
    for *comparison only* — never used as an on-disk name."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# ── Project paths ─────────────────────────────────────────────────────────────
#
# The layout is project-first: a project is one directory holding its board,
# notes and decisions, and projects can be grouped in nested folders. All
# projects live below <root>/projects; <root>/notes holds standalone notes
# that belong to no project.
#
#   <root>/projects/Work/AzureAIConsultantTraining/board.md
#   <root>/projects/Work/AzureAIConsultantTraining/notes/<key>.md
#   <root>/projects/Work/AzureAIConsultantTraining/decisions/YYYY-MM-DD.md
#   <root>/notes/<key>.md
#
# A project is identified by a path of 1..MAX_DEPTH segments separated by "/".
# A single-segment name is free text as before; segments of a nested path are
# restricted to [A-Za-z0-9._-]. Names are matched by slug one segment at a
# time (casing and separators ignored) and an existing entry is reused with
# its on-disk spelling.

BOARD_FILE = "board.md"
NOTES_DIR = "notes"
DECISIONS_DIR = "decisions"
PROJECTS_DIR = "projects"
# Top-level directories of the storage root; anything else visible there
# belongs to an older layout.
_TOP_LEVEL_DIRS = (PROJECTS_DIR, NOTES_DIR)

# These names would be ambiguous with the entries of a project, so they are
# rejected at every level.
RESERVED_NAMES = frozenset({"board", NOTES_DIR, DECISIONS_DIR})

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _read_max_depth() -> int:
    raw = os.environ.get("AGENT_MEMORY_MAX_DEPTH")
    if not raw:
        return 5
    try:
        depth = int(raw)
    except ValueError:
        depth = 0
    if depth < 1:
        raise RuntimeError(f"AGENT_MEMORY_MAX_DEPTH={raw!r} must be an integer >= 1.")
    return depth


MAX_DEPTH = _read_max_depth()


class ProjectPathError(ValueError):
    """A project name is not a valid path, or would make a path both a folder
    and a project. The server turns this into a plain error message."""


class LegacyLayoutError(ProjectPathError):
    """The storage root still uses the old type-first layout."""


def _split_project(project: str) -> list[str]:
    """Validate `project` and return its segments."""
    if not project or not project.strip():
        raise ProjectPathError("Project name must not be empty.")
    if "/" not in project:
        # Single names are free text (spaces etc. keep working).
        if project in (".", "..") or "\\" in project or "\0" in project:
            raise ProjectPathError(f"Invalid project name '{project}'.")
        segments = [project]
    else:
        segments = project.split("/")
        for seg in segments:
            if not seg:
                raise ProjectPathError(
                    f"Invalid project path '{project}': empty segment "
                    "(no leading, trailing or doubled '/')."
                )
            if seg in (".", ".."):
                raise ProjectPathError(f"Invalid project path '{project}': '{seg}' is not allowed.")
            if not _SEGMENT_RE.match(seg):
                raise ProjectPathError(
                    f"Invalid project path '{project}': segment '{seg}' may only "
                    "contain letters, digits, '.', '_' and '-'."
                )
    if len(segments) > MAX_DEPTH:
        raise ProjectPathError(
            f"Project path '{project}' is {len(segments)} levels deep; the limit is {MAX_DEPTH} "
            "(AGENT_MEMORY_MAX_DEPTH)."
        )
    for seg in segments:
        if not _slugify(seg):
            raise ProjectPathError(
                f"Invalid project name '{project}': segment '{seg}' needs a letter or digit."
            )
        if seg.startswith("."):
            raise ProjectPathError(
                f"Invalid project name '{project}': '{seg}' must not start with '.'."
            )
        if seg.lower().endswith(".md"):
            raise ProjectPathError(
                f"Invalid project name '{project}': '{seg}' must not end in .md."
            )
        if _slugify(seg) in RESERVED_NAMES:
            raise ProjectPathError(
                f"'{seg}' is reserved (it names an entry of a project); pick another name."
            )
    return segments


def _project_slug(project: str) -> str:
    """Per-segment slug of a project path, used to compare and lock projects.
    'Work/Example' and 'WorkExample' are different projects."""
    return "/".join(_slugify(seg) for seg in _split_project(project))


def _projects_root() -> Path:
    return STORAGE_ROOT / PROJECTS_DIR


def _standalone_notes_dir() -> Path:
    return STORAGE_ROOT / NOTES_DIR


def _old_layout_parts() -> list[str]:
    """What at the storage root does not belong to the current layout."""
    found = []
    try:
        top = sorted(p for p in STORAGE_ROOT.iterdir() if not p.name.startswith("."))
    except FileNotFoundError:
        return []
    for entry in top:
        if not entry.is_dir() or entry.name == PROJECTS_DIR:
            continue
        if entry.name == NOTES_DIR:
            # Standalone notes are plain files; subdirectories are the old layout.
            if _visible_subdirs(entry):
                found.append(entry.name)
        elif any(not child.name.startswith(".") for child in entry.iterdir()):
            found.append(entry.name)
    return found


def _assert_current_layout() -> None:
    """Refuse to work on a storage root that still has an older layout, so old
    and new data can never end up side by side."""
    found = _old_layout_parts()
    if found:
        raise LegacyLayoutError(
            f"The storage at {STORAGE_ROOT} still has an older layout ({', '.join(found)} at the "
            f"top level, outside {PROJECTS_DIR}/ and {NOTES_DIR}/). Restore a backup or move "
            "the folders by hand so the root holds only projects/ and notes/; nothing was "
            "read or written."
        )


def _find_existing(base_dir: Path, name: str) -> Path | None:
    """Look for an existing directory directly under `base_dir` whose name
    normalizes to the same slug as `name`, returning it with its on-disk
    casing intact. Hidden entries (.vscode, ...) never match."""
    if not base_dir.is_dir():
        return None
    slug = _slugify(name)
    return next(
        (
            p
            for p in sorted(base_dir.iterdir())
            if p.is_dir() and not p.name.startswith(".") and _slugify(p.name) == slug
        ),
        None,
    )


def _project_dir(project: str) -> Path:
    """The directory of `project`, resolved one segment at a time. Every
    segment reuses an existing directory with the same slug; a missing one is
    spelled as given. The directory itself may not exist yet."""
    segments = _split_project(project)
    _assert_current_layout()
    current = _projects_root()
    for seg in segments:
        current = _find_existing(current, seg) or current / seg
    # Names are validated, so this only trips on a symlink pointing out of the root.
    try:
        current.resolve().relative_to(STORAGE_ROOT.resolve())
    except ValueError:
        raise ProjectPathError(f"Project '{project}' resolves outside the storage root.") from None
    return current


def _visible_subdirs(d: Path) -> list[Path]:
    try:
        return sorted(c for c in d.iterdir() if c.is_dir() and not c.name.startswith("."))
    except (FileNotFoundError, NotADirectoryError):
        return []


def _is_project_dir(d: Path) -> bool:
    return d.is_dir() and (
        (d / BOARD_FILE).is_file() or (d / NOTES_DIR).is_dir() or (d / DECISIONS_DIR).is_dir()
    )


def _child_dirs(d: Path) -> list[Path]:
    """Sub-directories of `d` other than a project's own notes/decisions."""
    return [c for c in _visible_subdirs(d) if c.name.lower() not in (NOTES_DIR, DECISIONS_DIR)]


def _project_exists(project: str) -> bool:
    return _is_project_dir(_project_dir(project))


def _is_folder(project: str) -> bool:
    """True if `project` is a path that only groups other paths."""
    d = _project_dir(project)
    return d.is_dir() and not _is_project_dir(d) and bool(_child_dirs(d))


def _assert_can_create(project: str) -> None:
    """Refuse to create `project` where it would be both a folder and a project."""
    segments = _split_project(project)
    if _is_folder(project):
        raise ProjectPathError(
            f"'{project}' is a folder (it holds other projects); it can't also be a project."
        )
    for i in range(1, len(segments)):
        parent = "/".join(segments[:i])
        if _project_exists(parent):
            raise ProjectPathError(
                f"'{parent}' is a project; it can't also be a folder for '{project}'."
            )


def _prune_empty_parents(path: Path, stop: Path | None = None) -> None:
    """Remove empty directories from `path` upwards, never `stop` itself."""
    stop = stop or _projects_root()
    while path != stop and stop in path.parents:
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


@dataclass
class _Discovered:
    raw_names: set[str] = field(default_factory=set)
    path: Path | None = None
    has_board: bool = False
    notes: int | None = None  # None: the project has no notes directory
    decisions: int | None = None
    mixed: bool = False  # a project that also holds other projects


def _walk(directory: Path, inside_project: bool) -> Iterator[Path]:
    """Every project directory below `directory`."""
    for child in _visible_subdirs(directory):
        if inside_project and child.name.lower() in (NOTES_DIR, DECISIONS_DIR):
            continue
        is_project = _is_project_dir(child)
        if is_project:
            yield child
        yield from _walk(child, inside_project=is_project)


def _discover() -> dict[str, _Discovered]:
    """Every project, keyed by its per-segment slug path."""
    _assert_current_layout()
    entries: dict[str, _Discovered] = {}
    for d in _walk(_projects_root(), inside_project=False):
        rel = d.relative_to(_projects_root()).as_posix()
        key = "/".join(_slugify(seg) for seg in rel.split("/"))
        entry = entries.setdefault(key, _Discovered())
        entry.raw_names.add(rel)
        entry.path = entry.path or d
        entry.has_board = entry.has_board or (d / BOARD_FILE).is_file()
        if (d / NOTES_DIR).is_dir():
            entry.notes = (entry.notes or 0) + len(list((d / NOTES_DIR).glob("*.md")))
        if (d / DECISIONS_DIR).is_dir():
            entry.decisions = (entry.decisions or 0) + len(list((d / DECISIONS_DIR).glob("*.md")))
        entry.mixed = entry.mixed or bool(_child_dirs(d))
    return entries


def _suggest_project(project: str) -> str | None:
    """Suggest an existing project's on-disk spelling for a likely typo/casing miss."""
    known_names: set[str] = set()
    for entry in _discover().values():
        known_names |= entry.raw_names

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
    return _project_dir(project) / DECISIONS_DIR


def _kanban_path(project: str) -> Path:
    return _project_dir(project) / BOARD_FILE


def _notes_dir(project: str) -> Path:
    return _project_dir(project) / NOTES_DIR


def _project_label(project_dir: Path) -> str:
    return project_dir.relative_to(_projects_root()).as_posix()


# ── Decisions ─────────────────────────────────────────────────────────────────


def decision_log(project: str, summary: str, reasoning: str, create: bool = False) -> str:
    error = _require_existing_project(project, create)
    if error:
        return error
    if not _project_exists(project):
        _assert_can_create(project)
    d = _decisions_dir(project)
    display_name = _project_label(d.parent)  # the existing directory's on-disk spelling
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date.today().isoformat()}.md"
    if path.exists():
        existing = path.read_text()
    else:
        existing = _env.get_template("decision_header.md.j2").render(
            project=display_name,
            date=date.today().isoformat(),
        )
    entry = _env.get_template("decision_entry.md.j2").render(
        time=datetime.now().strftime("%H:%M"),
        summary=summary,
        reasoning=reasoning,
    )
    # A plain append (the previous approach) isn't atomic from a lock-free
    # reader's point of view — a read could observe a half-written entry
    # mid-append. Composing the full content and replacing the file in one
    # atomic write (see _atomic_write_text) means a reader only ever sees
    # the complete file before or after this entry, never a partial one.
    _atomic_write_text(path, existing + entry)
    return f"Logged to {path}"


def decision_read(project: str | None = None, days: int = 7) -> str:
    cutoff = date.today() - timedelta(days=days)

    if project:
        d = _decisions_dir(project)
        if not d.is_dir():
            return f"No decisions found for project '{project}'."
        roots = [(_project_label(d.parent), d)]
    else:
        roots = [
            (_display_name(e), e.path / DECISIONS_DIR)
            for _, e in sorted(_discover().items())
            if e.path is not None and e.decisions is not None
        ]
        if not roots:
            return "No decisions found."

    results = []
    for label, root in roots:
        for f in sorted(root.glob("*.md")):
            try:
                file_date = date.fromisoformat(f.stem)
            except ValueError:
                continue
            if file_date < cutoff:
                continue
            try:
                content = f.read_text()
            except FileNotFoundError:
                # Read-only calls take no lock (see server.py), so a file
                # listed by glob() a moment ago could have been renamed/moved
                # away by a concurrent write since — treat it like it was
                # never there rather than letting the read crash.
                continue
            results.append(f"### {label} / {f.stem}\n\n{content}")

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
            raw = stripped[9:].strip()
            if raw.startswith("[") and raw.endswith("]"):
                raw = raw[1:-1]
            # Notes render as markdown links; boards written before that still
            # hold bare keys, so both shapes have to parse back to the key.
            links = re.findall(r"\[([^\]]+)\]\([^)]*\)", raw)
            if links:
                current_card.notes = links
            else:
                current_card.notes = [n.strip() for n in raw.split(",") if n.strip()]
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
    _assert_can_create(project)
    _save_board(project, project, [Column(name=c) for c in columns])
    return f"Created board '{project}' with columns: {columns}."


def _save_board(project: str, title: str, columns: list[Column]) -> None:
    path = _kanban_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, _render_board(title, columns))


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
    names = sorted(_display_name(e) for e in _discover().values() if e.has_board)
    if not names:
        return "No boards found."
    return "\n".join(names)


def kanban_delete(project: str) -> str:
    path = _kanban_path(project)
    if not path.exists():
        return f"No board found for project '{project}'."
    path.unlink()
    _prune_empty_parents(path.parent)
    return f"Deleted board '{project}'."


def kanban_rename(project: str, new_name: str) -> str:
    """Rename a project (board, notes and decisions) inside its folder."""
    if "/" in new_name:
        return (
            "new_name must be a single name without '/'. "
            "Use project_move to move a project to another folder."
        )
    path = _kanban_path(project)
    if not path.exists():
        return f"No board found for project '{project}'."

    segments = _split_project(project)
    target = "/".join([*segments[:-1], new_name])
    _split_project(target)
    src = _project_dir(project)

    if _slugify(new_name) == _slugify(segments[-1]):
        # A pure re-casing/re-spelling. On a case-insensitive filesystem
        # (macOS) the old and the new name are the same entry, so go through
        # a temporary name for the new spelling to take effect.
        literal = src.parent / new_name
        tmp = src.with_name(f".rename-{os.getpid()}-{src.name}")
        _rename(src, tmp)
        try:
            _rename(tmp, literal)
        except OSError:
            _rename(tmp, src)
            raise
        _retitle_board(literal, project, target)
        return f"Renamed board '{project}' to '{new_name}'."

    if _project_exists(target):
        return f"Board '{new_name}' already exists."
    _assert_can_create(target)
    error = _move_project_dir(project, target)
    if error:
        return f"Rename of '{project}' failed ({error}); nothing changed."
    return f"Renamed board '{project}' to '{new_name}'."


def kanban_search(query: str, project: str = "") -> str:
    if project:
        path = _kanban_path(project)
        if not path.exists():
            return f"No board found for project '{project}'."
        files = [(project, path)]
    else:
        files = [
            (_display_name(e), e.path / BOARD_FILE)
            for _, e in sorted(_discover().items())
            if e.path is not None and e.has_board
        ]
        if not files:
            return "No boards found."
    query_lower = query.lower()
    matches = []
    for label, f in files:
        try:
            content = f.read_text()
        except FileNotFoundError:
            # Read-only calls take no lock (see server.py), so a board listed
            # a moment ago could have been renamed/deleted by a concurrent
            # write since — skip it rather than letting the read crash.
            continue
        _, columns = _parse_board(content)
        for col in columns:
            for card in col.cards:
                if (
                    query_lower in card.title.lower()
                    or query_lower in card.description.lower()
                    or any(query_lower in t.lower() for t in card.tags)
                ):
                    matches.append(f"**{label}** / {col.name} / {card.title}")
    return "\n".join(matches) if matches else f"No cards matching '{query}'."


def kanban_read(project: str) -> str:
    path = _kanban_path(project)
    try:
        content = path.read_text()
    except FileNotFoundError:
        # Read-only calls take no lock (see server.py), so this can race a
        # concurrent kanban_rename/kanban_delete for the same project — a
        # plain exists()-then-read_text() has a gap where the file could
        # vanish in between; read directly and treat a miss as "not found."
        return f"No board found for project '{project}'."
    note_keys = sorted(p.stem for p in _notes_dir(project).glob("*.md"))
    if note_keys:
        content += f"\n---\nNotes in this project: {', '.join(note_keys)}\n"
    return content


# ── Notes ─────────────────────────────────────────────────────────────────────


def _validate_note_key(key: str) -> None:
    if (
        not key
        or not key.strip()
        or "/" in key
        or "\\" in key
        or "\0" in key
        or key.startswith(".")
    ):
        raise ProjectPathError(
            f"Invalid note key '{key}': use a plain name without '/' and without a leading '.'."
        )


def _notes_target(project: str | None) -> tuple[Path, str]:
    """The notes directory and a description for messages: a project's own
    notes, or the standalone notes when `project` is empty."""
    if project:
        return _notes_dir(project), f"for project '{project}'"
    _assert_current_layout()
    return _standalone_notes_dir(), "(standalone)"


def note_write(project: str, key: str, content: str, create: bool = False) -> str:
    """Write a note. An empty `project` writes a standalone note."""
    _validate_note_key(key)
    if project:
        error = _require_existing_project(project, create)
        if error:
            return error
        if not _project_exists(project):
            _assert_can_create(project)
    d, where = _notes_target(project)
    d.mkdir(parents=True, exist_ok=True)
    body = f"# {key}\n\n_Updated: {date.today().isoformat()}_\n\n{content}\n"
    fixed_body, remaining_issues = markdownlint.fix_content(body)
    _atomic_write_text(d / f"{key}.md", fixed_body)
    result = f"Written note '{key}' {where}."
    if remaining_issues:
        result += f"\n\nmarkdownlint issues:\n{markdownlint.format_issues(remaining_issues)}"
    return result


def note_read(project: str, key: str) -> str:
    _validate_note_key(key)
    d, where = _notes_target(project)
    try:
        return (d / f"{key}.md").read_text()
    except FileNotFoundError:
        return f"Note '{key}' not found {where}."


def note_list(project: str) -> str:
    d, where = _notes_target(project)
    keys = sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []
    if not keys:
        return f"No notes found {where}."
    return "\n".join(keys)


def note_delete(project: str, key: str) -> str:
    _validate_note_key(key)
    d, where = _notes_target(project)
    path = d / f"{key}.md"
    if not path.exists():
        return f"Note '{key}' not found {where}."
    path.unlink()
    return f"Deleted note '{key}' {where}."


# ── Project listing ───────────────────────────────────────────────────────────


def _folder_key(folder: str) -> str:
    return "/".join(_slugify(seg) for seg in folder.strip("/").split("/"))


def _display_name(entry: _Discovered) -> str:
    return sorted(entry.raw_names)[0]


def _kind(entry: _Discovered) -> str:
    """What a project has, in words."""
    parts = []
    if entry.has_board:
        parts.append("board")
    if entry.notes is not None:
        parts.append(f"notes ({entry.notes})")
    if entry.decisions is not None:
        parts.append(f"decisions ({entry.decisions})")
    if len(parts) == 1:
        return parts[0] + " only" if parts[0] == "board" else parts[0].replace(" (", " only (")
    return " + ".join(parts)


def project_list(folder: str = "") -> str:
    """Enumerate every project as a tree of folders, saying what each has.

    Groups by normalized per-segment slug so near-duplicate spellings (e.g.
    'JobSearch' vs 'job-search') show up next to each other, flagged as a
    collision, instead of silently looking like two unrelated empty projects.
    `folder` limits the listing to what is below that folder.
    """
    entries = _discover()
    prefix = _folder_key(folder) if folder.strip("/") else ""
    if prefix:
        entries = {k: v for k, v in entries.items() if k.startswith(prefix + "/")}

    standalone = _standalone_notes_dir()
    standalone_count = len(list(standalone.glob("*.md"))) if standalone.is_dir() else 0
    footer = (
        [f"\nStandalone notes: {standalone_count} (note_list with an empty project)"]
        if standalone_count and not prefix
        else []
    )

    if not entries:
        if prefix:
            return f"No projects found in folder '{folder}'."
        return "\n".join(["No projects found.", *footer]).strip()

    lines = []
    printed_folders: set[str] = set()
    for key in sorted(entries):
        e = entries[key]
        parts = _display_name(e).split("/")
        key_parts = key.split("/")
        for depth in range(len(parts) - 1):
            folder_key = "/".join(key_parts[: depth + 1])
            if folder_key not in printed_folders and folder_key not in entries:
                printed_folders.add(folder_key)
                lines.append(f"{'  ' * depth}{parts[depth]}/")
        indent = "  " * (len(parts) - 1)
        line = f"{indent}{parts[-1]:<{max(20 - len(indent), 1)}} {_kind(e)}"
        if e.mixed:
            line += "  ⚠ also holds other projects (a path should be a folder or a project)"
        if len(e.raw_names) > 1:
            line += f"  ⚠ collides: {sorted(e.raw_names)}"
        lines.append(line)
    return "\n".join([*lines, *footer])


def folder_list() -> str:
    """List every folder with the number of projects below it."""
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for key, entry in sorted(_discover().items()):
        parts = _display_name(entry).split("/")
        key_parts = key.split("/")
        for depth in range(len(parts) - 1):
            folder_key = "/".join(key_parts[: depth + 1])
            counts[folder_key] = counts.get(folder_key, 0) + 1
            names.setdefault(folder_key, "/".join(parts[: depth + 1]))
    if not counts:
        return "No folders found."
    return "\n".join(f"{names[k]:<30} {counts[k]} project(s)" for k in sorted(counts))


# ── Project summary ───────────────────────────────────────────────────────────


def _single_project_summary(project: str, days: int) -> str:
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


def project_summary(project: str, days: int = 7) -> str:
    """Summary of one project; for a folder, the summaries of the projects below it."""
    _split_project(project)
    if not _project_exists(project) and _is_folder(project):
        prefix = _folder_key(project)
        below = [
            _display_name(e) for k, e in sorted(_discover().items()) if k.startswith(prefix + "/")
        ]
        if below:
            sections = [_single_project_summary(name, days) for name in below]
            header = f"# {project} (folder, {len(below)} project(s))"
            return header + "\n\n====\n\n" + "\n\n====\n\n".join(sections)
    return _single_project_summary(project, days)


# ── Moving projects ───────────────────────────────────────────────────────────


def _rename(src: Path, dst: Path) -> None:
    """The one place a project directory changes location (tests patch this)."""
    os.rename(src, dst)


def _retitle_board(project_dir: Path, old: str, new: str) -> None:
    """Give a board that still carries the old project name the new one.
    Cosmetic, so a failure here never undoes the move."""
    board = project_dir / BOARD_FILE
    try:
        title, columns = _parse_board(board.read_text())
        if _slugify(title) == _slugify(old):
            _atomic_write_text(board, _render_board(new, columns))
    except OSError:
        pass


def _move_project_dir(source: str, target: str) -> str | None:
    """Rename the directory of `source` to `target`: one atomic rename, so the
    project is either at the old or at the new path. Returns an error text or None."""
    src = _project_dir(source)
    dst = _project_dir(target)
    missing = []
    parent = dst.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _rename(src, dst)
    except OSError as exc:
        for d in missing:
            with contextlib.suppress(OSError):
                d.rmdir()
        return str(exc)
    _prune_empty_parents(src.parent)
    _retitle_board(dst, source, target)
    return None


def _describe_project(project_dir: Path) -> list[str]:
    lines = []
    if (project_dir / BOARD_FILE).is_file():
        lines.append("- board")
    for label, sub in (("notes", NOTES_DIR), ("decisions", DECISIONS_DIR)):
        d = project_dir / sub
        if d.is_dir():
            names = sorted(p.stem for p in d.glob("*.md"))
            lines.append(f"- {label} ({len(names)}): {', '.join(names) if names else '-'}")
    return lines


def project_move(source: str, target: str, dry_run: bool = True) -> str:
    """Move a project (board, notes, decisions) to another path in one rename."""
    _split_project(source)
    _split_project(target)
    if not _project_exists(source):
        if _is_folder(source):
            return f"'{source}' is a folder; project_move moves projects, not folders."
        return f"No project '{source}' to move."
    if _project_slug(source) == _project_slug(target):
        return (
            f"'{source}' and '{target}' are the same project (only the spelling differs); "
            "use kanban_rename to change the spelling."
        )
    if _project_exists(target):
        return f"Target '{target}' already exists."
    _assert_can_create(target)

    summary = _describe_project(_project_dir(source))
    if dry_run:
        return (
            f"Dry run: would move '{source}' to '{target}':\n"
            + "\n".join(summary)
            + "\n\nNothing was changed. Pass dry_run=false to move."
        )
    error = _move_project_dir(source, target)
    if error:
        return f"Move of '{source}' to '{target}' failed ({error}); nothing changed."
    return f"Moved project '{source}' to '{target}'.\n" + "\n".join(summary)
