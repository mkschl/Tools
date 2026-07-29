"""`list_plates` and `extract_plate` logic.

`extract_plate` follows the step-by-step plan in CLAUDE.md's "Plate
extraction" section. That plan was written against a single-plate sample
and explicitly flagged as needing validation against a real multi-plate
file -- this implementation surfaces the same caveats in its `warnings`
return value rather than silently assuming they don't apply.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from threemf_paint_mcp import archive, threemf_model

_PLATE_IMAGE_PREFIXES = (
    "plate_",
    "plate_no_light_",
    "top_",
    "pick_",
)


class PlateNotFoundError(RuntimeError):
    pass


def list_plates(path: str | Path) -> list[dict]:
    with zipfile.ZipFile(path) as zf:
        plates = threemf_model.get_plates(zf)
    return [
        {
            "plater_id": plate.plater_id,
            "plater_name": plate.plater_name,
            "object_ids": plate.object_ids,
            "thumbnails": plate.thumbnails,
        }
        for plate in plates
    ]


def _register_original_namespaces(xml_bytes: bytes) -> None:
    # For "start-ns" events, ElementTree yields (prefix, uri) str pairs as the
    # second tuple element -- typeshed's iterparse stub types it as Element
    # regardless of event kind, so the real runtime shape needs an explicit cast.
    events = ET.iterparse(io.BytesIO(xml_bytes), events=("start-ns",))
    for _, payload in cast("Iterator[tuple[str, tuple[str, str]]]", events):
        prefix, uri = payload
        ET.register_namespace(prefix or "", uri)


def _serialize(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _filter_root_model(xml_bytes: bytes, keep_object_ids: set[str]) -> tuple[bytes, set[str]]:
    _register_original_namespaces(xml_bytes)
    root = ET.fromstring(xml_bytes)

    kept_objects = []
    for resources in threemf_model.iter_local(root, "resources"):
        for obj in list(resources):
            if threemf_model.local_name(obj.tag) != "object":
                continue
            if threemf_model.find_attr(obj, "id") in keep_object_ids:
                kept_objects.append(obj)
            else:
                resources.remove(obj)

    for build in threemf_model.iter_local(root, "build"):
        for item in list(build):
            if threemf_model.local_name(item.tag) != "item":
                continue
            if threemf_model.find_attr(item, "objectid") not in keep_object_ids:
                build.remove(item)

    referenced_paths: set[str] = set()
    for obj in kept_objects:
        for component in threemf_model.iter_local(obj, "component"):
            path = threemf_model.find_attr(component, "path")
            if path:
                referenced_paths.add(path.lstrip("/"))

    return _serialize(root), referenced_paths


def _filter_model_settings(
    xml_bytes: bytes, keep_object_ids: set[str], target_plater_id: str
) -> bytes:
    _register_original_namespaces(xml_bytes)
    root = ET.fromstring(xml_bytes)

    for parent in root.iter():
        for child in list(parent):
            name = threemf_model.local_name(child.tag)
            if (
                name == "object"
                and threemf_model.find_attr(child, "id") not in keep_object_ids
                or name == "plate"
                and not any(
                    threemf_model.find_attr(meta, "key") == "plater_id"
                    and threemf_model.find_attr(meta, "value") == target_plater_id
                    for meta in threemf_model.iter_local(child, "metadata")
                )
                or name == "assemble_item"
                and threemf_model.find_attr(child, "object_id") not in keep_object_ids
            ):
                parent.remove(child)

    return _serialize(root)


def extract_plate(path: str | Path, plater_id: str, output_path: str | Path) -> dict:
    path = Path(path)
    output_path = Path(output_path)
    plater_id = str(plater_id)
    warnings: list[str] = []

    with zipfile.ZipFile(path) as zf:
        all_names = zf.namelist()
        plates = threemf_model.get_plates(zf)
        target = next((p for p in plates if p.plater_id == plater_id), None)
        if target is None:
            raise PlateNotFoundError(f"no plate with plater_id={plater_id!r} in {path}")
        keep_object_ids = set(target.object_ids)

        entries = {name: zf.read(name) for name in all_names}

    new_entries: dict[str, bytes] = dict(entries)

    if threemf_model.ROOT_MODEL in entries:
        filtered_root, referenced_paths = _filter_root_model(
            entries[threemf_model.ROOT_MODEL], keep_object_ids
        )
        new_entries[threemf_model.ROOT_MODEL] = filtered_root
        for name in list(new_entries):
            if name.startswith("3D/Objects/") and name not in referenced_paths:
                del new_entries[name]
    else:
        warnings.append(f"{threemf_model.ROOT_MODEL} missing; skipped root-model filtering")

    if threemf_model.MODEL_SETTINGS in entries:
        new_entries[threemf_model.MODEL_SETTINGS] = _filter_model_settings(
            entries[threemf_model.MODEL_SETTINGS], keep_object_ids, plater_id
        )
    else:
        warnings.append(f"{threemf_model.MODEL_SETTINGS} missing; skipped plate/object filtering")

    for name in list(new_entries):
        base = name.rsplit("/", 1)[-1]
        if not name.startswith("Metadata/"):
            continue
        if not base.startswith(_PLATE_IMAGE_PREFIXES):
            continue
        if f"_{plater_id}." not in base and not base.endswith(f"_{plater_id}"):
            del new_entries[name]

    for config_name in (
        "Metadata/project_settings.config",
        "Metadata/filament_sequence.json",
    ):
        if config_name in entries:
            warnings.append(
                f"{config_name} copied as-is (assumed project-wide, not per-plate -- "
                "unverified against a real multi-plate/per-plate-AMS sample)"
            )

    if "Metadata/slice_info.config" in entries:
        warnings.append(
            "Metadata/slice_info.config copied as-is without per-plate filtering -- "
            "unverified whether it holds multiple plates' worth of data"
        )

    warnings.append(
        "thumbnail/plate image files were not renumbered to plate_1* and "
        "model_settings.config thumbnail paths were not rewritten -- downstream "
        "tooling that assumes plate 1 may not find them"
    )
    warnings.append(
        "[Content_Types].xml and .rels files were copied unchanged -- dangling "
        "relationship references to dropped files were not pruned"
    )

    archive.rebuild_from_entries(output_path, new_entries)
    validation = archive.validate_archive(output_path)
    if not validation["ok"]:
        raise RuntimeError(f"extracted plate archive failed validation: {validation}")

    return {
        "output_path": str(output_path),
        "plater_id": plater_id,
        "object_ids": sorted(keep_object_ids, key=lambda x: int(x) if x.isdigit() else x),
        "validation": validation,
        "warnings": warnings,
    }
