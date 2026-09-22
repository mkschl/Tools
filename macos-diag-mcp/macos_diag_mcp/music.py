"""Music's `A.B.C` artwork identifiers, decoded.

From the artwork retry-loop case: the decimal parts of the identifier in
`artwork fetch failed for A.B.C` are Music persistent IDs, which AppleScript
and the Music UI only ever show as 16-digit uppercase hex.
"""

from __future__ import annotations

# The identifier's parts in the order Music logs them.
PART_ROLES = ("database", "track", "playlist")

APPLESCRIPT_TEMPLATE = (
    'tell application "Music" to get name of '
    '(first {kind} whose persistent ID is "{persistent_id}")'
)


def persistent_ids(identifier: str) -> list[dict[str, str]]:
    """Convert `A.B.C` to persistent IDs, with an AppleScript lookup for each.

    Roles are a best guess from position — verify by running the lookups rather
    than trusting the label.
    """
    parts = [p for p in identifier.strip().split(".") if p != ""]
    if not parts:
        raise ValueError(f"{identifier!r} is not an A.B.C identifier")

    out = []
    for index, part in enumerate(parts):
        if not part.isdigit():
            raise ValueError(
                f"part {index + 1} of {identifier!r} is not a decimal number"
            )
        role = PART_ROLES[index] if index < len(PART_ROLES) else f"part{index + 1}"
        hex_id = format(int(part), "016X")
        kind = "playlist" if role == "playlist" else "track"
        out.append(
            {
                "role": role,
                "decimal": part,
                "persistent_id": hex_id,
                "applescript": APPLESCRIPT_TEMPLATE.format(
                    kind=kind, persistent_id=hex_id
                ),
            }
        )
    return out
