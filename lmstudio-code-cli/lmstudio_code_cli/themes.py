import tomllib
from pathlib import Path

from .defaults import DEFAULTS

DEFAULT_THEME: str = DEFAULTS["theme"]

_themes_dir = Path(__file__).parent / "themes"

THEMES: dict[str, dict[str, str]] = {}
for _p in sorted(_themes_dir.glob("*.toml")):
    with open(_p, "rb") as _f:
        THEMES[_p.stem] = tomllib.load(_f)
