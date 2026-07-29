"""`recolor_slots`/`recolor_by_name` logic: remap filament slots across painted triangles.

Both can be scoped to specific objects (by root-level object id, or by
name via `recolor_by_name`) instead of touching the whole archive -- see
`_scope_by_path` for how a root-level object id (the id
`model_settings.config`'s name/extruder metadata uses) gets translated
into the mesh-internal object id(s) that actually own `<triangle>`
elements, per `threemf_model.ObjectRef`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from reference import paint_codec_reference
from threemf_paint_mcp import archive, threemf_model
from threemf_paint_mcp.inspect import inspect_archive
from threemf_paint_mcp.patch import TrianglePatchError, apply_triangle_decisions


class RecolorError(RuntimeError):
    pass


def _count_triangles(xml_bytes: bytes) -> int:
    root = ET.fromstring(xml_bytes)
    return sum(1 for _ in threemf_model.iter_local(root, "triangle"))


def _scope_by_path(
    refs: list[threemf_model.ObjectRef], root_ids: set[str]
) -> dict[str, set[str]]:
    """{model_path: {mesh-internal object ids}} for the requested root-level ids."""
    scope: dict[str, set[str]] = {}
    for ref in refs:
        if ref.root_object_id in root_ids:
            scope.setdefault(ref.model_path, set()).add(ref.mesh_object_id)
    return scope


def recolor_slots(
    path: str | Path,
    mapping: dict[str, int],
    output_path: str | Path,
    object_ids: list[str] | None = None,
) -> dict:
    """Remap filament slots across painted triangles.

    `mapping` is {old_slot: new_slot}. If `object_ids` is given (root-level
    object ids, as returned by `inspect_3mf`'s `objects[].object_id`), only
    triangles belonging to those objects are touched -- otherwise every
    object in the archive is in scope.
    """
    path = Path(path)
    output_path = Path(output_path)
    slot_mapping = {int(k): int(v) for k, v in mapping.items()}

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
                raise RecolorError(f"object_ids not found: {sorted(missing)}")
            scope_by_path = _scope_by_path(refs, requested)

    updated_entries: dict[str, bytes] = {}
    files_changed: list[str] = []
    distinct_codes_changed = 0
    triangles_changed = 0

    for model_path in model_paths:
        raw = original_bytes[model_path]
        root = ET.fromstring(raw)

        in_scope_mesh_ids = (
            None if scope_by_path is None else scope_by_path.get(model_path, set())
        )

        decisions: list[str | None] = []
        file_changed_codes: set[str] = set()
        file_changed = 0

        for obj in threemf_model.iter_local(root, "object"):
            object_id = threemf_model.find_attr(obj, "id")
            obj_in_scope = in_scope_mesh_ids is None or object_id in in_scope_mesh_ids
            for triangle in threemf_model.iter_local(obj, "triangle"):
                paint_color = threemf_model.find_attr(triangle, "paint_color")
                if not obj_in_scope or not paint_color:
                    decisions.append(None)
                    continue

                if not paint_codec_reference.verify_roundtrip(paint_color):
                    raise RecolorError(
                        f"round-trip verification failed for paint_color={paint_color!r} "
                        f"in {model_path}"
                    )
                tree = paint_codec_reference.decode(paint_color)
                new_tree = paint_codec_reference.remap_leaf_states(tree, slot_mapping)
                if not paint_codec_reference.structure_matches(tree, new_tree):
                    raise RecolorError(
                        f"split-tree structure changed after remap for "
                        f"paint_color={paint_color!r} in {model_path} -- this should "
                        "never happen"
                    )
                new_hex = paint_codec_reference.encode(new_tree)
                if new_hex != paint_color:
                    decisions.append(new_hex)
                    file_changed_codes.add(paint_color)
                    file_changed += 1
                else:
                    decisions.append(None)

        if file_changed == 0:
            continue

        text = raw.decode("utf-8")
        try:
            new_text, changed = apply_triangle_decisions(text, decisions)
        except TrianglePatchError as exc:
            raise RecolorError(str(exc)) from exc
        if changed != file_changed:
            raise RecolorError(
                f"decision/patch count mismatch in {model_path}: "
                f"expected {file_changed}, patched {changed}"
            )

        new_bytes = new_text.encode("utf-8")
        if _count_triangles(new_bytes) != _count_triangles(raw):
            raise RecolorError(f"triangle count changed while recoloring {model_path}")

        updated_entries[model_path] = new_bytes
        files_changed.append(model_path)
        distinct_codes_changed += len(file_changed_codes)
        triangles_changed += file_changed

    archive.repackage_with_updates(path, output_path, updated_entries)

    validation = archive.validate_archive(output_path)
    if not validation["ok"]:
        raise RecolorError(f"output archive failed validation: {validation}")

    after = inspect_archive(output_path)
    used_slots = {int(slot) for slot in after["paint_usage_by_slot"]}
    base_slots = {
        obj["base_extruder_slot"]
        for obj in after["objects"]
        if obj["base_extruder_slot"] is not None
    }
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


def _used_slots_for_root_ids(path: Path, root_ids: set[str]) -> set[int]:
    with zipfile.ZipFile(path) as zf:
        refs = threemf_model.get_object_refs(zf)
        scope_by_path = _scope_by_path(refs, root_ids)

        slots: set[int] = set()
        for model_path, mesh_ids in scope_by_path.items():
            root = threemf_model.read_xml(zf, model_path)
            for obj in threemf_model.iter_local(root, "object"):
                if threemf_model.find_attr(obj, "id") not in mesh_ids:
                    continue
                for triangle in threemf_model.iter_local(obj, "triangle"):
                    paint_color = threemf_model.find_attr(triangle, "paint_color")
                    if not paint_color:
                        continue
                    tree = paint_codec_reference.decode(paint_color)
                    slots.update(paint_codec_reference.collect_leaf_states(tree))
    return slots


def recolor_by_name(
    path: str | Path,
    object_name: str,
    target_slot: int,
    output_path: str | Path,
) -> dict:
    """Recolor every painted region on name-matched objects to `target_slot`.

    Matches objects (case-insensitive substring) against the `name`
    metadata `inspect_3mf` exposes per object (`objects[].name`); objects
    without a name are never matched. Every filament slot currently
    painted anywhere on the matched objects gets remapped to
    `target_slot`. Refuses (raises `RecolorError`) rather than silently
    no-op-ing when `object_name` matches nothing.

    Does NOT touch triangles that have no `paint_color` attribute at all --
    those inherit the object's *base* extruder (`model_settings.config`),
    which is separate from triangle paint data and out of scope here, same
    limitation as `recolor_slots`.
    """
    path = Path(path)
    output_path = Path(output_path)
    target_slot = int(target_slot)
    needle = object_name.strip().lower()
    if not needle:
        raise RecolorError("object_name must not be empty")

    with zipfile.ZipFile(path) as zf:
        object_names = threemf_model.get_object_names(zf)

    matched_ids = sorted(
        (oid for oid, name in object_names.items() if needle in name.lower()),
        key=lambda x: int(x) if x.isdigit() else x,
    )
    if not matched_ids:
        raise RecolorError(
            f"no object name matches {object_name!r} "
            f"(known names: {sorted(object_names.values())})"
        )

    used_slots = _used_slots_for_root_ids(path, set(matched_ids))
    mapping = {slot: target_slot for slot in used_slots if slot != target_slot}
    matched_names = [object_names[oid] for oid in matched_ids]

    if not mapping:
        return {
            "output_path": None,
            "matched_object_ids": matched_ids,
            "matched_object_names": matched_names,
            "mapping_applied": {},
            "already_correct": True,
        }

    result = recolor_slots(
        path,
        {str(k): v for k, v in mapping.items()},
        output_path,
        object_ids=matched_ids,
    )
    result["matched_object_ids"] = matched_ids
    result["matched_object_names"] = matched_names
    result["mapping_applied"] = {str(k): v for k, v in mapping.items()}
    return result
