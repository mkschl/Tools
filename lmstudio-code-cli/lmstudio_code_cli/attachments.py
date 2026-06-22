"""Parse @path references from user messages and encode them for the LLM."""

import base64
import re
from dataclasses import dataclass
from pathlib import Path

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# Match @path or @"path with spaces"
_REF_RE = re.compile(r'@"([^"]+)"|@(\S+)')


@dataclass
class Attachment:
    path: Path
    mime_type: str
    data: bytes

    @property
    def size_kb(self) -> float:
        return len(self.data) / 1024

    def to_openai(self) -> dict:
        b64 = base64.standard_b64encode(self.data).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{self.mime_type};base64,{b64}"},
        }


def parse(text: str, cwd: str) -> tuple[str, list[Attachment], list[str]]:
    """Extract @path refs from text, load the files, return cleaned text + attachments."""
    attachments: list[Attachment] = []
    errors: list[str] = []

    def _replace(m: re.Match) -> str:
        raw = m.group(1) or m.group(2)
        p = Path(raw) if Path(raw).is_absolute() else Path(cwd) / raw

        if not p.exists():
            errors.append(f"@{raw}: file not found")
            return m.group(0)

        suffix = p.suffix.lower()
        if suffix not in _MIME:
            errors.append(f"@{raw}: unsupported type '{suffix}' (supported: {', '.join(_MIME)})")
            return m.group(0)

        attachments.append(Attachment(path=p, mime_type=_MIME[suffix], data=p.read_bytes()))
        return ""

    clean = _REF_RE.sub(_replace, text).strip()
    return clean, attachments, errors
