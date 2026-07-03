"""`recolor_slots` logic: remap filament slots across every painted triangle."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from reference import paint_codec_reference
from threemf_paint_mcp import archive, threemf_model
from threemf_paint_mcp.inspect import inspect_archive

_PAINT_COLOR_ATTR = re.compile(r'paint_color="([0-9a-fA-F]+)"')


class RecolorError(RuntimeError):
    pass


def _count_triangles(xml_bytes: bytes) -> int:
    root = ET.fromstring(xml_bytes)
    return sum(1 for _ in threemf_model.iter_local(root, "triangle"))


def recolor_slots(path: str | Path, mapping: dict[str, int], output_path: str | Path) -> dict:
    path = Path(path)
    output_path = Path(output_path)
    slot_mapping = {int(k): int(v) for k, v in mapping.items()}

    with zipfile.ZipFile(path) as zf:
        model_paths = threemf_model.all_model_paths(zf)
        original_bytes = {name: zf.read(name) for name in model_paths}

    updated_entries: dict[str, bytes] = {}
    files_changed: list[str] = []
    distinct_codes_changed = 0
    triangles_changed = 0

    for model_path in model_paths:
        raw = original_bytes[model_path]
        text = raw.decode("utf-8")
        distinct_codes = set(_PAINT_COLOR_ATTR.findall(text))

        code_map: dict[str, str] = {}
        for old_hex in distinct_codes:
            if not paint_codec_reference.verify_roundtrip(old_hex):
                raise RecolorError(
                    f"round-trip verification failed for paint_color={old_hex!r} in {model_path}"
                )
            tree = paint_codec_reference.decode(old_hex)
            new_tree = paint_codec_reference.remap_leaf_states(tree, slot_mapping)
            if not paint_codec_reference.structure_matches(tree, new_tree):
                raise RecolorError(
                    f"split-tree structure changed after remap for paint_color={old_hex!r} "
                    f"in {model_path} -- this should never happen"
                )
            new_hex = paint_codec_reference.encode(new_tree)
            if new_hex != old_hex:
                code_map[old_hex] = new_hex

        if not code_map:
            continue

        new_text = text
        for old_hex, new_hex in code_map.items():
            old_pattern = f'paint_color="{old_hex}"'
            new_pattern = f'paint_color="{new_hex}"'
            triangles_changed += new_text.count(old_pattern)
            new_text = new_text.replace(old_pattern, new_pattern)

        new_bytes = new_text.encode("utf-8")
        if _count_triangles(new_bytes) != _count_triangles(raw):
            raise RecolorError(f"triangle count changed while recoloring {model_path}")

        updated_entries[model_path] = new_bytes
        files_changed.append(model_path)
        distinct_codes_changed += len(code_map)

    archive.repackage_with_updates(path, output_path, updated_entries)

    validation = archive.validate_archive(output_path)
    if not validation["ok"]:
        raise RecolorError(f"output archive failed validation: {validation}")

    after = inspect_archive(output_path)
    used_slots = {int(slot) for slot in after["paint_usage_by_slot"]}
    base_slots = {obj["base_extruder_slot"] for obj in after["objects"]}
    palette_slots = {int(slot) for slot in after["filament_palette"]}
    slots_now_unused = sorted(palette_slots - used_slots - base_slots)

    return {
        "output_path": str(output_path),
        "files_changed": files_changed,
        "distinct_codes_changed": distinct_codes_changed,
        "triangles_changed": triangles_changed,
        "slots_now_unused": slots_now_unused,
        "validation": validation,
    }
