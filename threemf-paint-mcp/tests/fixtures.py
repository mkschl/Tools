"""Builds minimal but structurally valid Bambu-style .3mf fixtures for tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from reference.paint_codec_reference import Leaf, Split, encode

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

# One leaf-only region (slot 4) and one subdivided triangle mixing slot 4
# and slot 1 across its children -- exercises both codec paths.
PAINT_LEAF_SLOT_4 = encode(Leaf(state=4))
PAINT_SPLIT_MIXED = encode(
    Split(special_side=0, children=(Leaf(state=4), Leaf(state=1), Leaf(state=0)))
)
PAINT_LEAF_SLOT_1 = encode(Leaf(state=1))
PAINT_SLOT_2 = encode(Leaf(state=2))
PAINT_SLOT_3 = encode(Leaf(state=3))


def _object_model_xml(object_id: str, paint_colors: list[str]) -> bytes:
    triangles = "".join(
        f'<triangle v1="0" v2="1" v3="2" paint_color="{pc}"/>' for pc in paint_colors
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{CORE_NS}" unit="millimeter">
  <resources>
    <object id="{object_id}" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0"/>
          <vertex x="1" y="0" z="0"/>
          <vertex x="0" y="1" z="0"/>
        </vertices>
        <triangles>{triangles}</triangles>
      </mesh>
    </object>
  </resources>
  <build/>
</model>"""
    return xml.encode("utf-8")


def _root_model_xml(objects: list[tuple[str, str, str]]) -> bytes:
    # objects: list of (object_id, component_objectid, component_path)
    resource_objs = "".join(
        f'<object id="{oid}" type="model">'
        f'<components><component objectid="{cid}" p:path="/{path}"/></components>'
        f"</object>"
        for oid, cid, path in objects
    )
    items = "".join(
        f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>' for oid, _, _ in objects
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{CORE_NS}" xmlns:p="{PROD_NS}" unit="millimeter">
  <resources>{resource_objs}</resources>
  <build>{items}</build>
</model>"""
    return xml.encode("utf-8")


def _model_settings_xml(
    objects: list[str],
    plates: list[dict],
    object_names: dict[str, str] | None = None,
    extruders: dict[str, str] | None = None,
) -> bytes:
    object_names = object_names or {}
    extruders = extruders or {}
    object_blocks = "".join(
        f'<object id="{oid}"><metadata key="extruder" value="{extruders.get(oid, "1")}"/>'
        + (f'<metadata key="name" value="{object_names[oid]}"/>' if oid in object_names else "")
        + "</object>"
        for oid in objects
    )
    plate_blocks = []
    for plate in plates:
        instances = "".join(
            f'<model_instance><metadata key="object_id" value="{oid}"/>'
            f'<metadata key="instance_id" value="1"/></model_instance>'
            for oid in plate["object_ids"]
        )
        plate_blocks.append(
            f"<plate>"
            f'<metadata key="plater_id" value="{plate["plater_id"]}"/>'
            f'<metadata key="plater_name" value="{plate.get("plater_name", "")}"/>'
            f'<metadata key="thumbnail_file" value="Metadata/plate_{plate["plater_id"]}.png"/>'
            f"{instances}"
            f"</plate>"
        )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  {object_blocks}
  {"".join(plate_blocks)}
</config>"""
    return xml.encode("utf-8")


def _project_settings_json(filament_colours: list[str]) -> bytes:
    return json.dumps({"filament_colour": filament_colours}).encode("utf-8")


def build_single_object_fixture(tmp_path: Path) -> Path:
    """One object, one plate, one plain leaf paint and one subdivided triangle."""
    object_model = _object_model_xml(
        object_id="2", paint_colors=[PAINT_LEAF_SLOT_4, PAINT_SPLIT_MIXED]
    )
    root_model = _root_model_xml([("1", "2", "3D/Objects/object_1.model")])
    model_settings = _model_settings_xml(
        objects=["1"],
        plates=[{"plater_id": "1", "plater_name": "Plate 1", "object_ids": ["1"]}],
    )
    project_settings = _project_settings_json(["#000000", "#FF0000", "#00FF00", "#0000FF"])

    out = tmp_path / "single_object.3mf"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", root_model)
        zf.writestr("3D/Objects/object_1.model", object_model)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/project_settings.config", project_settings)
    return out


def build_named_objects_fixture(tmp_path: Path) -> Path:
    """Two named, separately-filed objects sharing a paint slot -- for object_ids/name scoping.

    Root object id "1" (name "Text") and root id "2" (name "Base") each
    reference their own 3D/Objects/*.model file, whose *internal* <object
    id> ("10"/"20") deliberately differs from the root id -- this is the
    id-space split `threemf_model.ObjectRef` exists to bridge, and both
    objects paint slot 4 so scoped-vs-unscoped recolors are distinguishable.
    """
    text_object = _object_model_xml(
        object_id="10", paint_colors=[PAINT_LEAF_SLOT_4, PAINT_LEAF_SLOT_4]
    )
    base_object = _object_model_xml(object_id="20", paint_colors=[PAINT_LEAF_SLOT_4])

    root_model = _root_model_xml(
        [
            ("1", "10", "3D/Objects/object_text.model"),
            ("2", "20", "3D/Objects/object_base.model"),
        ]
    )
    model_settings = _model_settings_xml(
        objects=["1", "2"],
        plates=[{"plater_id": "1", "plater_name": "Plate 1", "object_ids": ["1", "2"]}],
        object_names={"1": "Text", "2": "Base"},
    )
    project_settings = _project_settings_json(["#000000", "#FF0000", "#00FF00", "#0000FF"])

    out = tmp_path / "named_objects.3mf"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", root_model)
        zf.writestr("3D/Objects/object_text.model", text_object)
        zf.writestr("3D/Objects/object_base.model", base_object)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/project_settings.config", project_settings)
    return out


def _object_model_xml_explicit(
    object_id: str,
    vertices: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int, str | None]],
) -> bytes:
    """Build an object .model file from explicit vertex coordinates and triangle refs.

    Used for the emboss/plane-detection fixtures below, where (unlike the
    generic single/multi-plate fixtures) the actual Z coordinates and which
    triangles are painted matter to the test.
    """
    vertex_xml = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
    triangle_xml = "".join(
        f'<triangle v1="{v1}" v2="{v2}" v3="{v3}"' + (f' paint_color="{pc}"' if pc else "") + "/>"
        for v1, v2, v3, pc in triangles
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{CORE_NS}" unit="millimeter">
  <resources>
    <object id="{object_id}" type="model">
      <mesh>
        <vertices>{vertex_xml}</vertices>
        <triangles>{triangle_xml}</triangles>
      </mesh>
    </object>
  </resources>
  <build/>
</model>"""
    return xml.encode("utf-8")


def _embossed_box_vertices() -> list[tuple[float, float, float]]:
    # A unit cube: plate-top face at z=0 (vertices 0-3), glyph/detail face
    # at z=1 (vertices 4-7). Side walls connect the two.
    return [
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
    ]


def _embossed_box_triangles(
    top_paint: str | None, wall_paint: str | None
) -> list[tuple[int, int, int, str | None]]:
    plate_top = [(0, 1, 2, None), (0, 2, 3, None)]
    glyph_top = [(4, 5, 6, top_paint), (4, 6, 7, top_paint)]
    walls = [
        (0, 1, 5, wall_paint),
        (0, 5, 4, wall_paint),
        (1, 2, 6, wall_paint),
        (1, 6, 5, wall_paint),
        (2, 3, 7, wall_paint),
        (2, 7, 6, wall_paint),
        (3, 0, 4, wall_paint),
        (3, 4, 7, wall_paint),
    ]
    return plate_top + glyph_top + walls


def _embossed_fixture_archive(
    tmp_path: Path, name: str, top_paint: str | None, wall_paint: str | None
) -> Path:
    """A raised-glyph box: plate top z=0 (base slot), detail top z=1.

    Glyph top faces carry `top_paint`; side walls carry `wall_paint` -- set
    `wall_paint=None` to reproduce the emboss side-wall trap (top painted,
    walls left at base colour).
    """
    object_model = _object_model_xml_explicit(
        object_id="2",
        vertices=_embossed_box_vertices(),
        triangles=_embossed_box_triangles(top_paint, wall_paint),
    )
    root_model = _root_model_xml([("1", "2", "3D/Objects/object_1.model")])
    model_settings = _model_settings_xml(
        objects=["1"],
        plates=[{"plater_id": "1", "plater_name": "Plate 1", "object_ids": ["1"]}],
    )
    project_settings = _project_settings_json(["#000000", "#FF0000", "#00FF00"])

    out = tmp_path / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", root_model)
        zf.writestr("3D/Objects/object_1.model", object_model)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/project_settings.config", project_settings)
    return out


def build_embossed_wall_gap_fixture(tmp_path: Path) -> Path:
    """Glyph top (z=1) fully painted slot 2; side walls left unpainted -- the defect."""
    return _embossed_fixture_archive(
        tmp_path, "embossed_wall_gap.3mf", top_paint=PAINT_SLOT_2, wall_paint=None
    )


def build_embossed_repaired_fixture(tmp_path: Path) -> Path:
    """Same shape, but walls already painted too -- repair should be a no-op."""
    return _embossed_fixture_archive(
        tmp_path,
        "embossed_repaired.3mf",
        top_paint=PAINT_SLOT_2,
        wall_paint=PAINT_SLOT_2,
    )


def build_unpainted_box_fixture(tmp_path: Path) -> Path:
    """Same shape, nothing painted anywhere -- repair should refuse to guess."""
    return _embossed_fixture_archive(tmp_path, "unpainted_box.3mf", top_paint=None, wall_paint=None)


def build_engraved_fixture(tmp_path: Path) -> Path:
    """Recessed detail: plate top z=1 (unpainted), engraved floor z=0 (painted slot 3).

    Detail-plane detection must pick the painted floor, not the (unpainted,
    but otherwise identical) top surface -- this is the case a
    topmost-plane heuristic would get wrong.
    """
    vertices: list[tuple[float, float, float]] = [
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
    ]
    triangles = [
        (0, 1, 2, None),
        (0, 2, 3, None),
        (4, 5, 6, PAINT_SLOT_3),
        (4, 6, 7, PAINT_SLOT_3),
        (0, 1, 5, None),
        (0, 5, 4, None),
        (1, 2, 6, None),
        (1, 6, 5, None),
        (2, 3, 7, None),
        (2, 7, 6, None),
        (3, 0, 4, None),
        (3, 4, 7, None),
    ]
    object_model = _object_model_xml_explicit(object_id="2", vertices=vertices, triangles=triangles)
    root_model = _root_model_xml([("1", "2", "3D/Objects/object_1.model")])
    model_settings = _model_settings_xml(
        objects=["1"],
        plates=[{"plater_id": "1", "plater_name": "Plate 1", "object_ids": ["1"]}],
    )
    project_settings = _project_settings_json(["#000000", "#FF0000", "#00FF00", "#FFFF00"])

    out = tmp_path / "engraved.3mf"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", root_model)
        zf.writestr("3D/Objects/object_1.model", object_model)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/project_settings.config", project_settings)
    return out


def build_region_paint_fixture(tmp_path: Path) -> Path:
    """Three spatially distinct triangle clusters, for bounding-box selection tests.

    Cluster A (vertices 0-2, around x=0..1) sits inside a typical test
    region (x <= 2) and carries four differently-painted duplicate faces
    -- unpainted, slot-1 leaf, slot-2 leaf (the usual `target_slot` used
    in tests), and a multi-region split -- so a single selection exercises
    the newly-painted/recoloured/already-correct/skipped-multi-region
    paths in one pass. Cluster B (vertices 3-5, around x=10..11) sits well
    outside that region, to confirm out-of-scope triangles are untouched.
    Cluster C (vertices 6-8) straddles the x=2 boundary -- one vertex in,
    one out, one in -- to distinguish mode="any" from mode="all".
    """
    vertices = [
        (0, 0, 0),
        (1, 0, 0),
        (0, 1, 0),
        (10, 10, 0),
        (11, 10, 0),
        (10, 11, 0),
        (1.5, 0, 0),
        (2.5, 0, 0),
        (1.5, 1, 0),
    ]
    triangles = [
        (0, 1, 2, None),
        (0, 1, 2, PAINT_LEAF_SLOT_1),
        (0, 1, 2, PAINT_SLOT_2),
        (0, 1, 2, PAINT_SPLIT_MIXED),
        (3, 4, 5, None),
        (6, 7, 8, None),
    ]
    object_model = _object_model_xml_explicit(object_id="2", vertices=vertices, triangles=triangles)
    root_model = _root_model_xml([("1", "2", "3D/Objects/object_1.model")])
    model_settings = _model_settings_xml(
        objects=["1"],
        plates=[{"plater_id": "1", "plater_name": "Plate 1", "object_ids": ["1"]}],
    )
    project_settings = _project_settings_json(["#000000", "#FF0000", "#00FF00", "#0000FF"])

    out = tmp_path / "region_paint.3mf"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", root_model)
        zf.writestr("3D/Objects/object_1.model", object_model)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/project_settings.config", project_settings)
    return out


def build_diff_variant_fixture(tmp_path: Path, suffix: str, name: str, extruder: str) -> Path:
    """Single object like build_single_object_fixture, with a parameterized
    name/base-extruder -- for diff_3mf's objects_changed detection, which
    needs two otherwise-identical archives differing in exactly those
    fields.
    """
    object_model = _object_model_xml(object_id="2", paint_colors=[PAINT_LEAF_SLOT_4])
    root_model = _root_model_xml([("1", "2", "3D/Objects/object_1.model")])
    model_settings = _model_settings_xml(
        objects=["1"],
        plates=[{"plater_id": "1", "plater_name": "Plate 1", "object_ids": ["1"]}],
        object_names={"1": name},
        extruders={"1": extruder},
    )
    project_settings = _project_settings_json(["#000000", "#FF0000", "#00FF00", "#0000FF"])

    out = tmp_path / f"diff_variant_{suffix}.3mf"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", root_model)
        zf.writestr("3D/Objects/object_1.model", object_model)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/project_settings.config", project_settings)
    return out


def build_multi_plate_fixture(tmp_path: Path) -> Path:
    """Two plates: plate 1 has two objects, plate 2 has one."""
    object_1 = _object_model_xml(object_id="11", paint_colors=[PAINT_LEAF_SLOT_4])
    object_2 = _object_model_xml(object_id="12", paint_colors=[PAINT_LEAF_SLOT_1])
    object_3 = _object_model_xml(object_id="13", paint_colors=[])

    root_model = _root_model_xml(
        [
            ("1", "11", "3D/Objects/object_1.model"),
            ("2", "12", "3D/Objects/object_2.model"),
            ("3", "13", "3D/Objects/object_3.model"),
        ]
    )
    model_settings = _model_settings_xml(
        objects=["1", "2", "3"],
        plates=[
            {"plater_id": "1", "plater_name": "Plate 1", "object_ids": ["1", "2"]},
            {"plater_id": "2", "plater_name": "Plate 2", "object_ids": ["3"]},
        ],
    )
    project_settings = _project_settings_json(["#000000", "#FF0000", "#00FF00"])

    out = tmp_path / "multi_plate.3mf"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", root_model)
        zf.writestr("3D/Objects/object_1.model", object_1)
        zf.writestr("3D/Objects/object_2.model", object_2)
        zf.writestr("3D/Objects/object_3.model", object_3)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/project_settings.config", project_settings)
        zf.writestr("Metadata/plate_1.png", b"fake-png-1")
        zf.writestr("Metadata/plate_2.png", b"fake-png-2")
    return out
