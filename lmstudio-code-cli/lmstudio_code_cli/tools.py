import subprocess
from pathlib import Path

# ── Tool schemas (OpenAI function-calling format) ─────────────────────────────

READ_FILE: dict = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a file inside the current working directory. "
            "Returns lines prefixed with line numbers. Use offset and limit to read a specific range. "
            "Always use this (not any MCP filesystem tool) for source code and project files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to working dir or absolute)"},
                "offset": {"type": "integer", "description": "First line to return (1-indexed, default 1)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to return"},
            },
            "required": ["path"],
        },
    },
}

WRITE_FILE: dict = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Create or overwrite a file inside the current working directory. Parent directories are created automatically. Always use this (not any MCP filesystem tool) for project files.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
}

EDIT_FILE: dict = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Edit a file by replacing an exact string with new text. "
            "old_string must match exactly once in the file — make it unique by including surrounding context. "
            "Always read_file before editing to confirm the exact text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_string": {"type": "string", "description": "Exact text to find (must be unique in the file)"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

RUN_BASH: dict = {
    "type": "function",
    "function": {
        "name": "run_bash",
        "description": "Execute a shell command in the working directory. Use for builds, tests, installs, git, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
            },
            "required": ["command"],
        },
    },
}

LIST_DIRECTORY: dict = {
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": "List files and subdirectories inside the current working directory. Always use this (not any MCP filesystem tool) to browse project files.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: working directory)"},
            },
        },
    },
}

SEARCH_FILES: dict = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": "Search for a pattern across files in the current working directory using grep. Returns matching lines with file and line number. Always use this (not any MCP filesystem tool) to search project source code.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search (default: working dir)"},
                "file_pattern": {"type": "string", "description": "Only search files matching this glob (e.g. '*.py')"},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive search (default true)"},
            },
            "required": ["pattern"],
        },
    },
}

GLOB_FILES: dict = {
    "type": "function",
    "function": {
        "name": "glob_files",
        "description": "Find files matching a glob pattern. Use ** for recursive matching.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py', 'src/*.ts')"},
                "path": {"type": "string", "description": "Base directory (default: working directory)"},
            },
            "required": ["pattern"],
        },
    },
}

ALL_TOOLS = [READ_FILE, WRITE_FILE, EDIT_FILE, RUN_BASH, LIST_DIRECTORY, SEARCH_FILES, GLOB_FILES]

# ── Tool implementations ──────────────────────────────────────────────────────


def _resolve(path: str, cwd: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(cwd) / p


def execute_tool(name: str, args: dict, cwd: str) -> str:
    try:
        match name:
            case "read_file":
                return _read_file(args, cwd)
            case "write_file":
                return _write_file(args, cwd)
            case "edit_file":
                return _edit_file(args, cwd)
            case "run_bash":
                return _run_bash(args, cwd)
            case "list_directory":
                return _list_directory(args, cwd)
            case "search_files":
                return _search_files(args, cwd)
            case "glob_files":
                return _glob_files(args, cwd)
            case _:
                return f"Unknown tool: {name}"
    except Exception as exc:
        return f"Error executing {name}: {exc}"


def _read_file(args: dict, cwd: str) -> str:
    path = _resolve(args["path"], cwd)
    if not path.exists():
        return f"File not found: {path}"
    if path.is_dir():
        return f"Path is a directory, use list_directory instead: {path}"

    lines = path.read_text(errors="replace").splitlines(keepends=True)
    offset = max(1, args.get("offset", 1))
    limit = args.get("limit")
    start = offset - 1
    end = (start + limit) if limit else len(lines)
    selected = lines[start:end]

    result = "".join(f"{start + i + 1}\t{line}" for i, line in enumerate(selected))
    if not result.endswith("\n"):
        result += "\n"
    if len(lines) > end:
        result += f"[{len(lines) - end} more lines — use offset/limit to read further]\n"
    return result


def _write_file(args: dict, cwd: str) -> str:
    path = _resolve(args["path"], cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = args["content"]
    path.write_text(content)
    lines = content.count("\n")
    return f"Wrote {len(content)} bytes ({lines} lines) to {path}"


def _edit_file(args: dict, cwd: str) -> str:
    path = _resolve(args["path"], cwd)
    if not path.exists():
        return f"File not found: {path}"

    content = path.read_text()
    old = args["old_string"]
    new = args["new_string"]

    count = content.count(old)
    if count == 0:
        return f"old_string not found in {path} — did you read the file first?"
    if count > 1:
        return (
            f"old_string matches {count} locations in {path}. "
            "Include more surrounding context to make it unique."
        )

    path.write_text(content.replace(old, new, 1))
    return f"Successfully edited {path}"


def _run_bash(args: dict, cwd: str) -> str:
    command = args["command"]
    timeout = int(args.get("timeout", 30))

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            executable="/bin/zsh",
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout.rstrip())
    if result.stderr:
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")
    if result.returncode != 0:
        parts.append(f"\n(exit code {result.returncode})")
    return "\n".join(parts) if parts else f"(exit code {result.returncode})"


def _list_directory(args: dict, cwd: str) -> str:
    path = _resolve(args.get("path", "."), cwd)
    if not path.exists():
        return f"Path not found: {path}"
    if not path.is_dir():
        return f"Not a directory: {path}"

    entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    if not entries:
        return "(empty directory)"

    lines: list[str] = []
    for entry in entries:
        if entry.is_dir():
            lines.append(f"{entry.name}/")
        else:
            size = entry.stat().st_size
            lines.append(f"{entry.name}  ({size:,} bytes)")
    return "\n".join(lines)


def _search_files(args: dict, cwd: str) -> str:
    pattern = args["pattern"]
    search_path = str(_resolve(args.get("path", "."), cwd))
    file_pattern = args.get("file_pattern", "")
    case_sensitive = args.get("case_sensitive", True)

    cmd = ["grep", "-rn", "--color=never"]
    if not case_sensitive:
        cmd.append("-i")
    if file_pattern:
        cmd.extend(["--include", file_pattern])
    cmd.extend([pattern, search_path])

    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
    output = result.stdout.strip()
    return output if output else "(no matches)"


def _glob_files(args: dict, cwd: str) -> str:
    base = _resolve(args.get("path", "."), cwd)
    pattern = args["pattern"]

    matches = sorted(base.glob(pattern))
    if not matches:
        return "(no files found)"

    lines = []
    for m in matches:
        try:
            rel = m.relative_to(base)
        except ValueError:
            rel = m
        lines.append(str(rel))
    return "\n".join(lines)
