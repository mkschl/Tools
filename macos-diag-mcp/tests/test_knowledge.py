"""The shipped knowledge.toml is the server's whole reason to exist — test it, not a fixture."""

from macos_diag_mcp.knowledge import load_knowledge, match_cases, match_signals

knowledge = load_knowledge()


def test_the_shipped_knowledge_file_loads():
    assert knowledge.summary
    assert knowledge.safety_rules
    assert knowledge.lessons


def test_the_playbook_keeps_the_five_triage_steps_in_order():
    assert [s.number for s in knowledge.steps] == [1, 2, 3, 4, 5]


def test_every_step_names_the_tools_that_serve_it_except_the_manual_one():
    numbered = {s.number: s for s in knowledge.steps}
    assert all(numbered[n].tools for n in (1, 2, 3, 4))
    assert numbered[5].tools == ()  # comparing machines cannot be automated


def test_the_no_sudo_rule_is_stated():
    assert any("sudo" in rule for rule in knowledge.safety_rules)


# ── match_signals ─────────────────────────────────────────────────────────────


def test_an_empty_query_returns_the_whole_table():
    assert len(match_signals(knowledge.signals, "")) == len(knowledge.signals)


def test_a_raw_log_line_finds_its_signal():
    line = (
        "2026-09-22 07:45:01.123 E  coreautha[701:2f1a] "
        "SACAssertScreenLockViaTouchIDBlocked renewed"
    )
    assert (
        "Touch ID prompt is open" in match_signals(knowledge.signals, line)[0].meaning
    )


def test_a_normalized_line_still_finds_its_signal():
    normalized = "E  WindowServer [default] _CGXPackagesSetWindowConstraints: Invalid window <hex>"
    top = match_signals(knowledge.signals, normalized)[0]
    assert "lost its valid placement" in top.meaning


def test_the_music_artwork_signal_points_at_the_id_decoder():
    line = "Music[812:1a2b] artwork fetch failed for 61.99.123 Code=9069"
    assert "music_persistent_ids" in match_signals(knowledge.signals, line)[0].meaning


def test_an_unrelated_line_matches_nothing():
    assert match_signals(knowledge.signals, "zzzz quuxbar frobnicate") == []


def test_short_words_alone_do_not_produce_a_match():
    """'the' and 'for' appear in half the table and must not score."""
    assert match_signals(knowledge.signals, "the for and a of") == []


# ── match_cases ───────────────────────────────────────────────────────────────


def test_an_empty_query_returns_every_case():
    assert len(match_cases(knowledge.cases, "")) == len(knowledge.cases)


def test_the_touch_id_case_is_findable_and_carries_a_recovery():
    case = match_cases(knowledge.cases, "Touch ID")[0]
    assert "coreautha" in case.cause
    assert "Activity Monitor" in case.recovery
    assert "pam_reattach" in case.prevention


def test_the_music_case_records_that_stream_deck_was_ruled_out():
    case = match_cases(knowledge.cases, "artwork")[0]
    assert "Stream Deck" in case.prevention


def test_case_search_ignores_case():
    assert match_cases(knowledge.cases, "touch id") == match_cases(
        knowledge.cases, "TOUCH ID"
    )


def test_an_unknown_query_matches_no_case():
    assert match_cases(knowledge.cases, "kernel panic on a Raspberry Pi") == []
