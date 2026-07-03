# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

`threemf-paint-mcp` is a local MCP server for inspecting and recoloring
Bambu Studio / OrcaSlicer `.3mf` project files — specifically their
per-triangle multi-color paint data (MMU segmentation), which is not
documented anywhere officially and is normally only editable by hand-painting
in the slicer GUI.

Primary use case: swap one filament slot for another across a whole model
(e.g. "replace every black-painted triangle with the green that's already in
use") without re-painting, and without touching mesh geometry.

## Why this exists

A `.3mf` is a ZIP archive of XML. Bambu Studio stores per-triangle paint as a
`paint_color` attribute on `<triangle>` elements inside `3D/Objects/*.model`.
That attribute is **not** a hex color — it's a bit-packed serialization of a
per-triangle quadtree (triangles can be subdivided so one face carries
multiple paint regions). The encoding comes from PrusaSlicer's
`TriangleSelector::serialize()` / `deserialize()` (Bambu forked this code),
in `src/libslic3r/TriangleSelector.cpp` and `Model.cpp`. There is an open,
unresolved PrusaSlicer docs request for this
(prusa3d/PrusaSlicer#13900) — so treat the codec in this repo as the
reference implementation, sourced directly from upstream C++.

A working, round-trip-verified Python decoder/encoder already exists —
see `reference/paint_codec_reference.py` in this repo. It was validated
against a real multi-plate Bambu project: all distinct `paint_color` values
in that file round-tripped byte-exact, and structural (split-tree) integrity
was verified to be preserved after remapping leaf colors. Port logic from
that file; don't re-derive the bit format from scratch.

## Encoding cheat sheet

- Filament/slot colors live in `Metadata/project_settings.config`
  (JSON) under `filament_colour`, a 0-indexed array. Slot number = array
  index + 1.
- `extruder_colour` in the same file is the single-color fallback,
  usually irrelevant once paint data exists.
- Each object's **base** (unpainted) color is
  `Metadata/model_settings.config` → `<object><metadata key="extruder"
  value="N"/>` — `N` is the slot number, 1-indexed.
- Triangle `paint_color` strings decode to a leaf-state number per painted
  region. State `0` = "none / inherit base extruder". State `N` for `N >=
  1` = filament slot `N`.
- Bit layout (see reference file for exact code):
  - 2 bits: number of split sides (0 = leaf, 1–3 = internal node with
    2–4 children)
  - leaf, state 0–2: 2 more bits, done (4 bits total)
  - leaf, state 3–16: `11` marker + 4-bit `(state-3)` (8 bits total)
  - leaf, state 17–255: `11` marker + `1110` marker + 8-bit `(state-17)`
    (16 bits total)
  - split node: 2 bits `special_side`, then children recursively, each in
    the same nibble-based format
  - the hex string in the XML attribute is the bitstream chunked into
    4-bit nibbles **and reversed** — decode by reading the string
    right-to-left.
- **Never do naive substring replacement** on `paint_color` values (e.g.
  regex-replacing `"8"` with `"4"`). Codes are not fixed-width and can
  appear as substrings of longer/different codes. Always decode → walk the
  tree → remap only leaf `state` integers → re-encode.
- One `<object>` can reference multiple `.model` files under
  `3D/Objects/` (via `p:path` on `<component>` in the root
  `3D/3dmodel.model`) — a recolor across "the whole model" must walk every
  referenced object file, not just the first one.

## MCP server design

Runtime: Python, using the official `mcp` SDK (`FastMCP`), run over stdio.
Reuse `reference/paint_codec_reference.py` as the core codec module —
don't inline a second copy of the bit logic in the server.

### Tools to expose

- `inspect_3mf(path)` — returns filament palette (slot → hex, from
  `filament_colour`), object list with base extruder per object, and
  actual paint usage counts per slot (walk every triangle's leaves, not
  just the palette — a slot can be defined but never painted, or painted
  far more than the base color; this distinction matters and bit us once
  already: don't assume "slot 1" is the intended color just because it's
  first).
- `recolor_slots(path, mapping, output_path)` — mapping is `{old_slot:
  new_slot}` (ints). Applies across every object `.model` file in the
  archive. Must:
  1. Decode every distinct `paint_color` value once (cache — many
     triangles repeat the same string).
  2. Round-trip-verify the decode/encode before trusting a mapping
     (assert identity transform reproduces the original string).
  3. Verify split-tree structure is unchanged after a real remap — only
     leaf `state` ints may differ.
  4. Rewrite via in-place ZIP entry update (see "Repackaging" below), not
     a full archive rebuild.
  5. Return a summary: triangles changed, distinct codes changed, slots
     now unused (so the caller/human can decide whether to prune the
     palette).
- `list_plates(path)` — parse `Metadata/model_settings.config` `<plate>`
  blocks and return, per plate: `plater_id`, `plater_name`, the object IDs
  placed on it (`<model_instance>` → `object_id`/`instance_id`), and its
  thumbnail paths (`thumbnail_file`, `thumbnail_no_light_file`, `top_file`,
  `pick_file`).
- `extract_plate(path, plater_id, output_path)` — pull one plate out of a
  multi-plate project into a standalone single-plate `.3mf`. See "Plate
  extraction" below.
- `validate_3mf(path)` — CRC test the zip, parse every `.model` file's
  XML, confirm triangle counts unchanged vs. a reference copy. Run this
  automatically at the end of `recolor_slots`, not just as an
  optionally-called tool.

### Repackaging rule

When only mesh/metadata files change, update those entries **in place**
inside a copy of the original ZIP (`zip <archive> <changed-file>`) rather
than rebuilding the archive from an extracted directory. This preserves
entry order, compression choices, and avoids stripping anything Bambu
Studio's reader is stricter about than Python's `zipfile`. Confirmed
working approach from the manual session that preceded this repo:
`cp original.3mf working.3mf && zip working.3mf path/to/changed/file`.
After repackaging, always test-open with `zipfile.testzip()` plus an
`xml.etree.ElementTree.parse()` of every touched file before returning
success to the caller.

### Plate extraction

Bambu Studio project `.3mf` files can hold multiple build plates in one
archive. There's no slicer button that exports "just this plate" cleanly —
the closest built-in workaround (delete the other plates, then Save As) is
manual and tends to leave orphaned metadata behind. `extract_plate` should
do this properly.

Verified against a real single-plate export from this session — confirm
each point against an actual multi-plate `.3mf` before relying on it, since
everything here was inferred from one plate and cross-checked against the
3MF/Bambu metadata layout rather than a multi-plate sample:

1. **Read `Metadata/model_settings.config`.** It contains one `<object>`
   block per object (with its `<part>` children and transform metadata)
   and one `<plate>` block per plate. Each `<plate>` has
   `<model_instance>` children that reference `object_id` /
   `instance_id` — this is the join key between "objects" and "which
   plate they sit on."
2. **Resolve the target plate's object set.** Collect every `object_id`
   referenced by the target plate's `<model_instance>` elements. An
   object can in principle be referenced by more than one plate
   (duplicated instances) — don't assume 1:1.
3. **Filter `3D/3dmodel.model`.** Keep only `<object>` resources (and
   their `<component>` references into `3D/Objects/*.model`) whose id is
   in the resolved set from step 2; drop the rest. Keep only the
   `<build>` `<item>` entries for those object ids.
4. **Filter `3D/Objects/*.model`.** Drop any per-object mesh file that
   isn't referenced by a surviving component. For files that stay, no
   internal changes needed — mesh and paint data travel as-is.
5. **Filter `model_settings.config` itself.** Keep only the matching
   `<object>` blocks and the one target `<plate>` block; drop the
   `<assemble>` entries for objects no longer present.
6. **Carry over plate-specific files.** `Metadata/plate_<plater_id>.png`,
   `plate_<plater_id>_small.png`, `plate_no_light_<plater_id>.png`,
   `top_<plater_id>.png`, `pick_<plater_id>.png` — copy these, drop the
   equivalents for other plates. Renumber to `plate_1*` in the new archive
   if downstream tooling assumes plate 1, and update the
   `thumbnail_file` / `top_file` / `pick_file` paths in the rewritten
   `model_settings.config` to match.
7. **`Metadata/project_settings.config` and `filament_sequence.json`** are
   project-wide (not per-plate) in every sample seen so far — copy as-is.
   If a multi-plate test file turns out to have per-plate AMS/filament-map
   overrides, that's a gap in this plan, not an assumption to paper over —
   surface it rather than guessing.
8. **`Metadata/slice_info.config`** contains per-plate slicing metadata;
   filter it the same way as the plate/object metadata if it turns out to
   contain multiple plates' worth of data (unverified — inspect a real
   multi-plate sample before assuming its shape).
9. **Update `[Content_Types].xml` and the `.rels` files** only if entries
   are being dropped that they reference (e.g. a removed thumbnail) —
   don't leave dangling relationship references.
10. **Repackage** using the same in-place-update-on-a-copy approach as
    recoloring, or a fresh archive if enough entries changed that in-place
    editing is no longer simpler — either way, run `validate_3mf` on the
    output before returning success.

Flag clearly to the caller if a project has plate-level filament/AMS
mapping that doesn't cleanly separate per-plate — that's the one part of
this plan resting on an assumption rather than something confirmed against
a multi-plate file.

## Testing

- Keep the round-trip self-test from `reference/paint_codec_reference.py`
  (encode(decode(x)) == x for identity mapping) as an actual test file,
  not just a manual check — it's cheap and it's the single most important
  correctness guarantee in this codebase.
- Add a fixture `.3mf` (small, single-object, a handful of painted
  triangles including at least one subdivided/multi-region triangle) and
  assert against it in CI-style tests rather than only testing against
  ad hoc user-uploaded files.
- Add a second fixture that's genuinely multi-plate (2–3 plates, at least
  one plate with more than one object) before trusting `extract_plate` —
  the plan in "Plate extraction" above was written against a single-plate
  file and needs validation against the real thing, especially step 7
  (whether filament/AMS mapping is ever plate-specific).
- Any bug in the codec is silent data corruption, not a crash — the XML
  will still parse and the zip will still open, but colors will be wrong
  in ways that are tedious to spot visually. Prioritize the round-trip and
  structure-preservation assertions over "does it look right" checks.

## Non-goals for v1

- No mesh geometry editing.
- No support for non-Bambu/Orca-flavored `.3mf` (plain 3MF spec files
  without the `paint_color` extension can be read for geometry but paint
  tools should no-op cleanly rather than error).
- No G-code or slicing logic.
