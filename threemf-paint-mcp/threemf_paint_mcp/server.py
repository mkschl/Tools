"""MCP server exposing .3mf paint inspection/recoloring tools over stdio."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from threemf_paint_mcp import archive
from threemf_paint_mcp.inspect import inspect_archive
from threemf_paint_mcp.plates import extract_plate as _extract_plate
from threemf_paint_mcp.plates import list_plates as _list_plates
from threemf_paint_mcp.recolor import recolor_slots as _recolor_slots

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
def recolor_slots(path: str, mapping: dict[str, int], output_path: str) -> dict:
    """Remap filament slots across every painted triangle in a .3mf.

    `mapping` is {old_slot: new_slot} (slot numbers as given by inspect_3mf).
    Applies across every object .model file in the archive, decodes each
    distinct paint_color value once, verifies round-trip and split-tree
    structure before trusting the remap, and writes the result via
    in-place ZIP entry updates. Runs validation on the output before
    returning.
    """
    return _recolor_slots(path, mapping, output_path)


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
def validate_3mf(path: str) -> dict:
    """CRC-test the zip and parse every .model/.config/.xml/.rels entry's XML."""
    return archive.validate_archive(Path(path))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
