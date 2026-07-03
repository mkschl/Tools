import tomllib
from pathlib import Path
from typing import Any

with open(Path(__file__).parent / "defaults.toml", "rb") as _f:
    DEFAULTS: dict[str, Any] = tomllib.load(_f)
