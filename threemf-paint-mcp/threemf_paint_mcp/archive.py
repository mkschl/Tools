"""ZIP-level helpers for editing .3mf archives without a full rebuild.

Per CLAUDE.md's "Repackaging rule": update changed entries in place inside
a copy of the original archive (`cp original.3mf working.3mf && zip
working.3mf path/to/changed/file`) rather than rebuilding from an
extracted directory, to preserve entry order and compression choices that
Bambu Studio's reader may be stricter about than Python's `zipfile`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


class RepackageError(RuntimeError):
    pass


def read_entry(archive_path: Path, entry_name: str) -> bytes:
    with zipfile.ZipFile(archive_path) as zf:
        return zf.read(entry_name)


def list_entries(archive_path: Path) -> list[str]:
    with zipfile.ZipFile(archive_path) as zf:
        return zf.namelist()


def repackage_with_updates(
    original_path: Path, output_path: Path, updated_entries: dict[str, bytes]
) -> None:
    """Copy `original_path` to `output_path`, then update entries in place.

    Uses the system `zip` binary (Info-ZIP) so the update semantics match
    what a human would get running `zip archive.3mf changed/file` by hand,
    rather than Python zipfile's rebuild-from-scratch behavior.
    """
    if shutil.which("zip") is None:
        raise RepackageError(
            "the 'zip' command-line tool is required for in-place repackaging "
            "but was not found on PATH"
        )
    output_path = Path(output_path)
    shutil.copy2(original_path, output_path)
    if not updated_entries:
        return
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        rel_paths = []
        for entry_name, data in updated_entries.items():
            file_path = tmp_path / entry_name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(data)
            rel_paths.append(entry_name)
        result = subprocess.run(
            ["zip", str(output_path.resolve()), *rel_paths],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RepackageError(f"zip update failed (exit {result.returncode}): {result.stderr}")


def rebuild_from_entries(output_path: Path, entries: dict[str, bytes]) -> None:
    """Build a fresh archive from scratch out of an explicit entry map.

    Used when enough entries are being dropped/added (e.g. plate
    extraction) that in-place updating is no longer simpler than a clean
    rebuild.
    """
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def validate_archive(archive_path: Path, xml_entries: list[str] | None = None) -> dict:
    """CRC-check the zip and parse the given (or all `.model`/`.config`/`.xml`) entries.

    Returns {"ok": bool, "bad_crc": [...], "xml_errors": {entry: message}}.
    """
    result: dict = {"ok": True, "bad_crc": [], "xml_errors": {}}
    with zipfile.ZipFile(archive_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            result["ok"] = False
            result["bad_crc"].append(bad)
        targets = xml_entries
        if targets is None:
            targets = [
                n for n in zf.namelist() if n.endswith((".model", ".config", ".xml", ".rels"))
            ]
        for name in targets:
            data = zf.read(name)
            try:
                ET.fromstring(data)
                continue
            except ET.ParseError as exc:
                xml_error = exc
            # project_settings.config and similar are JSON despite the
            # ".config" extension -- fall back before flagging as broken.
            if name.endswith(".config"):
                try:
                    json.loads(data)
                    continue
                except json.JSONDecodeError:
                    pass
            result["ok"] = False
            result["xml_errors"][name] = str(xml_error)
    return result
