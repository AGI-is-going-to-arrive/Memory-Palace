import importlib

import mcp_server as mcp_server_module


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
