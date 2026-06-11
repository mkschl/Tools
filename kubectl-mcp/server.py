"""Generic kubectl MCP server — requires KUBECONFIG env var."""

import asyncio
import os

from mcp.server.fastmcp import FastMCP

KUBECONFIG = os.getenv("KUBECONFIG", "")

mcp = FastMCP("kubectl")


def _env() -> dict[str, str]:
    env = os.environ.copy()
    if KUBECONFIG:
        env["KUBECONFIG"] = KUBECONFIG
    return env


async def _run(cmd: list[str], timeout: int = 60) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_env(),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"[timeout after {timeout}s]"
    return stdout.decode(errors="replace").strip()


def _confirm_required(confirm: bool, action: str) -> str | None:
    if not confirm:
        return f"Destructive action '{action}' requires confirm=true."
    return None


@mcp.tool()
async def kubectl_get(
    resource: str,
    namespace: str = "default",
    label_selector: str = "",
    all_namespaces: bool = False,
) -> str:
    """Get Kubernetes resources (pods, jobs, nodes, pvcs, services, deployments, ...).

    Set all_namespaces=true to query across all namespaces.
    Use label_selector for filtering, e.g. 'hardware=pi5'.
    """
    cmd = ["kubectl", "get", resource, "-o", "wide"]
    if all_namespaces:
        cmd.append("--all-namespaces")
    else:
        cmd += ["-n", namespace]
    if label_selector:
        cmd += ["-l", label_selector]
    return await _run(cmd)


@mcp.tool()
async def kubectl_describe(
    resource: str,
    name: str,
    namespace: str = "default",
) -> str:
    """Describe a specific Kubernetes resource (e.g. resource='pod', name='my-pod')."""
    return await _run(["kubectl", "describe", resource, name, "-n", namespace])


@mcp.tool()
async def kubectl_logs(
    pod: str,
    namespace: str = "default",
    container: str = "",
    tail: int = 100,
    previous: bool = False,
) -> str:
    """Fetch logs from a pod. Use tail to limit output lines."""
    cmd = ["kubectl", "logs", pod, "-n", namespace, f"--tail={tail}"]
    if container:
        cmd += ["-c", container]
    if previous:
        cmd.append("--previous")
    return await _run(cmd, timeout=30)


@mcp.tool()
async def kubectl_nodes() -> str:
    """List all cluster nodes with status, roles, and labels."""
    output = await _run(["kubectl", "get", "nodes", "-o", "wide"])
    labels = await _run(["kubectl", "get", "nodes", "--show-labels"])
    return f"{output}\n\n--- Labels ---\n{labels}"


@mcp.tool()
async def kubectl_top(namespace: str = "") -> str:
    """Show CPU/memory usage for pods or nodes (requires metrics-server)."""
    if namespace:
        return await _run(["kubectl", "top", "pods", "-n", namespace])
    node_out = await _run(["kubectl", "top", "nodes"])
    pod_out = await _run(["kubectl", "top", "pods", "--all-namespaces"])
    return f"--- Nodes ---\n{node_out}\n\n--- Pods ---\n{pod_out}"


@mcp.tool()
async def kubectl_delete_job(
    name: str,
    namespace: str = "default",
    confirm: bool = False,
) -> str:
    """Delete a Kubernetes job. Requires confirm=true."""
    if err := _confirm_required(confirm, f"delete job {name}"):
        return err
    return await _run(["kubectl", "delete", "job", name, "-n", namespace])


@mcp.tool()
async def kubectl_apply(
    manifest_path: str,
    namespace: str = "default",
) -> str:
    """Apply a Kubernetes manifest file. manifest_path must be an absolute path on the MCP host."""
    return await _run(["kubectl", "apply", "-f", manifest_path, "-n", namespace])


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
