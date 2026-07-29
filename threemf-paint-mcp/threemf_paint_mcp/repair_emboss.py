"""`repair_embossed_paint` logic: finish a partial paint job on raised/engraved detail.

Bambu Studio's paint brush only marks faces visible from the current camera
angle. On raised text viewed from above, the glyph top faces get painted and
the vertical side walls do not -- the preview looks correct from directly
overhead, and the model prints with a rim of base colour around every letter.
Engraved text fails the same way, one plane down.

Detection strategy: cluster mesh vertices by Z into planes, and for each
plane compute what fraction of its flat (all-three-vertices-on-plane) faces
are already painted. The plane the user already painted -- whichever one has
the highest painted fraction -- is the detail plane, regardless of whether
it sits above or below the surrounding surface. Every triangle with at
least one vertex on that plane (tops *and* walls) gets painted with the
slot already in use there; the surrounding surface is left untouched.

Never re-serialises a `.model` file through ElementTree (see CLAUDE.md's
"Safe rewriting rules") -- ElementTree is used read-only for analysis, and
the actual edit is a targeted text splice on the matched `<triangle>` tag
spans, identical in spirit to `recolor.py`'s approach.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from reference import paint_codec_reference
from reference.paint_codec_reference import Leaf, Split
from threemf_paint_mcp import archive, threemf_model
from threemf_paint_mcp.patch import TrianglePatchError, apply_triangle_decisions


class RepairEmbossError(RuntimeError):
    pass


@dataclass
class _TriangleInfo:
    v1: int
    v2: int
    v3: int
    paint_color: str | None


@dataclass
class _MeshData:
    object_id: str | None
    vertices_z: list[float]
    triangles: list[_TriangleInfo] = field(default_factory=list)


def _parse_meshes(root: ET.Element) -> list[_MeshData]:
    """Every `<mesh>` under every `<object>`, in document order."""
    meshes: list[_MeshData] = []
    for obj in threemf_model.iter_local(root, "object"):
        object_id = threemf_model.find_attr(obj, "id")
        for mesh_elem in threemf_model.iter_local(obj, "mesh"):
            vertices_z: list[float] = []
            for vertex in threemf_model.iter_local(mesh_elem, "vertex"):
                z = threemf_model.find_attr(vertex, "z")
                vertices_z.append(float(z) if z is not None else 0.0)
            triangles: list[_TriangleInfo] = []
            for triangle in threemf_model.iter_local(mesh_elem, "triangle"):
                v1 = threemf_model.find_attr(triangle, "v1")
                v2 = threemf_model.find_attr(triangle, "v2")
                v3 = threemf_model.find_attr(triangle, "v3")
                if v1 is None or v2 is None or v3 is None:
                    continue
                paint_color = threemf_model.find_attr(triangle, "paint_color")
                triangles.append(_TriangleInfo(int(v1), int(v2), int(v3), paint_color or None))
            meshes.append(_MeshData(object_id, vertices_z, triangles))
    return meshes


def _cluster_planes(z_values: list[float], tol: float) -> list[dict]:
    """Chain-cluster vertex Z values into planes within `tol` of their neighbour."""
    order = sorted(range(len(z_values)), key=lambda i: z_values[i])
    planes: list[dict] = []
    for i in order:
        z = z_values[i]
        if planes and z - planes[-1]["z_values"][-1] <= tol:
            planes[-1]["z_values"].append(z)
            planes[-1]["indices"].append(i)
        else:
            planes.append({"z_values": [z], "indices": [i]})
    return planes


def _vertex_plane_map(planes: list[dict]) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for plane_id, plane in enumerate(planes):
        for idx in plane["indices"]:
            mapping[idx] = plane_id
    return mapping


def _plane_stats(
    triangles: list[_TriangleInfo], planes: list[dict], vertex_plane: dict[int, int]
) -> list[dict]:
    stats = []
    for plane_id, plane in enumerate(planes):
        flat = 0
        painted_flat = 0
        for tri in triangles:
            ids = {vertex_plane[tri.v1], vertex_plane[tri.v2], vertex_plane[tri.v3]}
            if ids == {plane_id}:
                flat += 1
                if tri.paint_color:
                    painted_flat += 1
        z_mean = sum(plane["z_values"]) / len(plane["z_values"])
        stats.append(
            {
                "z": z_mean,
                "vertex_count": len(plane["indices"]),
                "flat_faces": flat,
                "painted_flat_faces": painted_flat,
                "painted_fraction": (painted_flat / flat) if flat else 0.0,
            }
        )
    return stats


def _majority_slot(
    triangles: list[_TriangleInfo],
    vertex_plane: dict[int, int],
    plane_id: int,
) -> int | None:
    counter: Counter[int] = Counter()
    for tri in triangles:
        ids = {vertex_plane[tri.v1], vertex_plane[tri.v2], vertex_plane[tri.v3]}
        if ids != {plane_id} or not tri.paint_color:
            continue
        node = paint_codec_reference.decode(tri.paint_color)
        if isinstance(node, Leaf) and node.state != 0:
            counter[node.state] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _select_detail_plane(
    mesh: _MeshData,
    slot: int | None,
    target_z: float | None,
    plane_tol: float,
    min_plane_verts: int,
    min_painted_fraction: float,
) -> dict:
    """Return plane table plus either a resolved {plane_id, vertex_plane, slot} or an error."""
    if not mesh.vertices_z:
        return {"planes": [], "error": "mesh has no vertices"}

    planes = _cluster_planes(mesh.vertices_z, plane_tol)
    vertex_plane = _vertex_plane_map(planes)
    stats = _plane_stats(mesh.triangles, planes, vertex_plane)

    if target_z is not None:
        plane_id = min(range(len(stats)), key=lambda i: abs(stats[i]["z"] - target_z))
    else:
        candidates = [i for i, s in enumerate(stats) if s["vertex_count"] >= min_plane_verts]
        if not candidates:
            return {
                "planes": stats,
                "error": (
                    f"no plane has at least min_plane_verts={min_plane_verts} vertices -- "
                    "pass target_z explicitly"
                ),
            }
        plane_id = max(candidates, key=lambda i: stats[i]["painted_fraction"])
        if stats[plane_id]["painted_fraction"] < min_painted_fraction and slot is None:
            return {
                "planes": stats,
                "error": (
                    "no plane has enough existing paint to auto-detect the detail plane "
                    "(refusing to guess) -- pass slot and target_z explicitly"
                ),
            }

    resolved_slot = slot
    if resolved_slot is None:
        resolved_slot = _majority_slot(mesh.triangles, vertex_plane, plane_id)
        if resolved_slot is None:
            return {
                "planes": stats,
                "error": (
                    "could not infer a filament slot from existing paint on the detail "
                    "plane -- pass slot explicitly"
                ),
            }

    return {
        "planes": stats,
        "plane_id": plane_id,
        "vertex_plane": vertex_plane,
        "slot": resolved_slot,
    }


def _classify_triangles(
    triangles: list[_TriangleInfo],
    vertex_plane: dict[int, int],
    plane_id: int,
    slot: int,
) -> tuple[list[str | None], str, dict]:
    paint_code = paint_codec_reference.encode(Leaf(state=slot))
    decisions: list[str | None] = []
    newly_painted = recoloured = already_correct = skipped_multi_region = 0

    for tri in triangles:
        touches = plane_id in (
            vertex_plane[tri.v1],
            vertex_plane[tri.v2],
            vertex_plane[tri.v3],
        )
        if not touches:
            decisions.append(None)
            continue

        if tri.paint_color is None:
            decisions.append(paint_code)
            newly_painted += 1
            continue

        node = paint_codec_reference.decode(tri.paint_color)
        if isinstance(node, Split):
            decisions.append(None)
            skipped_multi_region += 1
        elif node.state == slot:
            decisions.append(None)
            already_correct += 1
        else:
            decisions.append(paint_code)
            recoloured += 1

    counts = {
        "faces_newly_painted": newly_painted,
        "faces_recoloured": recoloured,
        "faces_already_correct": already_correct,
        "faces_skipped_multi_region": skipped_multi_region,
    }
    return decisions, paint_code, counts


def repair_embossed_paint(
    path: str | Path,
    output_path: str | Path,
    slot: int | None = None,
    target_z: float | None = None,
    plane_tol: float = 1e-3,
    min_plane_verts: int = 32,
    min_painted_fraction: float = 0.5,
    dry_run: bool = False,
) -> dict:
    path = Path(path)
    output_path = Path(output_path)

    with zipfile.ZipFile(path) as zf:
        model_paths = threemf_model.all_model_paths(zf)
        original_bytes = {name: zf.read(name) for name in model_paths}

    mesh_reports: list[dict] = []
    updated_entries: dict[str, bytes] = {}
    total_changed = 0
    any_resolved = False

    for model_path in model_paths:
        raw = original_bytes[model_path]
        root = ET.fromstring(raw)
        meshes = _parse_meshes(root)
        if not meshes:
            continue

        text = raw.decode("utf-8")
        file_decisions: list[str | None] = []
        file_changed = 0

        for mesh in meshes:
            selection = _select_detail_plane(
                mesh, slot, target_z, plane_tol, min_plane_verts, min_painted_fraction
            )
            report = {
                "model_path": model_path,
                "object_id": mesh.object_id,
                "planes": selection["planes"],
            }

            if "error" in selection:
                report["error"] = selection["error"]
                mesh_reports.append(report)
                file_decisions.extend([None] * len(mesh.triangles))
                continue

            any_resolved = True
            decisions, paint_code, counts = _classify_triangles(
                mesh.triangles,
                selection["vertex_plane"],
                selection["plane_id"],
                selection["slot"],
            )
            report.update(
                {
                    "detail_plane_z": selection["planes"][selection["plane_id"]]["z"],
                    "slot": selection["slot"],
                    "paint_code": paint_code,
                    **counts,
                }
            )
            mesh_reports.append(report)
            file_decisions.extend(decisions)
            file_changed += counts["faces_newly_painted"] + counts["faces_recoloured"]

        if file_changed:
            try:
                new_text, changed = apply_triangle_decisions(text, file_decisions)
            except TrianglePatchError as exc:
                raise RepairEmbossError(str(exc)) from exc
            if changed != file_changed:
                raise RepairEmbossError(
                    f"decision/patch count mismatch in {model_path}: "
                    f"expected {file_changed}, patched {changed}"
                )
            updated_entries[model_path] = new_text.encode("utf-8")
        total_changed += file_changed

    if not any_resolved:
        return {
            "error": (
                "could not determine a detail plane for any mesh in this file -- "
                "pass slot and target_z explicitly"
            ),
            "output_path": None,
            "meshes": mesh_reports,
        }

    if total_changed == 0:
        return {
            "already_complete": True,
            "output_path": None,
            "triangles_changed": 0,
            "meshes": mesh_reports,
        }

    if dry_run:
        return {
            "dry_run": True,
            "output_path": None,
            "triangles_changed": total_changed,
            "meshes": mesh_reports,
        }

    archive.repackage_with_updates(path, output_path, updated_entries)
    validation = archive.validate_archive(output_path)
    if not validation["ok"]:
        raise RepairEmbossError(f"output archive failed validation: {validation}")

    return {
        "output_path": str(output_path),
        "triangles_changed": total_changed,
        "meshes": mesh_reports,
        "validation": validation,
    }
