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
        f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
        for oid, _, _ in objects
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{CORE_NS}" xmlns:p="{PROD_NS}" unit="millimeter">
  <resources>{resource_objs}</resources>
  <build>{items}</build>
</model>"""
    return xml.encode("utf-8")


def _model_settings_xml(objects: list[str], plates: list[dict]) -> bytes:
    object_blocks = "".join(
        f'<object id="{oid}"><metadata key="extruder" value="1"/></object>'
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
    project_settings = _project_settings_json(
        ["#000000", "#FF0000", "#00FF00", "#0000FF"]
    )

    out = tmp_path / "single_object.3mf"
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
