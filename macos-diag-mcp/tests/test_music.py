import pytest

from macos_diag_mcp.music import persistent_ids


def test_converts_each_part_to_sixteen_digit_uppercase_hex():
    parts = persistent_ids("61.99.123")
    assert [p["persistent_id"] for p in parts] == [
        "000000000000003D",
        "0000000000000063",
        "000000000000007B",
    ]


def test_labels_the_parts_by_position():
    assert [p["role"] for p in persistent_ids("1.2.3")] == [
        "database",
        "track",
        "playlist",
    ]


def test_builds_a_playlist_lookup_for_the_third_part():
    playlist = persistent_ids("1.2.3")[2]
    assert "playlist whose persistent ID" in playlist["applescript"]
    assert playlist["persistent_id"] in playlist["applescript"]


def test_handles_a_real_sized_identifier():
    part = persistent_ids("6979286789042993455")[0]
    assert part["persistent_id"] == format(6979286789042993455, "016X")
    assert len(part["persistent_id"]) == 16


def test_handles_more_parts_than_known_roles():
    assert persistent_ids("1.2.3.4")[3]["role"] == "part4"


@pytest.mark.parametrize("bad", ["", "   ", "...", "a.b.c", "61.x.123"])
def test_rejects_something_that_is_not_an_identifier(bad):
    with pytest.raises(ValueError):
        persistent_ids(bad)
