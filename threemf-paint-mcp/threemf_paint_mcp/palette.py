"""`swap_filament_palette` logic: change what a slot's hex color means.

Complements `recolor.py` (which remaps which slot painted triangles use) by
editing the *other* side of the same relationship -- `filament_colour` in
`project_settings.config` -- without touching any triangle paint data. Pure
JSON edit, no ZIP entry needs re-parsing as XML.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from threemf_paint_mcp import archive, threemf_model

_HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


class PaletteError(RuntimeError):
    pass


def swap_filament_palette(
    path: str | Path, mapping: dict[str, str], output_path: str | Path
) -> dict:
    """Reassign filament slot colors without touching any painted triangle.

    `mapping` is {slot_number: new_hex_color} (slot numbers as given by
    inspect_3mf's `filament_palette`, hex as "#RRGGBB" or "#RRGGBBAA").
    Slots not mentioned in `mapping` are left unchanged. Raises if a slot
    doesn't exist in the current palette or a color isn't valid hex.
    """
    path = Path(path)
    output_path = Path(output_path)

    slot_mapping: dict[int, str] = {}
    for slot_str, hex_color in mapping.items():
        slot = int(slot_str)
        if not _HEX_RE.match(hex_color):
            raise PaletteError(
                f"invalid hex color {hex_color!r} for slot {slot} -- expected #RRGGBB or #RRGGBBAA"
            )
        slot_mapping[slot] = hex_color.upper()

    with zipfile.ZipFile(path) as zf:
        if threemf_model.PROJECT_SETTINGS not in zf.namelist():
            raise PaletteError(f"{threemf_model.PROJECT_SETTINGS} not found in archive")
        config = threemf_model.read_json(zf, threemf_model.PROJECT_SETTINGS)

    colours: list[str] = list(config.get("filament_colour", []))
    known_slots = set(range(1, len(colours) + 1))
    missing = set(slot_mapping) - known_slots
    if missing:
        raise PaletteError(
            f"slots not found in filament palette (has {len(colours)} slots): {sorted(missing)}"
        )

    colours_changed: dict[str, dict[str, str]] = {}
    for slot, new_hex in slot_mapping.items():
        old_hex = colours[slot - 1]
        if old_hex.upper() != new_hex:
            colours_changed[str(slot)] = {"old": old_hex, "new": new_hex}
            colours[slot - 1] = new_hex

    if not colours_changed:
        return {
            "output_path": None,
            "already_correct": True,
            "colours_changed": {},
        }

    config["filament_colour"] = colours
    new_bytes = json.dumps(config, indent=2).encode("utf-8")

    archive.repackage_with_updates(path, output_path, {threemf_model.PROJECT_SETTINGS: new_bytes})
    validation = archive.validate_archive(output_path)
    if not validation["ok"]:
        raise PaletteError(f"output archive failed validation: {validation}")

    return {
        "output_path": str(output_path),
        "colours_changed": colours_changed,
        "validation": validation,
    }
