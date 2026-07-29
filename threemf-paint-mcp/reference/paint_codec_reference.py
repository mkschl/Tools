"""Codec for Bambu Studio / OrcaSlicer per-triangle MMU paint data.

Ports the bit layout of PrusaSlicer's ``TriangleSelector::serialize()`` /
``deserialize()`` (``src/libslic3r/TriangleSelector.cpp``), which Bambu
Studio forked verbatim for its multi-color paint feature. That format is
not documented anywhere official (see prusa3d/PrusaSlicer#13900).

The core bit-level functions below (``decode_bits``, ``parse``,
``write_nibble``, ``_encode_bits``, ``bits_to_string``) were validated in
an earlier session against every distinct `paint_color` value pulled from
a real multi-plate Bambu project -- see
``tests/fixtures/sample_paint_color_values.txt`` and
``tests/test_paint_codec.py::test_sample_paint_color_values_roundtrip``,
which re-runs that check as an actual test. Do not change their bit
arithmetic without re-validating against that fixture.

Format, in generation order (this is nibble-oriented, not a raw
continuous bitstream -- every field before the final variable-length
tail occupies a whole 4-bit nibble):

- Nibble 1: low 2 bits = number of split sides (0 = leaf, 1-3 = internal
  node with 2-4 children). High 2 bits double as either the leaf state
  (states 0-2) or a node's `special_side`, depending on the low bits.
- Leaf, state 0-2: encoded entirely in nibble 1's high 2 bits.
- Leaf, state 3-16: nibble 1's high bits read as `0b11` (the marker),
  followed by a second nibble holding `state - 3` directly (and that
  nibble must not equal `0b1110`, which is reserved as the escape below).
- Leaf, state 17+: nibble 1's high bits `0b11`, second nibble `0b1110`
  (escape), then two more nibbles holding an 8-bit `state - 17` value
  split low-nibble-first.
- Split node: nibble 1's low bits give the split-side count, high bits
  give `special_side`; each child follows recursively, itself starting
  on a nibble boundary.

The hex string stored in the `paint_color` XML attribute is the
bitstream chunked into 4-bit nibbles (in generation order), each
converted to a hex digit LSB-first, with the resulting hex-digit string
then reversed. Decoding starts by walking the string in reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Validated core: bit-level parse/encode over plain tuples -------------
#
# Tree shapes:
#   ('leaf', state)
#   ('split', special_side, num_split, children)   # num_split = len(children) - 1


def decode_bits(s):
    bits = []
    for c in reversed(s):
        v = int(c, 16)
        for i in range(4):
            bits.append((v >> i) & 1)
    return bits


def parse(bits):
    pos = [0]

    def read_nibble():
        n = 0
        for i in range(4):
            n |= bits[pos[0]] << i
            pos[0] += 1
        return n

    def parse_node():
        code = read_nibble()
        num_split = code & 0b11
        if num_split != 0:
            special_side = code >> 2
            children = [parse_node() for _ in range(num_split + 1)]
            return ("split", special_side, num_split, children)
        else:
            if (code & 0b1100) != 0b1100:
                state = code >> 2
                return ("leaf", state)
            else:
                second = read_nibble()
                if second != 0b1110:
                    state = second + 3
                    return ("leaf", state)
                else:
                    lo = read_nibble()
                    hi = read_nibble()
                    state = (lo | (hi << 4)) + 17
                    return ("leaf", state)

    root = parse_node()
    assert pos[0] == len(bits), f"leftover bits: {pos[0]} vs {len(bits)}"
    return root


def write_nibble(out, val):
    for i in range(4):
        out.append((val >> i) & 1)


def _encode_bits(node, out):
    if node[0] == "split":
        _, special_side, num_split, children = node
        nib = (num_split & 0b11) | ((special_side & 0b11) << 2)
        write_nibble(out, nib)
        for child in children:
            _encode_bits(child, out)
    else:
        _, state = node
        if state < 3:
            nib = 0 | ((state & 0b11) << 2)
            write_nibble(out, nib)
        elif state <= 16:
            write_nibble(out, 0b1100)
            write_nibble(out, state - 3)
        else:
            write_nibble(out, 0b1100)
            write_nibble(out, 0b1110)
            encoded = state - 17
            write_nibble(out, encoded & 0xF)
            write_nibble(out, (encoded >> 4) & 0xF)


def bits_to_string(bits):
    assert len(bits) % 4 == 0
    digits = []
    for i in range(0, len(bits), 4):
        val = bits[i] | (bits[i + 1] << 1) | (bits[i + 2] << 2) | (bits[i + 3] << 3)
        digits.append(val < 10 and chr(ord("0") + val) or chr(ord("A") + val - 10))
    return "".join(reversed(digits))


def remap_tree(node, mapping):
    if node[0] == "leaf":
        _, state = node
        new_state = mapping.get(state, state)
        return ("leaf", new_state)
    else:
        _, special_side, num_split, children = node
        return ("split", special_side, num_split, [remap_tree(c, mapping) for c in children])


def transform_string(s, mapping):
    bits = decode_bits(s)
    tree = parse(bits)
    check_bits = []
    _encode_bits(tree, check_bits)
    assert check_bits == bits, f"roundtrip mismatch for {s}"
    new_tree = remap_tree(tree, mapping)
    new_bits = []
    _encode_bits(new_tree, new_bits)
    return bits_to_string(new_bits)


# --- Public API used by the rest of this server: a typed tree ------------


@dataclass(frozen=True)
class Leaf:
    state: int


@dataclass(frozen=True)
class Split:
    special_side: int
    children: tuple = field(default_factory=tuple)


Node = Leaf | Split


def _to_tuple(node: Node):
    if isinstance(node, Leaf):
        return ("leaf", node.state)
    if isinstance(node, Split):
        children = tuple(_to_tuple(c) for c in node.children)
        return ("split", node.special_side, len(children) - 1, list(children))
    raise TypeError(f"unknown node type: {type(node)!r}")


def _from_tuple(t) -> Node:
    if t[0] == "leaf":
        return Leaf(state=t[1])
    _, special_side, _num_split, children = t
    return Split(special_side=special_side, children=tuple(_from_tuple(c) for c in children))


def decode(hex_string: str) -> Node:
    """Decode a `paint_color` attribute value into a Leaf/Split tree."""
    if not hex_string:
        return Leaf(state=0)
    bits = decode_bits(hex_string)
    tree = parse(bits)
    return _from_tuple(tree)


def _validate(node: Node) -> None:
    if isinstance(node, Leaf):
        if not 0 <= node.state <= 255:
            raise ValueError(f"leaf state {node.state} out of range (0-255)")
        return
    if isinstance(node, Split):
        if not 2 <= len(node.children) <= 4:
            raise ValueError(f"split node must have 2-4 children, got {len(node.children)}")
        for child in node.children:
            _validate(child)
        return
    raise TypeError(f"unknown node type: {type(node)!r}")


def encode(node: Node) -> str:
    """Encode a Leaf/Split tree back into a `paint_color` attribute value."""
    _validate(node)
    out: list[int] = []
    _encode_bits(_to_tuple(node), out)
    return bits_to_string(out)


def collect_leaf_states(node: Node) -> list[int]:
    """Return the state of every leaf in the tree, in traversal order.

    Each entry represents one leaf *region*, not one triangle -- a single
    triangle can carry several leaves if it has been subdivided.
    """
    if isinstance(node, Leaf):
        return [node.state]
    states: list[int] = []
    for child in node.children:
        states.extend(collect_leaf_states(child))
    return states


def remap_leaf_states(node: Node, mapping: dict[int, int]) -> Node:
    """Return a new tree with leaf states remapped, preserving split structure.

    States not present in `mapping` are left unchanged. `Split` nodes and
    their `special_side` values are never touched.
    """
    if isinstance(node, Leaf):
        return Leaf(state=mapping.get(node.state, node.state))
    return Split(
        special_side=node.special_side,
        children=tuple(remap_leaf_states(child, mapping) for child in node.children),
    )


def structure_matches(a: Node, b: Node) -> bool:
    """True if `a` and `b` have identical split structure (ignoring leaf states)."""
    if isinstance(a, Leaf) and isinstance(b, Leaf):
        return True
    if isinstance(a, Split) and isinstance(b, Split):
        return (
            a.special_side == b.special_side
            and len(a.children) == len(b.children)
            and all(structure_matches(ca, cb) for ca, cb in zip(a.children, b.children))
        )
    return False


def verify_roundtrip(hex_string: str) -> bool:
    """True if decode -> encode reproduces the original string byte-exact."""
    return encode(decode(hex_string)) == hex_string


if __name__ == "__main__":
    # "AA" is deliberately not included here: on its own it decodes as a
    # split node that needs 3 children but only has 8 bits total, so it
    # isn't a valid standalone code -- it doesn't belong in this list.
    tests = ["4", "8", "0C", "1C", "9C"]
    for t in tests:
        r = transform_string(t, {})  # identity
        assert r == t, f"identity roundtrip failed: {t} -> {r}"
    print("Self-test passed: encode/decode round-trip is exact.")
