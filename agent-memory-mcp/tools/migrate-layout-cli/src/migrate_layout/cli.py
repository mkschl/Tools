"""Move an agent-memory storage root to the current layout.

Two older layouts are handled:

- type-first: kanban/, notes/ and decisions/ at the root, one tree per kind;
- projects at the root: one directory per project directly below the root.

The current layout keeps every project below <root>/projects/ (one directory
with board.md, notes/ and decisions/) and leaves <root>/notes/ for notes that
belong to no project.
"""

import argparse
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BOARD_FILE = "board.md"
NOTES_DIR = "notes"
DECISIONS_DIR = "decisions"
PROJECTS_DIR = "projects"
OLD_DIRS = ("kanban", NOTES_DIR, DECISIONS_DIR)
CURRENT_TOP_LEVEL = (PROJECTS_DIR, NOTES_DIR)
RESERVED = frozenset({"board", NOTES_DIR, DECISIONS_DIR})
SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
OLD_NOTE_LINK_RE = re.compile(r"\]\(\.\./notes/(?:[^)]*/)?([^/)]+\.md)\)")
TITLE_RE = re.compile(r"^# (.*)$", re.MULTILINE)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def slug_path(path: str) -> str:
    return "/".join(slugify(seg) for seg in path.split("/"))


@dataclass
class OldProject:
    name: str  # path in the old layout, e.g. "JobSearch" or "JobSearch/eigenblue"
    board: Path | None = None
    notes: Path | None = None  # directory whose own files belong to this project
    decisions: Path | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return slug_path(self.name)


@dataclass
class Move:
    old: OldProject
    target: str  # canonical new path


def visible_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))


def visible_dirs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))


def discover(root: Path) -> dict[str, OldProject]:
    """The projects of the old layout, keyed by their slug path."""
    found: dict[str, OldProject] = {}

    def project_for(name: str) -> OldProject:
        return found.setdefault(slug_path(name), OldProject(name=name))

    kanban = root / "kanban"
    if kanban.is_dir():
        for board in sorted(kanban.rglob("*.md")):
            if any(part.startswith(".") for part in board.relative_to(kanban).parts):
                continue
            name = board.relative_to(kanban).with_suffix("").as_posix()
            project = project_for(name)
            if project.board is not None:
                project.problems.append(f"two boards for '{name}'")
            project.board = board

    for kind in (NOTES_DIR, DECISIONS_DIR):
        base = root / kind
        if not base.is_dir():
            continue
        for directory in sorted(d for d in base.rglob("*") if d.is_dir()):
            rel = directory.relative_to(base)
            if any(part.startswith(".") for part in rel.parts):
                continue
            own_files = visible_files(directory)
            is_leaf = not visible_dirs(directory)
            if not own_files and not is_leaf:
                continue  # only groups other directories
            project = project_for(rel.as_posix())
            if getattr(project, kind) is not None:
                project.problems.append(f"two {kind} directories for '{rel.as_posix()}'")
            setattr(project, kind, directory)
    return found


def validate_target(path: str, max_depth: int) -> str | None:
    """Return a problem description for an invalid target path, or None."""
    segments = path.split("/")
    if len(segments) > max_depth:
        return f"'{path}' is {len(segments)} levels deep (limit {max_depth})"
    for seg in segments:
        if not seg or seg in (".", ".."):
            return f"'{path}' has an empty or relative segment"
        if len(segments) > 1 and not SEGMENT_RE.match(seg):
            return f"segment '{seg}' of '{path}' may only contain letters, digits, '.', '_', '-'"
        if seg.startswith(".") or seg.lower().endswith(".md") or not slugify(seg):
            return f"segment '{seg}' of '{path}' is not a valid name"
        if slugify(seg) in RESERVED:
            return f"segment '{seg}' of '{path}' is reserved"
    return None


def load_mapping(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    projects = data.get("projects", {})
    if not isinstance(projects, dict) or not all(isinstance(v, str) for v in projects.values()):
        raise SystemExit(f"{path}: [projects] must map old names to new path strings.")
    return {str(k): v.strip("/") for k, v in projects.items()}


def canonicalize(targets: list[str]) -> list[str]:
    """Give every directory the spelling it has the first time it appears, so
    'Health/x' and 'health/y' end up in one folder."""
    spelling: dict[str, str] = {}
    result = []
    for target in targets:
        segments = target.split("/")
        fixed = []
        for i, seg in enumerate(segments):
            key = "/".join(slugify(s) for s in segments[: i + 1])
            fixed.append(spelling.setdefault(key, seg))
        result.append("/".join(fixed))
    return result


def existing_dir(root: Path, target: str) -> Path | None:
    """The directory `target` already has in the root (matched by slug), if any."""
    current = root
    for seg in target.split("/"):
        match = next(
            (
                d
                for d in visible_dirs(current)
                if slugify(d.name) == slugify(seg) and d.name not in OLD_DIRS
            ),
            None,
        )
        if match is None:
            return None
        current = match
    return current


def plan(
    root: Path, mapping: dict[str, str], max_depth: int
) -> tuple[list[Move], list[str], list[str]]:
    """Return (moves, problems, warnings)."""
    old = discover(root)
    problems: list[str] = []
    warnings: list[str] = []
    mapped = {slug_path(k): v for k, v in mapping.items()}

    for key, value in mapped.items():
        if key not in old:
            warnings.append(f"mapping entry '{key}' matches no project (ignored)")
        elif (issue := validate_target(value, max_depth)) is not None:
            problems.append(f"mapping for '{old[key].name}': {issue}")

    ordered = sorted(old.values(), key=lambda p: p.key)
    raw_targets = [mapped.get(p.key, p.name) for p in ordered]
    for project, target in zip(ordered, raw_targets, strict=True):
        problems.extend(f"{project.name}: {p}" for p in project.problems)
        if project.key not in mapped and (issue := validate_target(target, max_depth)):
            problems.append(f"'{project.name}' keeps its path but {issue}")
    if problems:
        return [], problems, warnings

    targets = canonicalize(raw_targets)
    moves = [Move(p, t) for p, t in zip(ordered, targets, strict=True)]

    seen: dict[str, Move] = {}
    for move in moves:
        key = slug_path(move.target)
        if key in seen:
            problems.append(
                f"'{seen[key].old.name}' and '{move.old.name}' would both become '{move.target}'"
            )
        seen[key] = move
    for move in moves:
        key = slug_path(move.target)
        for other_key, other in seen.items():
            if other_key.startswith(key + "/"):
                problems.append(
                    f"'{move.target}' (from '{move.old.name}') would be both a project and a "
                    f"folder for '{other.target}'; give the files of '{move.old.name}' their own "
                    "path in the mapping"
                )
                break
        existing = existing_dir(root / PROJECTS_DIR, move.target)
        if existing is not None and any(existing.iterdir()):
            problems.append(f"target '{move.target}' already exists in the root")
    return moves, problems, warnings


def describe(move: Move) -> list[str]:
    lines = [f"{move.old.name}  ->  {move.target}"]
    if move.old.board is not None:
        lines.append("    board")
    for label, directory in (("notes", move.old.notes), ("decisions", move.old.decisions)):
        if directory is None:
            continue
        names = [f.stem for f in visible_files(directory)]
        shown = ", ".join(names) if names else "empty directory, dropped"
        lines.append(f"    {label} ({len(names)}): {shown}")
    return lines


def rewrite_board(text: str, old_name: str, new_path: str) -> str:
    text = OLD_NOTE_LINK_RE.sub(r"](notes/\1)", text)
    match = TITLE_RE.search(text)
    if match and slugify(match.group(1)) == slugify(old_name):
        text = text[: match.start(1)] + new_path + text[match.end(1) :]
    return text


def count_files(root: Path, exclude: Path | None = None) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not (exclude and Path(dirpath, d) == exclude)]
        total += sum(1 for f in filenames if not f.startswith("."))
    return total


def remove_empty_dirs(base: Path) -> None:
    """Remove empty directories below and including `base` (ignoring .DS_Store)."""
    if not base.is_dir():
        return
    for child in visible_dirs(base):
        remove_empty_dirs(child)
    leftovers = [p for p in base.iterdir() if p.name != ".DS_Store"]
    if not leftovers:
        (base / ".DS_Store").unlink(missing_ok=True)
        base.rmdir()


def apply_move(root: Path, move: Move) -> None:
    dst = root.joinpath(PROJECTS_DIR, *move.target.split("/"))
    dst.mkdir(parents=True, exist_ok=True)
    old = move.old
    if old.board is not None:
        text = rewrite_board(old.board.read_text(encoding="utf-8"), old.name, move.target)
        (dst / BOARD_FILE).write_text(text, encoding="utf-8")
        old.board.unlink()
    for sub, directory in ((NOTES_DIR, old.notes), (DECISIONS_DIR, old.decisions)):
        if directory is None:
            continue
        files = visible_files(directory)
        if not files:
            continue
        (dst / sub).mkdir(exist_ok=True)
        for f in files:
            shutil.move(str(f), str(dst / sub / f.name))


def type_first_present(root: Path) -> bool:
    """True if the root still has the kanban/notes/decisions trees."""
    for name in ("kanban", DECISIONS_DIR):
        d = root / name
        if d.is_dir() and any(not p.name.startswith(".") for p in d.iterdir()):
            return True
    notes = root / NOTES_DIR
    return notes.is_dir() and bool(visible_dirs(notes))


def stray_project_dirs(root: Path) -> list[Path]:
    """Directories at the root that should be below projects/."""
    return [
        d
        for d in visible_dirs(root)
        if d.name not in CURRENT_TOP_LEVEL
        and d.name not in ("kanban", DECISIONS_DIR)
        and any(True for _ in d.iterdir())
    ]


def make_backup(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root.with_name(f"{root.name}-backup-{stamp}")
    shutil.copytree(root, backup_dir)
    print(f"\nBackup written to {backup_dir}")
    return backup_dir


def run_projects_at_root(root: Path, apply: bool, backup: bool) -> int:
    """Move project directories that sit at the root into projects/."""
    strays = stray_project_dirs(root)
    projects = root / PROJECTS_DIR
    problems = []
    if projects.exists() and any(True for _ in projects.iterdir()):
        problems.append(
            f"{PROJECTS_DIR}/ already exists and is not empty; merge by hand, nothing was changed"
        )
    for d in strays:
        if slugify(d.name) in RESERVED:
            problems.append(f"'{d.name}' is a reserved name and cannot be a folder of projects")
    if problems:
        print("Problems found, nothing was changed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    for d in strays:
        print(f"{d.name}  ->  {PROJECTS_DIR}/{d.name}")
    if not apply:
        print(f"\nDry run: {len(strays)} folder(s) would move into {PROJECTS_DIR}/.")
        print("Nothing was changed. Re-run with --apply to migrate.")
        return 0
    if backup:
        make_backup(root)
    before = count_files(root)
    projects.mkdir(exist_ok=True)
    for d in strays:
        os.rename(d, projects / d.name)
    after = count_files(root)
    print(
        f"\nMoved {len(strays)} folder(s) into {PROJECTS_DIR}/. Files before: {before}, after: {after}."
    )
    if before != after:
        print("The counts do not match; compare with the backup.")
        return 1
    return 0


def run(root: Path, mapping: dict[str, str], apply: bool, backup: bool, max_depth: int) -> int:
    old_style = type_first_present(root)
    strays = stray_project_dirs(root)
    if old_style and strays:
        print(
            "Problems found, nothing was changed:\n"
            f"  - the root has both the type-first trees and project folders at the top "
            f"({', '.join(d.name for d in strays)}); migrate one layout at a time"
        )
        return 1
    if not old_style and not strays:
        print(f"{root}: nothing to migrate.")
        return 0
    if not old_style:
        return run_projects_at_root(root, apply, backup)

    moves, problems, warnings = plan(root, mapping, max_depth)
    for warning in warnings:
        print(f"warning: {warning}")
    if problems:
        print("Problems found, nothing was changed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    for move in moves:
        print("\n".join(describe(move)))

    if not apply:
        print(f"\nDry run: {len(moves)} project(s) would move. Nothing was changed.")
        print("Re-run with --apply to migrate.")
        return 0

    before = count_files(root)
    if backup:
        make_backup(root)
        before = count_files(root)
    for move in moves:
        apply_move(root, move)
    for name in OLD_DIRS:
        remove_empty_dirs(root / name)

    after = count_files(root)
    leftovers = [
        p.relative_to(root).as_posix()
        for name in OLD_DIRS
        if (root / name).is_dir()
        for p in (root / name).rglob("*")
        if p.is_file() and not p.name.startswith(".")
    ]
    print(f"\nMigrated {len(moves)} project(s). Files before: {before}, after: {after}.")
    if leftovers:
        print("Left behind in the old directories:")
        for item in leftovers:
            print(f"  - {item}")
    if before != after or leftovers:
        print("The counts do not match or files were left behind; compare with the backup.")
        return 1
    return 0


def default_root() -> Path:
    env = os.environ.get("AGENT_MEMORY_DIR")
    return Path(env) if env else Path.home() / ".agent-memory"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate-layout",
        description=(
            "Move an agent-memory storage root from the type-first layout "
            "(kanban/, notes/, decisions/) to the project-first layout."
        ),
        epilog=(
            "examples:\n"
            "  migrate-layout --root ~/agent-memory --mapping mapping.toml\n"
            "  migrate-layout --root ~/agent-memory --mapping mapping.toml --apply\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-r", "--root", type=Path, help="storage root (default: $AGENT_MEMORY_DIR)")
    parser.add_argument("-m", "--mapping", type=Path, help="TOML file with a [projects] table")
    parser.add_argument("--apply", action="store_true", help="migrate (default: dry run)")
    parser.add_argument(
        "--no-backup", action="store_true", help="with --apply: skip the backup copy"
    )
    parser.add_argument("--max-depth", type=int, default=5, help="deepest allowed path (default 5)")
    args = parser.parse_args(argv)

    root = (args.root or default_root()).expanduser()
    if not root.is_dir():
        print(f"{root} does not exist.", file=sys.stderr)
        return 2
    mapping = load_mapping(args.mapping)
    return run(root, mapping, args.apply, not args.no_backup, args.max_depth)


if __name__ == "__main__":
    sys.exit(main())
