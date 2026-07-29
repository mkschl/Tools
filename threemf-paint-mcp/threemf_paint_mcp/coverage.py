"""`get_paint_coverage_report` logic: surface-area paint coverage per object per slot.

Triangle *counts* (as `inspect_3mf` reports) can be misleading: a model can
be "90% painted" by triangle count while the unpainted 10% is most of the
visible surface (a handful of large unpainted triangles vs. many small
painted detail triangles, or vice versa -- exactly the shape of defect
`repair_embossed_paint` targets, generalized to any object rather than just
plane-detectable emboss). This walks every mesh, computes each triangle's
actual surface area from its vertex positions, and buckets that area by
filament slot per object -- read-only, no archive mutation.

Read-only, so this reuses `threemf_model.ObjectRef` (see recolor.py's
module docstring) purely to resolve mesh-internal object ids back to the
root-level object id `inspect_3mf`/`model_settings.config` use, so results
are reported per user-recognizable object rather than per internal mesh id.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from reference import paint_codec_reference
from reference.paint_codec_reference import Split
from threemf_paint_mcp import threemf_model


class CoverageError(RuntimeError):
    pass


def _triangle_area(
    v1: tuple[float, float, float],
    v2: tuple[float, float, float],
    v3: tuple[float, float, float],
) -> float:
    ax, ay, az = v2[0] - v1[0], v2[1] - v1[1], v2[2] - v1[2]
    bx, by, bz = v3[0] - v1[0], v3[1] - v1[1], v3[2] - v1[2]
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    return 0.5 * (cx * cx + cy * cy + cz * cz) ** 0.5


def _new_accumulator() -> dict:
    return {
        "total_surface_area": 0.0,
        "total_triangles": 0,
        "unpainted_area": 0.0,
        "unpainted_triangles": 0,
        "split_area": 0.0,
        "split_triangles": 0,
        "area_by_slot": {},
        "triangles_by_slot": {},
    }


def get_coverage_report(path: str | Path, object_ids: list[str] | None = None) -> dict:
    """Per-object, per-slot painted surface area (not just triangle counts).

    If `object_ids` is given (root-level object ids from inspect_3mf's
    `objects[].object_id`), only those objects are reported -- otherwise
    every object in the archive. Triangles whose paint is a multi-region
    split (subdivided, mixing slots) contribute to `split_area` /
    `split_triangles` rather than being apportioned to a slot -- same
    "never guess inside a split node" rule as recolor.py/repair_emboss.py.
    """
    path = Path(path)

    with zipfile.ZipFile(path) as zf:
        refs = threemf_model.get_object_refs(zf)
        object_names = threemf_model.get_object_names(zf)
        base_extruders = threemf_model.get_object_base_extruders(zf)
        model_paths = threemf_model.all_model_paths(zf)

        mesh_to_root = {(ref.model_path, ref.mesh_object_id): ref.root_object_id for ref in refs}

        requested: set[str] | None = None
        if object_ids is not None:
            requested = {str(oid) for oid in object_ids}
            known_roots = {ref.root_object_id for ref in refs}
            missing = requested - known_roots
            if missing:
                raise CoverageError(f"object_ids not found: {sorted(missing)}")

        accumulators: dict[str, dict] = {}

        for model_path in model_paths:
            root = threemf_model.read_xml(zf, model_path)
            for obj in threemf_model.iter_local(root, "object"):
                mesh_id = threemf_model.find_attr(obj, "id")
                if mesh_id is None:
                    continue
                root_id = mesh_to_root.get((model_path, mesh_id), mesh_id)
                if requested is not None and root_id not in requested:
                    continue

                for mesh_elem in threemf_model.iter_local(obj, "mesh"):
                    vertices: list[tuple[float, float, float]] = []
                    for vertex in threemf_model.iter_local(mesh_elem, "vertex"):
                        x = float(threemf_model.find_attr(vertex, "x") or 0.0)
                        y = float(threemf_model.find_attr(vertex, "y") or 0.0)
                        z = float(threemf_model.find_attr(vertex, "z") or 0.0)
                        vertices.append((x, y, z))

                    acc = accumulators.setdefault(root_id, _new_accumulator())
                    for triangle in threemf_model.iter_local(mesh_elem, "triangle"):
                        v1 = threemf_model.find_attr(triangle, "v1")
                        v2 = threemf_model.find_attr(triangle, "v2")
                        v3 = threemf_model.find_attr(triangle, "v3")
                        if v1 is None or v2 is None or v3 is None:
                            continue
                        area = _triangle_area(
                            vertices[int(v1)], vertices[int(v2)], vertices[int(v3)]
                        )
                        acc["total_surface_area"] += area
                        acc["total_triangles"] += 1

                        paint_color = threemf_model.find_attr(triangle, "paint_color")
                        if not paint_color:
                            acc["unpainted_area"] += area
                            acc["unpainted_triangles"] += 1
                            continue

                        node = paint_codec_reference.decode(paint_color)
                        if isinstance(node, Split):
                            acc["split_area"] += area
                            acc["split_triangles"] += 1
                        elif node.state == 0:
                            acc["unpainted_area"] += area
                            acc["unpainted_triangles"] += 1
                        else:
                            acc["area_by_slot"][node.state] = (
                                acc["area_by_slot"].get(node.state, 0.0) + area
                            )
                            acc["triangles_by_slot"][node.state] = (
                                acc["triangles_by_slot"].get(node.state, 0) + 1
                            )

    objects = []
    for root_id in sorted(accumulators, key=lambda x: int(x) if x.isdigit() else x):
        acc = accumulators[root_id]
        total = acc["total_surface_area"]
        classified_unpainted = acc["unpainted_area"] + acc["split_area"]
        painted_fraction = (total - classified_unpainted) / total if total > 0 else 0.0
        objects.append(
            {
                "object_id": root_id,
                "name": object_names.get(root_id),
                "base_extruder_slot": base_extruders.get(root_id),
                "total_surface_area": round(total, 6),
                "total_triangles": acc["total_triangles"],
                "unpainted_area": round(acc["unpainted_area"], 6),
                "unpainted_triangles": acc["unpainted_triangles"],
                "split_area": round(acc["split_area"], 6),
                "split_triangles": acc["split_triangles"],
                "area_by_slot": {
                    str(slot): round(area, 6) for slot, area in sorted(acc["area_by_slot"].items())
                },
                "triangles_by_slot": {
                    str(slot): count for slot, count in sorted(acc["triangles_by_slot"].items())
                },
                "painted_fraction": round(painted_fraction, 6),
            }
        )

    return {"objects": objects}
