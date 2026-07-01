import pytest
from unittest.mock import AsyncMock, patch

from kubectl_mcp.server import (
    _confirm_required,
    _run,
    kubectl_apply,
    kubectl_delete_job,
    kubectl_delete_pods,
    kubectl_exec,
    kubectl_get,
    kubectl_logs,
    kubectl_nodes,
    kubectl_rollout_restart,
    kubectl_top,
)


# ── _confirm_required ──────────────────────────────────────────────────────────

def test_confirm_required_returns_message_when_false():
    result = _confirm_required(False, "delete job foo")
    assert result is not None
    assert "confirm=true" in result


def test_confirm_required_returns_none_when_true():
    assert _confirm_required(True, "delete job foo") is None


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


# ── kubectl_delete_job ─────────────────────────────────────────────────────────

async def test_delete_job_requires_confirm():
    result = await kubectl_delete_job("my-job", confirm=False)
    assert "confirm=true" in result


async def test_delete_job_calls_kubectl_with_correct_args():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = 'job.batch "my-job" deleted'
        result = await kubectl_delete_job("my-job", namespace="prod", confirm=True)
        mock_run.assert_called_once_with(
            ["kubectl", "delete", "job", "my-job", "-n", "prod"]
        )
        assert result == 'job.batch "my-job" deleted'


# ── kubectl_get ────────────────────────────────────────────────────────────────

async def test_kubectl_get_default_namespace():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "pod output"
        await kubectl_get("pods")
        cmd = mock_run.call_args[0][0]
        assert "-n" in cmd
        assert "default" in cmd
        assert "--all-namespaces" not in cmd


async def test_kubectl_get_all_namespaces_excludes_n_flag():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "pod output"
        await kubectl_get("pods", all_namespaces=True)
        cmd = mock_run.call_args[0][0]
        assert "--all-namespaces" in cmd
        assert "-n" not in cmd


async def test_kubectl_get_label_selector_included():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "pod output"
        await kubectl_get("pods", label_selector="app=nginx")
        cmd = mock_run.call_args[0][0]
        assert "-l" in cmd
        assert "app=nginx" in cmd


async def test_kubectl_get_no_label_selector_by_default():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "pod output"
        await kubectl_get("pods")
        cmd = mock_run.call_args[0][0]
        assert "-l" not in cmd


# ── kubectl_logs ───────────────────────────────────────────────────────────────

async def test_kubectl_logs_default_args():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "log line"
        await kubectl_logs("my-pod")
        cmd = mock_run.call_args[0][0]
        assert "--tail=100" in cmd
        assert "-c" not in cmd
        assert "--previous" not in cmd


async def test_kubectl_logs_custom_tail():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "log line"
        await kubectl_logs("my-pod", tail=50)
        cmd = mock_run.call_args[0][0]
        assert "--tail=50" in cmd


async def test_kubectl_logs_with_container_and_previous():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "log line"
        await kubectl_logs("my-pod", container="sidecar", previous=True)
        cmd = mock_run.call_args[0][0]
        assert "-c" in cmd
        assert "sidecar" in cmd
        assert "--previous" in cmd


# ── kubectl_nodes ──────────────────────────────────────────────────────────────

async def test_kubectl_nodes_makes_two_calls_and_combines():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = ["node list", "label list"]
        result = await kubectl_nodes()
        assert mock_run.call_count == 2
        assert "node list" in result
        assert "label list" in result
        assert "Labels" in result


# ── kubectl_top ────────────────────────────────────────────────────────────────

async def test_kubectl_top_with_namespace_makes_one_call():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "pod top"
        result = await kubectl_top(namespace="kube-system")
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert "kube-system" in cmd
        assert result == "pod top"


async def test_kubectl_top_no_namespace_makes_two_calls():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = ["node top", "pod top"]
        result = await kubectl_top()
        assert mock_run.call_count == 2
        assert "node top" in result
        assert "pod top" in result


# ── kubectl_apply ──────────────────────────────────────────────────────────────

async def test_kubectl_apply_passes_manifest_path_and_namespace():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "applied"
        await kubectl_apply("/tmp/deploy.yaml", namespace="staging")
        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        assert "/tmp/deploy.yaml" in cmd
        assert "-n" in cmd
        assert "staging" in cmd


# ── kubectl_exec ───────────────────────────────────────────────────────────────

async def test_kubectl_exec_requires_confirm():
    result = await kubectl_exec("my-pod", ["echo", "hi"], confirm=False)
    assert "confirm=true" in result


async def test_kubectl_exec_calls_kubectl_with_correct_args():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "hi"
        await kubectl_exec("my-pod", ["echo", "hi"], namespace="prod", confirm=True)
        cmd = mock_run.call_args[0][0]
        assert cmd == ["kubectl", "exec", "my-pod", "-n", "prod", "--", "echo", "hi"]


async def test_kubectl_exec_with_container():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "hi"
        await kubectl_exec("my-pod", ["echo", "hi"], container="sidecar", confirm=True)
        cmd = mock_run.call_args[0][0]
        assert "-c" in cmd
        assert "sidecar" in cmd


# ── kubectl_rollout_restart ──────────────────────────────────────────────────────

async def test_kubectl_rollout_restart_requires_confirm():
    result = await kubectl_rollout_restart("deployment", "my-app", confirm=False)
    assert "confirm=true" in result


async def test_kubectl_rollout_restart_calls_kubectl_with_correct_args():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "restarted"
        await kubectl_rollout_restart("deployment", "my-app", namespace="prod", confirm=True)
        mock_run.assert_called_once_with(
            ["kubectl", "rollout", "restart", "deployment", "my-app", "-n", "prod"]
        )


# ── kubectl_delete_pods ──────────────────────────────────────────────────────────

async def test_kubectl_delete_pods_requires_confirm():
    result = await kubectl_delete_pods(name="my-pod", confirm=False)
    assert "confirm=true" in result


async def test_kubectl_delete_pods_requires_exactly_one_of_name_or_selector():
    result = await kubectl_delete_pods(confirm=True)
    assert "Exactly one" in result

    result = await kubectl_delete_pods(name="my-pod", label_selector="app=my-app", confirm=True)
    assert "Exactly one" in result


async def test_kubectl_delete_pods_by_name():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "deleted"
        await kubectl_delete_pods(name="my-pod", namespace="prod", confirm=True)
        cmd = mock_run.call_args[0][0]
        assert cmd == ["kubectl", "delete", "pod", "-n", "prod", "my-pod"]


async def test_kubectl_delete_pods_by_label_selector():
    with patch("kubectl_mcp.server._run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "deleted"
        await kubectl_delete_pods(label_selector="app=my-app", confirm=True)
        cmd = mock_run.call_args[0][0]
        assert "-l" in cmd
        assert "app=my-app" in cmd
