"""`paint_region` logic: paint triangles selected by an axis-aligned bounding box.

Complements the other paint-selection tools, each keyed on a different
signal: `repair_embossed_paint` selects by detected Z-plane,
`recolor_by_name`/`recolor_slots(object_ids=...)` select by whole object.
This selects by pure geometry -- useful for painting detail that was never
painted at all (so there's no existing paint to detect a plane from) and
isn't cleanly its own named object (e.g. a procedurally generated model
where color-coded regions are known by coordinate ranges).

Same "never guess inside a split node" rule as recolor.py/repair_emboss.py:
a selected triangle whose existing paint is a multi-region split is left
untouched and counted separately rather than reinterpreted.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from reference import paint_codec_reference
from reference.paint_codec_reference import Leaf, Split
from threemf_paint_mcp import archive, threemf_model
from threemf_paint_mcp.patch import TrianglePatchError, apply_triangle_decisions

_MODES = ("any", "all")


class RegionPaintError(RuntimeError):
    pass


def _in_bounds(value: float, lo: float | None, hi: float | None) -> bool:
    if lo is not None and value < lo:
        return False
    return not (hi is not None and value > hi)


def _vertex_in_bbox(
    vertex: tuple[float, float, float],
    bounds: tuple[
        float | None, float | None, float | None, float | None, float | None, float | None
    ],
) -> bool:
    x_min, x_max, y_min, y_max, z_min, z_max = bounds
    return (
        _in_bounds(vertex[0], x_min, x_max)
        and _in_bounds(vertex[1], y_min, y_max)
        and _in_bounds(vertex[2], z_min, z_max)
    )


def paint_region(
    path: str | Path,
    output_path: str | Path,
    slot: int,
    x_min: float | None = None,
    x_max: float | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    z_min: float | None = None,
    z_max: float | None = None,
    mode: str = "any",
    object_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Paint every triangle whose vertices fall inside an axis-aligned bounding box.

    Any bound left as `None` is unbounded on that axis (e.g. only setting
    `z_min`/`z_max` selects by Z range regardless of X/Y). At least one
    bound must be set -- use `recolor_slots`/`recolor_by_name` to paint a
    whole object. `mode="any"` (default) selects a triangle if any vertex
    is inside the box; `mode="all"` requires every vertex inside (fully
    contained). Pass `object_ids` to further scope to specific root-level
    objects. Multi-region (split) triangles inside the selection are left
    untouched and reported as `faces_skipped_multi_region`. Idempotent --
    a fully-painted selection reports `already_complete` and writes
    nothing. Set `dry_run=True` to preview counts without writing.
    """
    if mode not in _MODES:
        raise RegionPaintError(f"mode must be one of {_MODES}, got {mode!r}")
    bounds = (x_min, x_max, y_min, y_max, z_min, z_max)
    if all(b is None for b in bounds):
        raise RegionPaintError(
            "at least one of x_min/x_max/y_min/y_max/z_min/z_max must be set -- "
            "use recolor_slots/recolor_by_name to paint a whole object"
        )
    slot = int(slot)
    path = Path(path)
    output_path = Path(output_path)

    with zipfile.ZipFile(path) as zf:
        model_paths = threemf_model.all_model_paths(zf)
        original_bytes = {name: zf.read(name) for name in model_paths}

        scope_by_path: dict[str, set[str]] | None = None
        if object_ids is not None:
            requested = {str(oid) for oid in object_ids}
            refs = threemf_model.get_object_refs(zf)
            known_roots = {ref.root_object_id for ref in refs}
            missing = requested - known_roots
            if missing:
                raise RegionPaintError(f"object_ids not found: {sorted(missing)}")
            scope_by_path = threemf_model.scope_refs_by_path(refs, requested)

    paint_code = paint_codec_reference.encode(Leaf(state=slot))
    updated_entries: dict[str, bytes] = {}
    newly_painted = recoloured = already_correct = skipped_multi_region = 0

    for model_path in model_paths:
        raw = original_bytes[model_path]
        root = ET.fromstring(raw)
        in_scope_mesh_ids = None if scope_by_path is None else scope_by_path.get(model_path, set())

        decisions: list[str | None] = []
        file_changed = 0

        for obj in threemf_model.iter_local(root, "object"):
            object_id = threemf_model.find_attr(obj, "id")
            obj_in_scope = in_scope_mesh_ids is None or object_id in in_scope_mesh_ids

            for mesh_elem in threemf_model.iter_local(obj, "mesh"):
                vertices: list[tuple[float, float, float]] = []
                for vertex in threemf_model.iter_local(mesh_elem, "vertex"):
                    x = float(threemf_model.find_attr(vertex, "x") or 0.0)
                    y = float(threemf_model.find_attr(vertex, "y") or 0.0)
                    z = float(threemf_model.find_attr(vertex, "z") or 0.0)
                    vertices.append((x, y, z))

                for triangle in threemf_model.iter_local(mesh_elem, "triangle"):
                    v1 = threemf_model.find_attr(triangle, "v1")
                    v2 = threemf_model.find_attr(triangle, "v2")
                    v3 = threemf_model.find_attr(triangle, "v3")
                    if not obj_in_scope or v1 is None or v2 is None or v3 is None:
                        decisions.append(None)
                        continue

                    flags = [_vertex_in_bbox(vertices[int(v)], bounds) for v in (v1, v2, v3)]
                    selected = any(flags) if mode == "any" else all(flags)
                    if not selected:
                        decisions.append(None)
                        continue

                    paint_color = threemf_model.find_attr(triangle, "paint_color")
                    if paint_color is None:
                        decisions.append(paint_code)
                        newly_painted += 1
                        file_changed += 1
                        continue

                    node = paint_codec_reference.decode(paint_color)
                    if isinstance(node, Split):
                        decisions.append(None)
                        skipped_multi_region += 1
                    elif node.state == slot:
                        decisions.append(None)
                        already_correct += 1
                    else:
                        decisions.append(paint_code)
                        recoloured += 1
                        file_changed += 1

        if file_changed:
            text = raw.decode("utf-8")
            try:
                new_text, changed = apply_triangle_decisions(text, decisions)
            except TrianglePatchError as exc:
                raise RegionPaintError(str(exc)) from exc
            if changed != file_changed:
                raise RegionPaintError(
                    f"decision/patch count mismatch in {model_path}: "
                    f"expected {file_changed}, patched {changed}"
                )
            updated_entries[model_path] = new_text.encode("utf-8")

    counts = {
        "faces_newly_painted": newly_painted,
        "faces_recoloured": recoloured,
        "faces_already_correct": already_correct,
        "faces_skipped_multi_region": skipped_multi_region,
    }
    total_changed = newly_painted + recoloured

    if total_changed == 0:
        return {
            "already_complete": True,
            "output_path": None,
            "paint_code": paint_code,
            **counts,
        }

    if dry_run:
        return {
            "dry_run": True,
            "output_path": None,
            "paint_code": paint_code,
            **counts,
        }

    archive.repackage_with_updates(path, output_path, updated_entries)
    validation = archive.validate_archive(output_path)
    if not validation["ok"]:
        raise RegionPaintError(f"output archive failed validation: {validation}")

    return {
        "output_path": str(output_path),
        "paint_code": paint_code,
        "validation": validation,
        **counts,
    }
