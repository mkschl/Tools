"""Shared helper: patch `<triangle paint_color=...>` attributes via targeted text splices.

Per CLAUDE.md's "Safe rewriting rules", a `.model` file is never
re-serialised through `ElementTree` -- that rewrites namespace prefixes,
attribute order, and drops `p:UUID` formatting the slicer depends on.
Callers parse structure read-only via `ElementTree`, decide what to
change, then apply those decisions here as plain text edits at the
matched `<triangle>` tag spans, leaving the rest of the document
byte-identical. Used by both `recolor.py` and `repair_emboss.py`.
"""

from __future__ import annotations

import re

_TRIANGLE_RE = re.compile(r"<triangle\b[^>]*/>")
_PAINT_COLOR_ATTR_RE = re.compile(r'paint_color="[0-9a-fA-F]*"')


class TrianglePatchError(RuntimeError):
    pass


def apply_triangle_decisions(text: str, decisions: list[str | None]) -> tuple[str, int]:
    """Set/replace `paint_color` on `<triangle>` tags in document order.

    `decisions` must have one entry per `<triangle>` tag found in `text`,
    in the same (document) order: `None` leaves that tag untouched, a hex
    string sets or replaces its `paint_color` attribute. Returns the
    patched text and the number of tags actually changed.
    """
    matches = list(_TRIANGLE_RE.finditer(text))
    if len(matches) != len(decisions):
        raise TrianglePatchError(
            f"triangle count mismatch while patching: {len(matches)} tags vs "
            f"{len(decisions)} decisions"
        )

    pieces: list[str] = []
    last_end = 0
    changed = 0
    for match, decision in zip(matches, decisions):
        pieces.append(text[last_end : match.start()])
        tag_text = match.group(0)
        if decision is not None:
            if 'paint_color="' in tag_text:
                new_tag = _PAINT_COLOR_ATTR_RE.sub(
                    f'paint_color="{decision}"', tag_text
                )
            else:
                new_tag = tag_text[:-2].rstrip() + f' paint_color="{decision}"/>'
            pieces.append(new_tag)
            changed += 1
        else:
            pieces.append(tag_text)
        last_end = match.end()
    pieces.append(text[last_end:])
    return "".join(pieces), changed
