"""File-based storage under ~/.agent-memory/."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import os

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
        raise RuntimeError(
            f"{label}={str(path)!r} is not writable: {exc}"
        ) from exc


_env_dir = os.environ.get("AGENT_MEMORY_DIR")
if _env_dir:
    STORAGE_ROOT = Path(_env_dir)
    _check_storage_dir(STORAGE_ROOT, "AGENT_MEMORY_DIR")
else:
    STORAGE_ROOT = Path.home() / ".agent-memory"
    STORAGE_ROOT.mkdir(exist_ok=True)
    _check_storage_dir(STORAGE_ROOT, "AGENT_MEMORY_DIR")
_DEFAULT_COLUMNS = ["Backlog", "In Progress", "Done"]

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


# ── Paths ─────────────────────────────────────────────────────────────────────


def _decisions_dir(project: str) -> Path:
    return STORAGE_ROOT / "decisions" / project


def _kanban_path(project: str) -> Path:
    return STORAGE_ROOT / "kanban" / f"{project}.md"


# ── Decisions ─────────────────────────────────────────────────────────────────


def decision_log(project: str, summary: str, reasoning: str) -> str:
    d = _decisions_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date.today().isoformat()}.md"
    if not path.exists():
        header = _env.get_template("decision_header.md.j2").render(
            project=project,
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
        elif stripped == "- steps:":
            in_steps = True
        elif line.startswith("  ") and stripped:
            # preserve unknown fields (workload, defaultExpanded, …)
            extra_meta_lines.append(line)

    flush_card()
    return board_title, columns


def _render_board(title: str, columns: list[Column]) -> str:
    return _env.get_template("kanban_board.md.j2").render(title=title, columns=columns)


def _load_board(project: str) -> tuple[str, list[Column]]:
    path = _kanban_path(project)
    if not path.exists():
        return project, [Column(name=c) for c in _DEFAULT_COLUMNS]
    return _parse_board(path.read_text())


def _save_board(project: str, title: str, columns: list[Column]) -> None:
    path = _kanban_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_board(title, columns))


def kanban_add(
    project: str,
    column: str,
    title: str,
    description: str = "",
    due: str = "",
    tags: list[str] | None = None,
    priority: str = "",
    steps: list[str] | None = None,
) -> str:
    board_title, columns = _load_board(project)
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
                )
            )
            _save_board(project, board_title, columns)
            return f"Added '{title}' to '{col.name}'."
    return f"Column '{column}' not found. Available: {[c.name for c in columns]}"


def kanban_move(project: str, title: str, to_column: str) -> str:
    board_title, columns = _load_board(project)
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


def kanban_read(project: str) -> str:
    path = _kanban_path(project)
    if not path.exists():
        return f"No board found for project '{project}'."
    return path.read_text()
