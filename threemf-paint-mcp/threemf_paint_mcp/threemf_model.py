"""Namespace-agnostic parsing of the Bambu/Orca .3mf XML and config layout.

3MF root documents use a `p:` production-extension namespace prefix whose
exact URI varies by slicer/version, so all lookups here match on local
tag/attribute name (the part after `}`) instead of a fully-qualified name.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

ROOT_MODEL = "3D/3dmodel.model"
MODEL_SETTINGS = "Metadata/model_settings.config"
PROJECT_SETTINGS = "Metadata/project_settings.config"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_attr(elem: ET.Element, name: str) -> str | None:
    for key, value in elem.attrib.items():
        if local_name(key) == name:
            return value
    return None


def iter_local(root: ET.Element, name: str):
    for elem in root.iter():
        if local_name(elem.tag) == name:
            yield elem


def read_xml(zf: zipfile.ZipFile, entry: str) -> ET.Element:
    return ET.fromstring(zf.read(entry))


def read_json(zf: zipfile.ZipFile, entry: str) -> dict:
    return json.loads(zf.read(entry))


def get_filament_colours(zf: zipfile.ZipFile) -> dict[int, str]:
    """Return {slot_number (1-indexed): hex_colour} from project_settings.config."""
    if PROJECT_SETTINGS not in zf.namelist():
        return {}
    config = read_json(zf, PROJECT_SETTINGS)
    colours = config.get("filament_colour", [])
    return {index + 1: colour for index, colour in enumerate(colours)}


def get_object_base_extruders(zf: zipfile.ZipFile) -> dict[str, int]:
    """Return {object_id: base_extruder_slot} from model_settings.config."""
    if MODEL_SETTINGS not in zf.namelist():
        return {}
    root = read_xml(zf, MODEL_SETTINGS)
    result: dict[str, int] = {}
    for obj in iter_local(root, "object"):
        object_id = find_attr(obj, "id")
        if object_id is None:
            continue
        for meta in iter_local(obj, "metadata"):
            if find_attr(meta, "key") == "extruder":
                value = find_attr(meta, "value")
                if value is not None:
                    result[object_id] = int(value)
    return result


def find_referenced_object_paths(zf: zipfile.ZipFile) -> set[str]:
    """Every `3D/Objects/*.model` file referenced via <component p:path=...>."""
    if ROOT_MODEL not in zf.namelist():
        return set()
    root = read_xml(zf, ROOT_MODEL)
    paths = set()
    for component in iter_local(root, "component"):
        path = find_attr(component, "path")
        if path:
            paths.add(path.lstrip("/"))
    return paths


def all_model_paths(zf: zipfile.ZipFile) -> list[str]:
    """The root model plus every object file it references."""
    paths = [ROOT_MODEL] if ROOT_MODEL in zf.namelist() else []
    paths.extend(sorted(find_referenced_object_paths(zf)))
    return [p for p in paths if p in zf.namelist()]


@dataclass
class PaintedTriangle:
    model_path: str
    object_id: str | None
    triangle_index: int
    paint_color: str


def iter_painted_triangles(zf: zipfile.ZipFile, model_path: str):
    """Yield PaintedTriangle for every <triangle paint_color="..."> in a model file."""
    root = read_xml(zf, model_path)
    for obj in iter_local(root, "object"):
        object_id = find_attr(obj, "id")
        for index, triangle in enumerate(iter_local(obj, "triangle")):
            paint_color = find_attr(triangle, "paint_color")
            if paint_color:
                yield PaintedTriangle(model_path, object_id, index, paint_color)


@dataclass
class PlateInfo:
    plater_id: str
    plater_name: str | None
    object_ids: list[str] = field(default_factory=list)
    thumbnails: dict[str, str] = field(default_factory=dict)


_THUMBNAIL_KEYS = (
    "thumbnail_file",
    "thumbnail_no_light_file",
    "top_file",
    "pick_file",
)


def get_plates(zf: zipfile.ZipFile) -> list[PlateInfo]:
    if MODEL_SETTINGS not in zf.namelist():
        return []
    root = read_xml(zf, MODEL_SETTINGS)
    plates = []
    for plate in iter_local(root, "plate"):
        plater_id = None
        plater_name = None
        thumbnails: dict[str, str] = {}
        object_ids: list[str] = []
        for meta in iter_local(plate, "metadata"):
            key = find_attr(meta, "key")
            value = find_attr(meta, "value")
            if key == "plater_id":
                plater_id = value
            elif key == "plater_name":
                plater_name = value
            elif key in _THUMBNAIL_KEYS and value:
                thumbnails[key] = value
        for instance in iter_local(plate, "model_instance"):
            for meta in iter_local(instance, "metadata"):
                if find_attr(meta, "key") == "object_id":
                    value = find_attr(meta, "value")
                    if value is not None:
                        object_ids.append(value)
        if plater_id is None:
            continue
        plates.append(
            PlateInfo(
                plater_id=plater_id,
                plater_name=plater_name,
                object_ids=object_ids,
                thumbnails=thumbnails,
            )
        )
    return plates
