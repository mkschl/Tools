from pathlib import Path

import pytest

from reference.paint_codec_reference import (
    Leaf,
    Split,
    collect_leaf_states,
    decode,
    encode,
    remap_leaf_states,
    structure_matches,
    verify_roundtrip,
)

SAMPLE_VALUES_PATH = (
    Path(__file__).parent / "fixtures" / "sample_paint_color_values.txt"
)
SAMPLE_VALUES = [
    line.strip() for line in SAMPLE_VALUES_PATH.read_text().splitlines() if line.strip()
]


@pytest.mark.parametrize("state", range(256))
def test_every_leaf_state_roundtrips(state):
    hex_string = encode(Leaf(state=state))
    assert decode(hex_string) == Leaf(state=state)
    assert verify_roundtrip(hex_string)


def test_split_node_roundtrips():
    tree = Split(
        special_side=2,
        children=(
            Leaf(state=0),
            Leaf(state=5),
            Split(special_side=1, children=(Leaf(state=200), Leaf(state=1))),
            Leaf(state=255),
        ),
    )
    hex_string = encode(tree)
    assert decode(hex_string) == tree
    assert verify_roundtrip(hex_string)


def test_collect_leaf_states_in_traversal_order():
    tree = Split(
        special_side=0,
        children=(Leaf(state=4), Leaf(state=1), Leaf(state=0)),
    )
    assert collect_leaf_states(tree) == [4, 1, 0]


def test_remap_preserves_structure_and_touches_only_mapped_states():
    tree = Split(
        special_side=0,
        children=(Leaf(state=4), Leaf(state=1), Leaf(state=7)),
    )
    remapped = remap_leaf_states(tree, {4: 2})
    assert structure_matches(tree, remapped)
    assert collect_leaf_states(remapped) == [2, 1, 7]


def test_remap_leaves_unmapped_states_untouched():
    tree = Leaf(state=9)
    remapped = remap_leaf_states(tree, {4: 2})
    assert remapped == tree


def test_structure_mismatch_detected_for_different_shapes():
    a = Leaf(state=0)
    b = Split(special_side=0, children=(Leaf(state=0), Leaf(state=1)))
    assert not structure_matches(a, b)


def test_encode_rejects_out_of_range_leaf_state():
    with pytest.raises(ValueError):
        encode(Leaf(state=256))


def test_encode_rejects_invalid_split_child_count():
    with pytest.raises(ValueError):
        encode(Split(special_side=0, children=(Leaf(state=0),)))


def test_decode_empty_string_is_none_leaf():
    assert decode("") == Leaf(state=0)


@pytest.mark.parametrize("hex_string", SAMPLE_VALUES)
def test_sample_paint_color_values_roundtrip(hex_string):
    """Every distinct paint_color value pulled from a real Bambu project.

    This is the single most important correctness guarantee in this
    codebase (CLAUDE.md): a codec bug is silent data corruption, not a
    crash, since the XML still parses and the zip still opens.
    """
    assert verify_roundtrip(hex_string)


def test_sample_paint_color_values_identity_remap_preserves_structure():
    for hex_string in SAMPLE_VALUES:
        tree = decode(hex_string)
        remapped = remap_leaf_states(tree, {})
        assert structure_matches(tree, remapped)
        assert encode(remapped) == hex_string
