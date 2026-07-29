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

## 3MF colour painting — domain notes

Everything below was established empirically against Bambu Studio output;
the slicer's on-disk paint format is undocumented, so treat these as
load-bearing invariants and re-verify before changing any of them.

### Archive layout

A `.3mf` is a plain ZIP. The entries that matter:

| Entry | Format | Notes |
| --- | --- | --- |
| `3D/3dmodel.model` | XML | Root model; may hold objects or only references |
| `3D/Objects/*.model` | XML | Meshes; vertex indices are per-`<mesh>` |
| `Metadata/model_settings.config` | XML | Base extruder, plate layout |
| `Metadata/project_settings.config` | **JSON** | Not XML — see below |
| `Metadata/plate_*.png` | PNG | Cached thumbnails; stale after any edit |

**Gotcha:** `project_settings.config` is JSON while `model_settings.config`
is XML. Any validator that dispatches on the `.config` suffix will report a
bogus parse failure — `archive.validate_archive` sniffs by attempting XML
first and falling back to JSON for `.config` entries, rather than
dispatching on suffix.

The filament palette is `filament_colour` in `project_settings.config` — a
1-based array of hex strings. An object's base colour is
`<metadata key="extruder" value="N"/>` in `model_settings.config`, and it is
**not** necessarily slot 1.

### The `paint_color` attribute

Painted triangles carry `paint_color` on `<triangle>`. An absent attribute
means "use the object's base extruder" — it does **not** mean slot 1.

The value is a hex-encoded serialised split-tree (PrusaSlicer's
`TriangleSelector`, which Bambu inherited — see
`reference/paint_codec_reference.py` for the full bit layout). For an
unsplit triangle the stream is: 2 bits of split info (`00` = leaf), then the
state. States 1–2 fit inline; state ≥ 3 writes the escape `0b11` followed by
4 bits holding `state - 3`.

For a **leaf** triangle painted with a single filament slot:

```text
slot <= 2:  code = slot << 2
slot >= 3:  code = 12 + (slot - 3) * 16
```

```text
1 -> "4"    5 -> "2C"    9 -> "6C"    13 -> "AC"
2 -> "8"    6 -> "3C"   10 -> "7C"    14 -> "BC"
3 -> "C"    7 -> "4C"   11 -> "8C"    15 -> "CC"
4 -> "1C"   8 -> "5C"   12 -> "9C"    16 -> "DC"
```

Decoding rule: if the low two bits are non-zero the node is **split** — a
partially painted triangle subdivided into a quadtree. `repair_emboss.py`
never touches these (`faces_skipped_multi_region` in its report) rather than
guessing how to paint a subdivided face; only plain `Leaf` triangles get
recoloured or newly painted.

### The emboss side-wall trap

The most common real-world defect, and the reason `repair_embossed_paint`
exists.

Bambu Studio's paint brush only marks faces visible from the current camera
angle. On raised text viewed from above, the glyph **top faces** get painted
and the **vertical side walls** do not. The 3D preview looks correct from
directly overhead, and the model prints with a rim of base colour around
every letter. Engraved text fails the same way, one plane down.

Symptom to look for: a mesh where triangles lying flat on a plane are
~100% painted while triangles straddling that plane and the one below are
~0% painted.

### Plane structure of embossed detail

Flat-topped emboss on a flat plate collapses to a small number of distinct
vertex Z values. A typical sign:

```text
z = -2.1   68 verts   plate bottom
z =  0.9  3838 verts  plate top surface
z =  2.1  3768 verts  detail (glyph) tops
```

Triangle classification follows directly:

- all three vertices on the detail plane → glyph top face
- vertices split between detail plane and plate top → glyph **side wall**
- all three on the plate top → surrounding plate, must stay base colour

So the complete glyph is `every triangle with at least one vertex on the
detail plane`. That single predicate covers tops and walls and excludes the
plate — see `_classify_triangles` in
[repair_emboss.py](threemf_paint_mcp/repair_emboss.py).

### Detecting the detail plane

Do **not** assume the detail plane is the topmost one — that breaks on
engraved text, where the detail sits below the plate surface.

`repair_embossed_paint` uses the existing paint as the signal instead:
**the plane the user already painted is the plane they meant.** It clusters
vertex Z into planes (`_cluster_planes`, chained within `plane_tol`), and
for each plane computes the painted fraction of its flat faces
(`_plane_stats`). The detail plane is the one with the highest painted
fraction, gated by `min_painted_fraction` so a barely-painted plane isn't
trusted over genuine ambiguity.

This generalises to raised and recessed detail with no mode flag, and it
can never pick the wrong colour, because the filament code is read out of
the file rather than inferred. With a completely unpainted model, or when no
plane meets `min_painted_fraction`, it refuses (returns an `error` field,
writes nothing) and asks for `slot` + `target_z` explicitly, rather than
guessing — see `tests/test_repair_emboss.py::test_repair_refuses_on_completely_unpainted_mesh`.

### Safe rewriting rules

- **Never re-serialise a `.model` through `ElementTree`.** It rewrites
  namespace prefixes and attribute order and drops `p:UUID` formatting that
  the slicer depends on. `ElementTree` is used read-only for analysis in
  both `recolor.py` and `repair_emboss.py`; the actual edit is a targeted
  text splice at the matched `<triangle>` tag spans, leaving the rest of the
  document byte-identical.
- Vertex indices are scoped to the enclosing `<mesh>`. `repair_emboss.py`
  builds a fresh vertex→plane map per mesh for exactly this reason — never
  reuse one mesh's vertex table for another.
- When rewriting the ZIP, preserve entry order, `compress_type`,
  `date_time` and the attribute fields — `archive.repackage_with_updates`
  shells out to `zip` on a copy of the original archive rather than
  rebuilding, for this reason.
- Painting changes no geometry, so `face_count` and `mesh_stat` in
  `model_settings.config` stay valid — leave them alone.
- Cached thumbnails go stale and will show the old colours in Finder or any
  file browser. The slicer regenerates them on the next slice; do not
  attempt to re-render them.
- Write to a new path by default. These are the user's project files.

### Tool: `repair_embossed_paint`

Finishes a partial paint job on embossed or engraved detail. Implemented in
[repair_emboss.py](threemf_paint_mcp/repair_emboss.py), exposed via
[server.py](threemf_paint_mcp/server.py).

```python
repair_embossed_paint(
    path: str,
    output_path: str,
    slot: int | None = None,          # force a slot instead of reusing existing
    target_z: float | None = None,    # force the detail plane
    plane_tol: float = 1e-3,          # mm; coplanarity tolerance
    min_plane_verts: int = 32,        # ignore sparse planes
    min_painted_fraction: float = 0.5,
    dry_run: bool = False,
) -> dict
```

Behaviour:

- Auto-detects the detail plane and reuses its filament code. No
  parameters needed in the normal case.
- Paints every triangle touching that plane; leaves the surrounding
  surface at the object's base extruder.
- **Idempotent** — a second run reports `already_complete: true` and writes
  nothing (`output_path` is `None`).
- Refuses rather than guesses when no plane carries existing paint (returns
  `error`, writes nothing).
- Validates the output (ZIP CRC + XML/JSON parse) before returning, same as
  `recolor_slots`.

Returns a per-mesh report (`meshes: [...]`): the plane table (vertex
counts and painted fractions per plane), the chosen `detail_plane_z`, the
`paint_code` and decoded `slot`, and counts of faces
`faces_newly_painted` / `faces_recoloured` / `faces_already_correct` /
`faces_skipped_multi_region`.

Test coverage in `tests/test_repair_emboss.py` covers: the basic wall-gap
repair, idempotency (both "re-run own output" and "already fully painted
input"), `dry_run`, refusing on a fully unpainted mesh, the explicit
`slot`+`target_z` override on an unpainted mesh, and the engraved case
(picks the painted floor, not the unpainted top plane).

### Object-scoped recoloring: two id spaces

`model_settings.config` (extruder, and the `name` a user gave an object in
Bambu Studio's outliner) and plate `model_instance` references all key off
one **root-level object id**. But a root `<object>` in `3D/3dmodel.model`
usually doesn't hold triangles itself — it holds
`<component objectid="..." p:path="...">` pointers into a
`3D/Objects/*.model` file, and that file's *own* `<object id>` (where the
`<triangle>` elements actually live) is a **different id space** from the
root id. Recoloring "just this object" has to bridge the two, or a filter
keyed on the root id silently matches nothing in the mesh file.

`threemf_model.ObjectRef` + `get_object_refs()` do that bridging: one
`ObjectRef` per (root_object_id → model_path, mesh_object_id). `recolor.py`
resolves requested root ids into this per-file scope
(`_scope_by_path`) once per call, then filters at the triangle level while
walking each model file — never a whole-file skip, since one file can hold
multiple objects.

- `recolor_slots(..., object_ids=[...])` — same remap as before, restricted
  to the given root-level object ids (`inspect_3mf`'s `objects[].object_id`).
  Omitting `object_ids` keeps the old whole-archive behaviour. Raises if a
  requested id doesn't resolve to any object in the archive.
- `recolor_by_name(path, object_name, target_slot, output_path)` — matches
  objects by `name` (case-insensitive substring, e.g. `"Text"`), collects
  every slot currently painted anywhere on the matched objects, and remaps
  all of them to `target_slot` via `recolor_slots(object_ids=...)`. Refuses
  (raises) if the name matches nothing, rather than silently no-op-ing.
  Reports `already_correct: true` / `output_path: None` and writes nothing
  if the matched objects are already entirely `target_slot`. Does **not**
  touch triangles with no `paint_color` attribute at all — those inherit
  the object's base extruder, which lives in `model_settings.config`, not
  in triangle paint data.
- `inspect_3mf`'s `objects[]` now also returns `name` (`None` if the object
  has no `name` metadata) and `base_extruder_slot` (now `None` rather than
  omitted, if the object has no extruder metadata) — `name` is what
  `recolor_by_name` matches against.

`threemf_paint_mcp/patch.py` holds the shared `<triangle>`-tag text-splice
helper (`apply_triangle_decisions`) used by both `recolor.py` and
`repair_emboss.py`, per the "never re-serialise through ElementTree" rule
above.

Test coverage in `tests/test_recolor.py` (`build_named_objects_fixture` in
`tests/fixtures.py`, which deliberately gives its two objects root ids
`"1"`/`"2"` distinct from their mesh-internal ids `"10"`/`"20"` to exercise
the id-space bridge) covers: `object_ids` scoping leaving an out-of-scope
object's same-slot paint untouched, a missing `object_ids` entry raising,
name matching, no-match raising, and the already-correct no-write case.
