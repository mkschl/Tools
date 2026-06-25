import tomllib
from pathlib import Path

with open(Path(__file__).parent / "defaults.toml", "rb") as _f:
    DEFAULTS: dict = tomllib.load(_f)
