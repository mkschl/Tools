"""Markdown linting via the system `markdownlint` CLI (markdownlint-cli, npm).

Shells out rather than adding a Python markdown-linting dependency, since
`markdownlint` is already the linting tool used elsewhere on this machine —
writing wiki pages under the same rule set keeps them consistent with the
rest of the repo. If the binary isn't on PATH, linting is skipped gracefully
rather than failing the calling tool.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_BINARY = "markdownlint"


def _find_binary() -> str | None:
    return shutil.which(_BINARY)


def _config_args(wiki_root: Path) -> list[str]:
    override = os.environ.get("WIKI_MARKDOWNLINT_CONFIG")
    config = Path(override) if override else wiki_root / ".markdownlint.json"
    return ["--config", str(config)] if config.is_file() else []


def format_issues(issues: list[dict]) -> str:
    if not issues:
        return "No markdownlint issues found."
    lines = []
    for v in issues:
        rule = "/".join(v.get("ruleNames", []))
        desc = v.get("ruleDescription", "")
        line_no = v.get("lineNumber", "?")
        detail = v.get("errorDetail")
        msg = f"Line {line_no}: {rule} {desc}"
        if detail:
            msg += f" ({detail})"
        lines.append(msg)
    return "\n".join(lines)


def fix_content(content: str, wiki_root: Path) -> tuple[str, list[dict]]:
    """Auto-fix basic markdownlint issues in-place. Returns the (possibly
    fixed) content plus any violations that couldn't be auto-fixed. Returns
    the content unchanged if the markdownlint binary is unavailable."""
    binary = _find_binary()
    if not binary:
        return content, []
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        temp_path = Path(f.name)
    try:
        result = subprocess.run(
            [binary, "--fix", "--json", *_config_args(wiki_root), str(temp_path)],
            capture_output=True,
            text=True,
        )
        fixed_content = temp_path.read_text(encoding="utf-8")
        if result.returncode == 0:
            return fixed_content, []
        raw = result.stderr.strip() or result.stdout.strip()
        if not raw:
            return fixed_content, []
        try:
            issues = json.loads(raw)
        except json.JSONDecodeError:
            issues = [{"ruleNames": ["error"], "ruleDescription": raw, "lineNumber": None}]
        return fixed_content, issues
    finally:
        temp_path.unlink(missing_ok=True)
