"""`diff_3mf` logic: compare two .3mf files' palette, objects, and paint usage.

Read-only, makes no archive changes. Built on top of `inspect_archive` (the
same summary `inspect_3mf` returns) plus a whole-archive triangle count, so
the comparison surfaces the same signals a human would check by eye after
running a tool like `recolor_slots` or `repair_embossed_paint` twice --
"did this actually change what I expected, and nothing else."
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from threemf_paint_mcp import threemf_model
from threemf_paint_mcp.inspect import inspect_archive


def _total_triangle_count(path: str | Path) -> int:
    with zipfile.ZipFile(path) as zf:
        total = 0
        for model_path in threemf_model.all_model_paths(zf):
            root = threemf_model.read_xml(zf, model_path)
            total += sum(1 for _ in threemf_model.iter_local(root, "triangle"))
    return total


def diff_3mf(path_a: str | Path, path_b: str | Path) -> dict:
    """Compare two .3mf files: filament palette, objects, and paint usage.

    Returns `triangle_counts` (total across the whole archive, each side),
    `palette_diff` (slots whose hex color differs, `None` for a side where
    the slot doesn't exist), `objects_added`/`objects_removed` (root-level
    object ids only present on one side), `objects_changed` (common object
    ids whose `name` or `base_extruder_slot` differs), and
    `paint_usage_diff` (slots whose painted-leaf-region count differs,
    defaulting to 0 on a side where the slot isn't painted at all).
    `identical` is `True` only if none of the above found any difference.
    """
    a = inspect_archive(path_a)
    b = inspect_archive(path_b)

    palette_a = a["filament_palette"]
    palette_b = b["filament_palette"]
    palette_diff = {
        slot: {"a": palette_a.get(slot), "b": palette_b.get(slot)}
        for slot in sorted(set(palette_a) | set(palette_b), key=int)
        if palette_a.get(slot) != palette_b.get(slot)
    }

    objects_a = {obj["object_id"]: obj for obj in a["objects"]}
    objects_b = {obj["object_id"]: obj for obj in b["objects"]}
    ids_a = set(objects_a)
    ids_b = set(objects_b)
    objects_added = sorted(ids_b - ids_a, key=lambda x: int(x) if x.isdigit() else x)
    objects_removed = sorted(ids_a - ids_b, key=lambda x: int(x) if x.isdigit() else x)

    objects_changed: dict[str, dict] = {}
    for object_id in sorted(ids_a & ids_b, key=lambda x: int(x) if x.isdigit() else x):
        obj_a, obj_b = objects_a[object_id], objects_b[object_id]
        field_diff = {
            field: {"a": obj_a[field], "b": obj_b[field]}
            for field in ("name", "base_extruder_slot")
            if obj_a[field] != obj_b[field]
        }
        if field_diff:
            objects_changed[object_id] = field_diff

    usage_a = a["paint_usage_by_slot"]
    usage_b = b["paint_usage_by_slot"]
    paint_usage_diff = {
        slot: {"a": usage_a.get(slot, 0), "b": usage_b.get(slot, 0)}
        for slot in sorted(set(usage_a) | set(usage_b), key=int)
        if usage_a.get(slot, 0) != usage_b.get(slot, 0)
    }

    triangle_counts = {
        "a": _total_triangle_count(path_a),
        "b": _total_triangle_count(path_b),
    }

    identical = (
        not palette_diff
        and not objects_added
        and not objects_removed
        and not objects_changed
        and not paint_usage_diff
        and triangle_counts["a"] == triangle_counts["b"]
    )

    return {
        "identical": identical,
        "triangle_counts": triangle_counts,
        "palette_diff": palette_diff,
        "objects_added": objects_added,
        "objects_removed": objects_removed,
        "objects_changed": objects_changed,
        "paint_usage_diff": paint_usage_diff,
    }
