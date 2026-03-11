from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pathlib import Path
import http.client
import os
import pytest
import signal
import socket
import subprocess
import sys
import time

import run_sse
from run_sse import apply_mcp_api_key_middleware, create_sse_app


def _build_client(*, client=("testclient", 50000)) -> TestClient:
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    wrapped_app = apply_mcp_api_key_middleware(app)
    return TestClient(wrapped_app, client=client)


def test_sse_auth_rejects_when_api_key_not_configured_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)
    with _build_client() as client:
        response = client.get("/ping")
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "api_key_not_configured"


@pytest.mark.parametrize("override_value", ["true", "enabled"])
def test_sse_auth_allows_when_explicit_insecure_local_override_is_enabled(
    monkeypatch, override_value: str
) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", override_value)
    with _build_client(client=("127.0.0.1", 50000)) as client:
        response = client.get("/ping", headers={"Host": "127.0.0.1"})
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_sse_auth_rejects_insecure_local_override_for_non_loopback_client(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    with _build_client(client=("203.0.113.10", 50000)) as client:
        response = client.get("/ping")
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "insecure_local_override_requires_loopback"


def test_sse_auth_rejects_insecure_local_override_when_forwarded_headers_present(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    headers = {"X-Forwarded-For": "198.51.100.8"}
    with _build_client(client=("127.0.0.1", 50000)) as client:
        response = client.get("/ping", headers=headers)
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "insecure_local_override_requires_loopback"


def test_sse_auth_rejects_insecure_local_override_when_host_is_not_loopback(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    with _build_client(client=("127.0.0.1", 50000)) as client:
        response = client.get(
            "/ping",
            headers={"Host": "memory-palace.example"},
        )
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "insecure_local_override_requires_loopback"


def test_sse_auth_rejects_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-sse-secret")
    with _build_client() as client:
        response = client.get("/ping")
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "invalid_or_missing_api_key"


def test_sse_auth_accepts_x_mcp_api_key_header(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-sse-secret")
    headers = {"X-MCP-API-Key": "week6-sse-secret"}
    with _build_client() as client:
        response = client.get("/ping", headers=headers)
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_sse_auth_accepts_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-sse-secret")
    headers = {"Authorization": "Bearer week6-sse-secret"}
    with _build_client() as client:
        response = client.get("/ping", headers=headers)
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_sse_auth_preserves_streaming_response(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-sse-secret")

    app = FastAPI()

    @app.get("/stream")
    async def stream():
        async def _events():
            yield "event: endpoint\n\n"
            yield "data: ok\n\n"

        return StreamingResponse(_events(), media_type="text/event-stream")

    wrapped_app = apply_mcp_api_key_middleware(app)
    with TestClient(wrapped_app) as client:
        with client.stream("GET", "/stream", headers={"X-MCP-API-Key": "week6-sse-secret"}) as response:
            assert response.status_code == 200
            lines = list(response.iter_lines())
    assert "event: endpoint" in lines
    assert "data: ok" in lines


def test_sse_health_endpoint_is_public(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)

    with TestClient(create_sse_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "memory-palace-sse"}


def test_sse_auth_does_not_raise_on_streaming_disconnect(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(**os.environ)
    env["MCP_API_KEY"] = "week6-sse-secret"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'streaming_disconnect.db'}"
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    server = subprocess.Popen(
        [
            sys.executable,
            "run_sse.py",
        ],
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
                pytest.fail("uvicorn exited before the streaming test could connect")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("timed out waiting for streaming test server to start")

        request = (
            "GET /sse HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Accept: text/event-stream\r\n"
            "X-MCP-API-Key: week6-sse-secret\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8")

        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(request)
            chunks = []
            deadline = time.time() + 5
            while time.time() < deadline:
                chunk = client.recv(4096).decode("utf-8", errors="ignore")
                if not chunk:
                    break
                chunks.append(chunk)
                if "event: endpoint" in "".join(chunks):
                    break
            received = "".join(chunks)
            assert "200 OK" in received
            assert "event: endpoint" in received

        time.sleep(0.5)
    finally:
        server.terminate()
        try:
            output, _ = server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            output, _ = server.communicate(timeout=5)

    assert "AssertionError: Unexpected message" not in output


def test_sse_auth_does_not_raise_on_streaming_shutdown(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(**os.environ)
    env["MCP_API_KEY"] = "week6-sse-secret"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'streaming_shutdown.db'}"
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    server = subprocess.Popen(
        [
            sys.executable,
            "run_sse.py",
        ],
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    client = None
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if server.poll() is not None:
                pytest.fail("uvicorn exited before the shutdown test could connect")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("timed out waiting for shutdown test server to start")

        request = (
            "GET /sse HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Accept: text/event-stream\r\n"
            "X-MCP-API-Key: week6-sse-secret\r\n"
            "\r\n"
        ).encode("utf-8")

        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(request)
        received = ""
        deadline = time.time() + 5
        while time.time() < deadline:
            chunk = client.recv(4096).decode("utf-8", errors="ignore")
            if not chunk:
                break
            received += chunk
            if "event: endpoint" in received:
                break
        assert "200 OK" in received
        assert "event: endpoint" in received

        server.send_signal(signal.SIGINT)
        if client is not None:
            client.close()
            client = None
        output, _ = server.communicate(timeout=10)
    finally:
        if client is not None:
            client.close()
        if server.poll() is None:
            server.terminate()
            try:
                output, _ = server.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                output, _ = server.communicate(timeout=5)

    assert "Expected ASGI message 'http.response.body'" not in output
    assert "RuntimeError:" not in output


def test_sse_auth_rejects_when_posting_to_closed_message_stream(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(**os.environ)
    env["MCP_API_KEY"] = "week6-sse-secret"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'closed_message_stream.db'}"
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    server = subprocess.Popen(
        [
            sys.executable,
            "run_sse.py",
        ],
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
                pytest.fail("uvicorn exited before the closed-stream test could connect")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("timed out waiting for closed-stream test server to start")

        session_id = None
        request = (
            "GET /sse HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Accept: text/event-stream\r\n"
            "X-MCP-API-Key: week6-sse-secret\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(request)
            received = ""
            deadline = time.time() + 5
            while time.time() < deadline:
                chunk = client.recv(4096).decode("utf-8", errors="ignore")
                if not chunk:
                    break
                received += chunk
                if "session_id=" in received:
                    session_id = received.split("session_id=", 1)[1].splitlines()[0].strip()
                    break
        assert session_id

        time.sleep(0.2)

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                f"/messages/?session_id={session_id}",
                body='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
                headers={
                    "Content-Type": "application/json",
                    "X-MCP-API-Key": "week6-sse-secret",
                },
            )
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()

        assert response.status in {404, 410}

        time.sleep(0.5)
    finally:
        server.terminate()
        try:
            output, _ = server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            output, _ = server.communicate(timeout=5)

    assert "ClosedResourceError" not in output
    assert "Traceback" not in output


def test_sse_main_runs_mcp_startup_before_uvicorn(monkeypatch) -> None:
    call_order = []

    async def _fake_startup() -> None:
        call_order.append("startup")

    def _fake_create_sse_app():
        call_order.append("create_sse_app")
        return {"app": "fake"}

    def _fake_uvicorn_run(app, host, port):
        call_order.append(("uvicorn", host, port, app))

    monkeypatch.setattr(run_sse, "mcp_startup", _fake_startup)
    monkeypatch.setattr(run_sse, "create_sse_app", _fake_create_sse_app)
    monkeypatch.setattr(run_sse.uvicorn, "run", _fake_uvicorn_run)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8010")

    run_sse.main()

    assert call_order[0] == "startup"
    assert call_order[1] == "create_sse_app"
    assert call_order[2][0] == "uvicorn"
    assert call_order[2][1] == "127.0.0.1"
    assert call_order[2][2] == 8010


def test_sse_malformed_message_returns_400_without_internal_error_event(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(**os.environ)
    env["MCP_API_KEY"] = "week6-sse-secret"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'malformed_message.db'}"
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    server = subprocess.Popen(
        [
            sys.executable,
            "run_sse.py",
        ],
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    sse_socket = None
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if server.poll() is not None:
                pytest.fail("uvicorn exited before the malformed-message test could connect")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("timed out waiting for malformed-message test server to start")

        session_id = None
        request = (
            "GET /sse HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Accept: text/event-stream\r\n"
            "X-MCP-API-Key: week6-sse-secret\r\n"
            "Connection: keep-alive\r\n"
            "\r\n"
        ).encode("utf-8")
        sse_socket = socket.create_connection(("127.0.0.1", port), timeout=5)
        sse_socket.sendall(request)
        sse_socket.settimeout(5)

        received = ""
        deadline = time.time() + 5
        while time.time() < deadline:
            chunk = sse_socket.recv(4096).decode("utf-8", errors="ignore")
            if not chunk:
                break
            received += chunk
            if "session_id=" in received:
                session_id = received.split("session_id=", 1)[1].splitlines()[0].strip()
                break
        assert session_id

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                f"/messages/?session_id={session_id}",
                body='{"bad":',
                headers={
                    "Content-Type": "application/json",
                    "X-MCP-API-Key": "week6-sse-secret",
                },
            )
            response = connection.getresponse()
            payload = response.read().decode("utf-8", errors="ignore")
        finally:
            connection.close()

        assert response.status == 400
        assert "Could not parse message" in payload

        sse_socket.settimeout(1)
        trailing = ""
        try:
            while True:
                chunk = sse_socket.recv(4096).decode("utf-8", errors="ignore")
                if not chunk:
                    break
                trailing += chunk
        except socket.timeout:
            pass

        assert "Internal Server Error" not in trailing
        time.sleep(0.2)
    finally:
        if sse_socket is not None:
            try:
                sse_socket.close()
            except OSError:
                pass
        server.terminate()
        try:
            output, _ = server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            output, _ = server.communicate(timeout=5)

    assert "Received exception from stream" not in output
    assert "Traceback" not in output
