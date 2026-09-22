"""Redaction has to mask identity without destroying the diagnosis or the paths."""

from pathlib import Path

import pytest

from macos_diag_mcp.config import load_settings
from macos_diag_mcp.redact import Redactor, build

SETTINGS = load_settings().redact


@pytest.fixture
def redactor() -> Redactor:
    """A redactor for a fixed fake machine, so tests do not depend on this one."""
    return build(SETTINGS, home=Path("/Users/testuser"), hostname="Test-Studio.local")


# ── what must be masked ───────────────────────────────────────────────────────


def test_the_users_home_becomes_a_tilde(redactor):
    assert redactor.text("/Users/testuser/Documents/x") == "~/Documents/x"


def test_another_users_home_is_masked_wholesale(redactor):
    assert redactor.text("/Users/someoneelse/Documents") == "/Users/<user>/Documents"


def test_the_bare_username_is_masked(redactor):
    assert redactor.text("session owned by testuser") == "session owned by <user>"


def test_a_quoted_ssid_is_masked_but_the_keyword_survives(redactor):
    assert redactor.text('joined SSID "Home-WLAN"') == 'joined SSID "<ssid>"'


def test_an_unquoted_ssid_is_masked(redactor):
    assert redactor.text("ssid=Home-WLAN now") == "ssid=<ssid> now"


def test_ipv4_is_masked(redactor):
    assert redactor.text("from 192.168.1.42 ok") == "from <ip> ok"


def test_ipv6_is_masked(redactor):
    assert redactor.text("peer fe80:0:0:0:1:2:3:4 up") == "peer <ip> up"


def test_a_mac_address_is_masked_and_not_read_as_ipv6(redactor):
    assert redactor.text("en0 3c:22:fb:aa:bb:cc") == "en0 <mac>"


def test_email_is_masked(redactor):
    assert redactor.text("account a.b@example.com") == "account <email>"


def test_this_machines_hostname_is_masked_with_or_without_the_suffix(redactor):
    assert redactor.text("host Test-Studio.local") == "host <host>"
    assert redactor.text("host Test-Studio here") == "host <host> here"


def test_any_other_local_hostname_is_masked(redactor):
    assert redactor.text("peer Someones-MBP.local") == "peer <host>.local"


def test_the_whole_leaky_line_from_the_audit(redactor):
    line = (
        'user testuser at /Users/testuser/Documents joined SSID "Home-WLAN" '
        "from 192.168.1.42 host Test-Studio.local"
    )
    assert redactor.text(line) == (
        'user <user> at ~/Documents joined SSID "<ssid>" from <ip> host <host>'
    )


# ── what must survive, because it is the diagnosis ────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "E  WindowServer [com.apple.SkyLight:default] _CGXPackagesSetWindowConstraints: Invalid window",
        "coreautha SACAssertScreenLockViaTouchIDBlocked renewed",
        "/System/Library/PrivateFrameworks/UniversalAccess.framework/Versions/A/Resources/x",
        "artwork fetch failed for 61.99.123 Code=9069",
        "libsystem_kernel.dylib  __pthread_kill + 8",
        "Music[812:1a2b] EXC_CRASH SIGABRT",
    ],
)
def test_diagnostic_content_is_left_alone(redactor, line):
    assert redactor.text(line) == line


def test_a_timestamp_is_not_mistaken_for_an_ipv6_address(redactor):
    assert redactor.text("2026-09-22 07:45:01.123 E") == "2026-09-22 07:45:01.123 E"


def test_a_short_username_is_not_masked_as_a_bare_word():
    """'dev' as a username would otherwise eat the word everywhere it appears."""
    short = build(SETTINGS, home=Path("/Users/dev"), hostname="h")
    assert short.text("the dev build") == "the dev build"
    assert short.text("/Users/dev/x") == "~/x"  # the home path still goes


# ── round trip: a masked path must still be usable ────────────────────────────


def test_a_masked_home_path_still_resolves_for_the_next_tool_call():
    real = build(SETTINGS)
    original = Path.home() / "Library/Logs/DiagnosticReports/x.ips"
    masked = real.text(str(original))
    assert masked.startswith("~/")
    assert Path(masked).expanduser() == original


# ── walk ──────────────────────────────────────────────────────────────────────


def test_walk_reaches_strings_nested_in_lists_and_dicts(redactor):
    walked = redactor.walk(
        {"reports": [{"path": "/Users/testuser/a.ips", "count": 3}], "ok": True}
    )
    assert walked["reports"][0]["path"] == "~/a.ips"


def test_walk_leaves_non_strings_untouched(redactor):
    walked = redactor.walk({"n": 42, "ok": True, "none": None, "f": 1.5})
    assert walked == {"n": 42, "ok": True, "none": None, "f": 1.5}


def test_walk_handles_tuples(redactor):
    assert redactor.walk(("/Users/testuser",)) == ("~",)


def test_a_disabled_redactor_changes_nothing():
    off = build(
        type(SETTINGS)(**{**SETTINGS.__dict__, "enabled": False}),
        home=Path("/Users/testuser"),
        hostname="h",
    )
    assert (
        off.text("/Users/testuser at 192.168.1.42") == "/Users/testuser at 192.168.1.42"
    )
    assert off.walk({"a": "/Users/testuser"}) == {"a": "/Users/testuser"}


def test_mapping_is_walk_for_a_result_dict(redactor):
    assert redactor.mapping({"p": "/Users/testuser/a"}) == {"p": "~/a"}


def test_mapping_passes_through_when_disabled():
    off = build(
        type(SETTINGS)(**{**SETTINGS.__dict__, "enabled": False}),
        home=Path("/Users/testuser"),
        hostname="h",
    )
    assert off.mapping({"p": "/Users/testuser"}) == {"p": "/Users/testuser"}
