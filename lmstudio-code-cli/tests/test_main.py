from unittest.mock import MagicMock, patch

from lmstudio_code_cli.main import _handle_slash


def _make_agent():
    agent = MagicMock()
    agent.model = "test-model"
    agent.config.base_url = "http://localhost:1234/v1"
    agent.config.cwd = "/tmp"
    return agent


# ── /cls ──────────────────────────────────────────────────────────────────────

def test_cls_clears_screen():
    agent = _make_agent()
    with patch("lmstudio_code_cli.main.click.clear") as mock_clear, \
         patch("lmstudio_code_cli.main.ui.print_welcome"):
        _handle_slash("/cls", agent)
    mock_clear.assert_called_once()


def test_cls_reprints_welcome():
    agent = _make_agent()
    with patch("lmstudio_code_cli.main.click.clear"), \
         patch("lmstudio_code_cli.main.ui.print_welcome") as mock_welcome:
        _handle_slash("/cls", agent)
    mock_welcome.assert_called_once_with(
        agent.model, agent.config.base_url, agent.config.cwd
    )


def test_cls_returns_none():
    agent = _make_agent()
    with patch("lmstudio_code_cli.main.click.clear"), \
         patch("lmstudio_code_cli.main.ui.print_welcome"):
        result = _handle_slash("/cls", agent)
    assert result is None
