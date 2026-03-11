import importlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import mcp_server as mcp_server_module
import run_sse


def _reload_mcp_server():
    return importlib.reload(mcp_server_module)


def test_remote_host_disables_dns_rebinding_protection(monkeypatch) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")

    module = _reload_mcp_server()

    assert module.mcp.settings.host == "0.0.0.0"
    assert module.mcp.settings.transport_security is not None
    assert module.mcp.settings.transport_security.enable_dns_rebinding_protection is False


def test_loopback_host_keeps_dns_rebinding_protection(monkeypatch) -> None:
    monkeypatch.setenv("HOST", "127.0.0.1")

    module = _reload_mcp_server()

    assert module.mcp.settings.host == "127.0.0.1"
    assert module.mcp.settings.transport_security is not None
    assert module.mcp.settings.transport_security.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in module.mcp.settings.transport_security.allowed_hosts


def test_run_sse_main_binds_loopback_by_default(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    async def _noop_startup() -> None:
        return None

    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(run_sse, "mcp_startup", _noop_startup)
    monkeypatch.setattr(run_sse, "create_sse_app", lambda: "app")
    monkeypatch.setattr(
        run_sse.uvicorn,
        "run",
        lambda app, host, port: calls.append((host, port)),
    )

    run_sse.main()

    assert calls == [("127.0.0.1", 8000)]


def test_sse_transport_security_rejects_invalid_host_without_traceback(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(os.environ)
    env["MCP_API_KEY"] = "transport-secret"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'transport_security.db'}"
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)

    server = subprocess.Popen(
        [sys.executable, "run_sse.py"],
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if server.poll() is not None:
                pytest.fail("uvicorn exited before the transport security test could connect")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("timed out waiting for transport security test server to start")

        request = (
            "GET /sse HTTP/1.1\r\n"
            "Host: example.invalid\r\n"
            "Accept: text/event-stream\r\n"
            "X-MCP-API-Key: transport-secret\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8")

        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(request)
            received = client.recv(4096).decode("utf-8", errors="ignore")

        assert "421 Misdirected Request" in received
    finally:
        server.terminate()
        try:
            output, _ = server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            output, _ = server.communicate(timeout=5)

    assert "Request validation failed" not in output
    assert "Traceback" not in output
