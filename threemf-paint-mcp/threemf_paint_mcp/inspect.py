"""`inspect_3mf` logic: filament palette, object base colors, actual paint usage."""

from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path

from reference import paint_codec_reference
from threemf_paint_mcp import threemf_model


def inspect_archive(path: str | Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        palette = threemf_model.get_filament_colours(zf)
        base_extruders = threemf_model.get_object_base_extruders(zf)
        model_paths = threemf_model.all_model_paths(zf)

        usage: Counter[int] = Counter()
        for model_path in model_paths:
            for painted in threemf_model.iter_painted_triangles(zf, model_path):
                tree = paint_codec_reference.decode(painted.paint_color)
                for state in paint_codec_reference.collect_leaf_states(tree):
                    usage[state] += 1

    unpainted_regions = usage.pop(0, 0)
    objects = [
        {"object_id": object_id, "base_extruder_slot": slot}
        for object_id, slot in sorted(base_extruders.items(), key=lambda kv: int(kv[0]))
    ]
    return {
        "filament_palette": {str(slot): colour for slot, colour in sorted(palette.items())},
        "objects": objects,
        "paint_usage_by_slot": {str(slot): count for slot, count in sorted(usage.items())},
        "unpainted_leaf_regions": unpainted_regions,
    }
