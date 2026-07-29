"""MCP server exposing .3mf paint inspection/recoloring tools over stdio."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from threemf_paint_mcp import archive
from threemf_paint_mcp.coverage import get_coverage_report as _get_coverage_report
from threemf_paint_mcp.diff import diff_3mf as _diff_3mf
from threemf_paint_mcp.inspect import inspect_archive
from threemf_paint_mcp.paint_region import paint_region as _paint_region
from threemf_paint_mcp.palette import swap_filament_palette as _swap_filament_palette
from threemf_paint_mcp.plates import extract_plate as _extract_plate
from threemf_paint_mcp.plates import list_plates as _list_plates
from threemf_paint_mcp.recolor import recolor_by_name as _recolor_by_name
from threemf_paint_mcp.recolor import recolor_slots as _recolor_slots
from threemf_paint_mcp.repair_emboss import (
    repair_embossed_paint as _repair_embossed_paint,
)

mcp = FastMCP("threemf-paint-mcp")


@mcp.tool()
def inspect_3mf(path: str) -> dict:
    """Inspect a .3mf's filament palette, object base colors, and actual paint usage.

    Returns the filament palette (slot -> hex color), the base extruder
    slot per object, and how many painted-triangle leaf regions actually
    use each slot -- a slot can be defined in the palette but never
    painted, or painted far more than the base color, so don't assume
    "slot 1" is the intended color just because it's first.
    """
    return inspect_archive(path)


@mcp.tool()
def recolor_slots(
    path: str,
    mapping: dict[str, int],
    output_path: str,
    object_ids: list[str] | None = None,
) -> dict:
    """Remap filament slots across painted triangles in a .3mf.

    `mapping` is {old_slot: new_slot} (slot numbers as given by inspect_3mf).
    By default applies across every object .model file in the archive;
    pass `object_ids` (root-level object ids from inspect_3mf's
    `objects[].object_id`) to scope the remap to specific objects only.
    Decodes each affected paint_color value once, verifies round-trip and
    split-tree structure before trusting the remap, and writes the result
    via in-place ZIP entry updates. Runs validation on the output before
    returning.
    """
    return _recolor_slots(path, mapping, output_path, object_ids=object_ids)


@mcp.tool()
def recolor_by_name(path: str, object_name: str, target_slot: int, output_path: str) -> dict:
    """Recolor every object whose name matches `object_name` to `target_slot`.

    Matches (case-insensitive substring) against the object `name`
    inspect_3mf exposes per object -- the name a user gave the object in
    Bambu Studio's outliner (e.g. "Text"). Remaps every filament slot
    currently painted anywhere on the matched objects to `target_slot`;
    look up `target_slot` from inspect_3mf's filament_palette first (e.g.
    the slot whose hex is #000000 for "black"). Refuses (raises) rather
    than silently doing nothing if no object name matches. Does not touch
    triangles with no paint_color attribute at all -- those inherit the
    object's base extruder, which is separate from triangle paint data.
    """
    return _recolor_by_name(path, object_name, target_slot, output_path)


@mcp.tool()
def list_plates(path: str) -> list[dict]:
    """List build plates in a multi-plate .3mf project.

    For each plate: plater_id, plater_name, the object IDs placed on it,
    and its thumbnail file paths.
    """
    return _list_plates(path)


@mcp.tool()
def extract_plate(path: str, plater_id: str, output_path: str) -> dict:
    """Pull one build plate out of a multi-plate .3mf into a standalone file.

    See the tool result's `warnings` list for parts of this operation that
    rest on assumptions not yet verified against a real multi-plate
    sample (e.g. whether AMS/filament-map config is ever per-plate).
    """
    return _extract_plate(path, plater_id, output_path)


@mcp.tool()
def repair_embossed_paint(
    path: str,
    output_path: str,
    slot: int | None = None,
    target_z: float | None = None,
    plane_tol: float = 1e-3,
    min_plane_verts: int = 32,
    min_painted_fraction: float = 0.5,
    dry_run: bool = False,
) -> dict:
    """Finish a partial paint job on raised or engraved detail (the emboss side-wall trap).

    Bambu Studio's paint brush only marks faces visible from the current
    camera angle, so glyph top faces often end up painted while their
    vertical side walls don't -- the preview looks fine from directly
    overhead but the model prints with a rim of base colour around every
    letter. This clusters mesh vertices into Z planes, finds the plane the
    user already painted (highest painted fraction of its flat faces --
    works for both raised and engraved detail, no mode flag needed), and
    paints every triangle touching that plane with the slot already in use
    there. Refuses rather than guesses when no plane carries existing
    paint; pass `slot` and `target_z` to override explicitly. Idempotent --
    a second run reports `already_complete` and writes nothing. Set
    `dry_run=True` to preview counts without writing.
    """
    return _repair_embossed_paint(
        path,
        output_path,
        slot=slot,
        target_z=target_z,
        plane_tol=plane_tol,
        min_plane_verts=min_plane_verts,
        min_painted_fraction=min_painted_fraction,
        dry_run=dry_run,
    )


@mcp.tool()
def swap_filament_palette(path: str, mapping: dict[str, str], output_path: str) -> dict:
    """Reassign filament slot colors without touching any painted triangle.

    `mapping` is {slot_number: new_hex_color} (slot numbers from
    inspect_3mf's filament_palette, hex as "#RRGGBB" or "#RRGGBBAA"). This
    is the complement of recolor_slots: recolor_slots changes which slot a
    triangle uses, this changes what a slot's color *is* -- e.g. "slot 3 is
    now this orange instead of that yellow" without repainting anything.
    Slots not mentioned in `mapping` are left unchanged. Raises if a slot
    isn't in the current palette or a color isn't valid hex. Reports
    `already_correct: true` and writes nothing if the requested colors
    already match.
    """
    return _swap_filament_palette(path, mapping, output_path)


@mcp.tool()
def get_paint_coverage_report(path: str, object_ids: list[str] | None = None) -> dict:
    """Report actual painted surface area per object per slot, not just triangle counts.

    Triangle counts (inspect_3mf's paint_usage_by_slot) can be misleading:
    a handful of large unpainted triangles can be most of the visible
    surface even if they're a small fraction of the triangle count, or vice
    versa. This computes each triangle's real area from its vertex
    positions and buckets it per object into unpainted_area, split_area
    (multi-region/subdivided triangles -- never guessed at, reported
    separately), and area_by_slot. Pass `object_ids` (root-level object ids
    from inspect_3mf's `objects[].object_id`) to scope to specific objects.
    Read-only -- makes no changes to the archive.
    """
    return _get_coverage_report(path, object_ids=object_ids)


@mcp.tool()
def paint_region(
    path: str,
    output_path: str,
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

    Any bound left unset is unbounded on that axis -- e.g. only setting
    z_min/z_max selects by Z range regardless of X/Y. At least one bound
    is required; use recolor_slots/recolor_by_name to paint a whole
    object instead. mode="any" (default) selects a triangle if any vertex
    is inside the box; mode="all" requires every vertex inside. Useful for
    painting detail that was never painted at all (no existing paint for
    repair_embossed_paint to detect a plane from) and isn't its own named
    object. Multi-region (split) triangles inside the selection are left
    untouched and reported as faces_skipped_multi_region rather than
    guessed at. Idempotent -- reports already_complete and writes nothing
    if the selection is already fully painted. Set dry_run=True to preview
    counts without writing.
    """
    return _paint_region(
        path,
        output_path,
        slot,
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        z_min=z_min,
        z_max=z_max,
        mode=mode,
        object_ids=object_ids,
        dry_run=dry_run,
    )


@mcp.tool()
def diff_3mf(path_a: str, path_b: str) -> dict:
    """Compare two .3mf files' filament palette, objects, and paint usage.

    Read-only -- makes no changes. Returns triangle_counts (total per
    side), palette_diff (slots whose hex color differs), objects_added /
    objects_removed (root-level object ids only on one side),
    objects_changed (common objects whose name or base_extruder_slot
    differs), and paint_usage_diff (slots whose painted-leaf-region count
    differs). identical is true only if none of those found a difference.
    Useful for confirming a recolor/repair tool changed exactly what was
    expected and nothing else.
    """
    return _diff_3mf(path_a, path_b)


@mcp.tool()
def validate_3mf(path: str) -> dict:
    """CRC-test the zip and parse every .model/.config/.xml/.rels entry's XML."""
    return archive.validate_archive(Path(path))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
