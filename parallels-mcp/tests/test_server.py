import json
import os
import pytest
from unittest.mock import AsyncMock, patch

from parallels_mcp.server import (
    _run,
    parallels_exec,
    parallels_force_stop,
    parallels_list,
    parallels_pause,
    parallels_screenshot,
    parallels_snapshot_create,
    parallels_snapshot_list,
    parallels_snapshot_restore,
    parallels_start,
    parallels_status,
    parallels_stop,
)


# ── _run ───────────────────────────────────────────────────────────────────────

async def test_run_returns_stripped_stdout():
    result = await _run(["echo", "hello"])
    assert result == "hello"


async def test_run_timeout_returns_timeout_string():
    result = await _run(["sleep", "10"], timeout=0.05)
    assert result.startswith("[timeout after")


async def test_run_merges_stderr_into_stdout():
    result = await _run(["sh", "-c", "echo out; echo err >&2"])
    assert "out" in result
    assert "err" in result


# ── parallels_list ─────────────────────────────────────────────────────────────

async def test_parallels_list_formats_vm_table():
    vms = [
        {"name": "ubuntu-24", "status": "running", "os": "ubuntu", "uuid": "aaa-111"},
        {"name": "win11", "status": "stopped", "os": "windows", "uuid": "bbb-222"},
    ]
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = json.dumps(vms)
        result = await parallels_list()
        assert "ubuntu-24" in result
        assert "running" in result
        assert "aaa-111" in result
        assert "win11" in result
        assert "bbb-222" in result


async def test_parallels_list_empty_array_returns_header():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "[]"
        result = await parallels_list()
        assert "Name" in result
        assert "Status" in result


async def test_parallels_list_invalid_json_returns_raw():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "prlctl: command not found"
        result = await parallels_list()
        assert result == "prlctl: command not found"


async def test_parallels_list_null_json_returns_raw():
    # json.loads("null") → None → TypeError on iteration
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "null"
        result = await parallels_list()
        assert result == "null"


async def test_parallels_list_non_dict_items_returns_raw():
    # json array of strings → AttributeError on .get() — the bug we fixed
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = '["vm1", "vm2"]'
        result = await parallels_list()
        assert result == '["vm1", "vm2"]'


# ── parallels_snapshot_list ────────────────────────────────────────────────────

async def test_snapshot_list_formats_table():
    snaps = {
        "snap-uuid-1": {"name": "Before upgrade", "date": "2024-01-15 10:00:00"},
        "snap-uuid-2": {"name": "Clean state", "date": "2024-02-01 09:30:00"},
    }
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = json.dumps(snaps)
        result = await parallels_snapshot_list("ubuntu-24")
        assert "Before upgrade" in result
        assert "snap-uuid-1" in result
        assert "Clean state" in result
        assert "snap-uuid-2" in result


async def test_snapshot_list_empty_dict_returns_no_snapshots():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "{}"
        result = await parallels_snapshot_list("ubuntu-24")
        assert result == "No snapshots found."


async def test_snapshot_list_invalid_json_returns_raw():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "error: VM not found"
        result = await parallels_snapshot_list("missing-vm")
        assert result == "error: VM not found"


async def test_snapshot_list_calls_correct_vm():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "{}"
        await parallels_snapshot_list("my-vm")
        cmd = mock_run.call_args[0][0]
        assert "my-vm" in cmd
        assert "snapshot-list" in cmd


# ── parallels_screenshot ───────────────────────────────────────────────────────

async def test_screenshot_returns_image_and_cleans_up(tmp_path):
    fake_png = b"\x89PNG fake data"
    fake_path = str(tmp_path / "vm-screenshot.png")

    async def fake_run(cmd, timeout=60):
        with open(fake_path, "wb") as f:
            f.write(fake_png)
        return ""

    with patch("parallels_mcp.server._run", side_effect=fake_run), \
         patch("parallels_mcp.server.os.path.join", return_value=fake_path):
        from mcp.server.fastmcp import Image
        result = await parallels_screenshot("ubuntu-24")
        assert isinstance(result, Image)
        assert not os.path.exists(fake_path)


async def test_screenshot_raises_when_file_not_created(tmp_path):
    fake_path = str(tmp_path / "vm-screenshot.png")

    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run, \
         patch("parallels_mcp.server.os.path.join", return_value=fake_path):
        mock_run.return_value = "capture failed: VM not running"
        with pytest.raises(RuntimeError, match="prlctl capture failed"):
            await parallels_screenshot("stopped-vm")
        assert not os.path.exists(fake_path)


# ── pass-through tools ─────────────────────────────────────────────────────────

async def test_parallels_start_sends_start_command():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "VM started"
        result = await parallels_start("ubuntu-24")
        assert mock_run.call_args[0][0] == ["prlctl", "start", "ubuntu-24"]
        assert result == "VM started"


async def test_parallels_stop_sends_stop_command():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "VM stopped"
        await parallels_stop("ubuntu-24")
        assert mock_run.call_args[0][0] == ["prlctl", "stop", "ubuntu-24"]


async def test_parallels_force_stop_sends_kill_flag():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "VM killed"
        await parallels_force_stop("ubuntu-24")
        cmd = mock_run.call_args[0][0]
        assert "stop" in cmd
        assert "--kill" in cmd
        assert "ubuntu-24" in cmd


async def test_parallels_pause_sends_pause_command():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "VM paused"
        await parallels_pause("ubuntu-24")
        assert mock_run.call_args[0][0] == ["prlctl", "pause", "ubuntu-24"]


async def test_parallels_status_sends_status_command():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "VM is running"
        await parallels_status("ubuntu-24")
        assert mock_run.call_args[0][0] == ["prlctl", "status", "ubuntu-24"]


async def test_parallels_snapshot_create_includes_name():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "snapshot created"
        await parallels_snapshot_create("ubuntu-24", "pre-upgrade")
        cmd = mock_run.call_args[0][0]
        assert "snapshot-create" in cmd
        assert "ubuntu-24" in cmd
        assert "--name" in cmd
        assert "pre-upgrade" in cmd


async def test_parallels_snapshot_restore_includes_id():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "snapshot restored"
        await parallels_snapshot_restore("ubuntu-24", "snap-uuid-1")
        cmd = mock_run.call_args[0][0]
        assert "snapshot-restore" in cmd
        assert "ubuntu-24" in cmd
        assert "--id" in cmd
        assert "snap-uuid-1" in cmd


async def test_parallels_exec_passes_command_and_timeout():
    with patch("parallels_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "output"
        await parallels_exec("ubuntu-24", "ls /tmp", timeout=30)
        call_args, call_kwargs = mock_run.call_args
        cmd = call_args[0]
        assert "ubuntu-24" in cmd
        assert "ls /tmp" in cmd
        assert "/bin/sh" in cmd
        assert call_kwargs.get("timeout") == 30
