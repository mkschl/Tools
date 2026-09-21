"""Per-photo calibration storage.

Every photo has its own scale — two shots from slightly different heights will
not share one. Calibrations are therefore keyed by the photo's content hash, so
a scale can only ever be looked up for the exact image it was measured on, and
a moved or renamed photo keeps it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Calibration:
    px_per_mm: float
    method: str  # "grid" or "reference"
    photo_sha256: str
    photo_path: str
    # Present only when a caliper-measured feature confirmed the scale.
    check_measured_mm: float | None = None
    check_actual_mm: float | None = None
    check_deviation_pct: float | None = None

    @property
    def cross_checked(self) -> bool:
        return self.check_actual_mm is not None


def photo_sha256(photo: Path) -> str:
    digest = hashlib.sha256()
    with photo.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CalibrationStore:
    """One JSON file per photo, named by the photo's content hash."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _file(self, sha: str) -> Path:
        return self.directory / f"{sha}.json"

    def save(self, calibration: Calibration) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._file(calibration.photo_sha256)
        scratch = target.with_suffix(".tmp")
        scratch.write_text(json.dumps(asdict(calibration), indent=2))
        scratch.replace(target)

    def load(self, photo: Path) -> Calibration | None:
        target = self._file(photo_sha256(photo))
        if not target.is_file():
            return None
        return Calibration(**json.loads(target.read_text()))
