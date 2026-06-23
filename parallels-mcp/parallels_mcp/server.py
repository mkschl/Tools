"""Parallels Desktop MCP server — wraps prlctl CLI."""

import asyncio
import json
import os
import tempfile
import uuid

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("parallels")


async def _run(cmd: list[str], timeout: int = 60) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"[timeout after {timeout}s]"
    return stdout.decode(errors="replace").strip()


@mcp.tool()
async def parallels_list() -> str:
    """List all Parallels VMs with their current status (running/stopped/paused/suspended)."""
    output = await _run(["prlctl", "list", "-a", "--json"])
    try:
        vms = json.loads(output)
        lines = [f"{'Name':<40} {'Status':<12} {'OS':<20} {'ID'}"]
        lines.append("-" * 95)
        for vm in vms:
            name = vm.get("name", "")
            status = vm.get("status", "")
            os_ver = vm.get("os", "")
            uid = vm.get("uuid", "")
            lines.append(f"{name:<40} {status:<12} {os_ver:<20} {uid}")
        return "\n".join(lines)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return output


@mcp.tool()
async def parallels_start(vm: str) -> str:
    """Boot a Parallels VM.

    Args:
        vm: VM name or UUID.
    """
    return await _run(["prlctl", "start", vm])


@mcp.tool()
async def parallels_stop(vm: str) -> str:
    """Gracefully shut down a running Parallels VM.

    Args:
        vm: VM name or UUID.
    """
    return await _run(["prlctl", "stop", vm])


@mcp.tool()
async def parallels_force_stop(vm: str) -> str:
    """Force-kill a Parallels VM (equivalent to pulling the power plug).

    Args:
        vm: VM name or UUID.
    """
    return await _run(["prlctl", "stop", vm, "--kill"])


@mcp.tool()
async def parallels_pause(vm: str) -> str:
    """Pause (suspend) a running Parallels VM.

    Args:
        vm: VM name or UUID.
    """
    return await _run(["prlctl", "pause", vm])


@mcp.tool()
async def parallels_status(vm: str) -> str:
    """Get the current status of a Parallels VM.

    Args:
        vm: VM name or UUID.
    """
    return await _run(["prlctl", "status", vm])


@mcp.tool()
async def parallels_snapshot_list(vm: str) -> str:
    """List all snapshots for a VM.

    Args:
        vm: VM name or UUID.
    """
    output = await _run(["prlctl", "snapshot-list", vm, "--json"])
    try:
        snaps = json.loads(output)
        if not snaps:
            return "No snapshots found."
        lines = [f"{'Name':<40} {'Date':<22} {'ID'}"]
        lines.append("-" * 90)
        for sid, snap in snaps.items():
            name = snap.get("name", "")
            date = snap.get("date", "")
            lines.append(f"{name:<40} {date:<22} {sid}")
        return "\n".join(lines)
    except (json.JSONDecodeError, AttributeError):
        return output


@mcp.tool()
async def parallels_snapshot_create(vm: str, name: str) -> str:
    """Create a named snapshot of a VM.

    Args:
        vm: VM name or UUID.
        name: Snapshot name.
    """
    return await _run(["prlctl", "snapshot-create", vm, "--name", name])


@mcp.tool()
async def parallels_snapshot_restore(vm: str, snapshot_id: str) -> str:
    """Restore a VM to a specific snapshot.

    Args:
        vm: VM name or UUID.
        snapshot_id: Snapshot UUID (from parallels/snapshot_list).
    """
    return await _run(["prlctl", "snapshot-restore", vm, "--id", snapshot_id])


@mcp.tool()
async def parallels_exec(vm: str, command: str, timeout: int = 120) -> str:
    """Run an arbitrary shell command inside a running VM and return stdout+stderr.

    The command is executed via /bin/sh -c, so pipes, redirects, and quoting work as expected.

    Args:
        vm: VM name or UUID.
        command: Shell command to run inside the VM.
        timeout: Seconds to wait before killing the command (default 120).
    """
    return await _run(
        ["prlctl", "exec", vm, "--", "/bin/sh", "-c", command],
        timeout=timeout,
    )


@mcp.tool()
async def parallels_screenshot(vm: str) -> Image:
    """Capture a screenshot of a running VM's display.

    Returns the screen as a PNG image so you can visually inspect the VM state.

    Args:
        vm: VM name or UUID.
    """
    path = os.path.join(tempfile.gettempdir(), f"vm-screenshot-{uuid.uuid4()}.png")
    try:
        result = await _run(["prlctl", "capture", vm, "--file", path])
        if not os.path.exists(path):
            raise RuntimeError(f"prlctl capture failed: {result}")
        with open(path, "rb") as f:
            data = f.read()
        return Image(data=data, format="png")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
