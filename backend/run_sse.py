import os
import sys
import hmac
import asyncio
import uvicorn
from typing import Optional

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Ensure we can import from backend dir
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from mcp_server import mcp, startup as mcp_startup

_MCP_API_KEY_ENV = "MCP_API_KEY"
_MCP_API_KEY_HEADER = "X-MCP-API-Key"
_MCP_API_KEY_ALLOW_INSECURE_LOCAL_ENV = "MCP_API_KEY_ALLOW_INSECURE_LOCAL"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on", "enabled"}
_LOOPBACK_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost"}
_FORWARDED_HEADER_NAMES = {
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-real-ip",
    "x-client-ip",
    "true-client-ip",
    "cf-connecting-ip",
}


def _get_configured_mcp_api_key() -> str:
    return str(os.getenv(_MCP_API_KEY_ENV) or "").strip()


def _allow_insecure_local_without_api_key() -> bool:
    value = str(os.getenv(_MCP_API_KEY_ALLOW_INSECURE_LOCAL_ENV) or "").strip().lower()
    return value in _TRUTHY_ENV_VALUES


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not isinstance(authorization, str):
        return None
    value = authorization.strip()
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token if token else None


def _is_loopback_scope(scope: Scope) -> bool:
    client = scope.get("client")
    host = ""
    if isinstance(client, tuple) and client:
        host = str(client[0] or "").strip().lower()
    elif client is not None:
        host = str(getattr(client, "host", "") or "").strip().lower()
    if host not in _LOOPBACK_CLIENT_HOSTS:
        return False

    headers = Headers(scope=scope)
    for header_name in _FORWARDED_HEADER_NAMES:
        header_value = headers.get(header_name)
        if isinstance(header_value, str) and header_value.strip():
            return False
    return True


def apply_mcp_api_key_middleware(app: ASGIApp) -> ASGIApp:
    async def _auth_middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return

        configured = _get_configured_mcp_api_key()
        headers = Headers(scope=scope)
        if not configured:
            if _allow_insecure_local_without_api_key() and _is_loopback_scope(scope):
                await app(scope, receive, send)
                return
            reason = (
                "insecure_local_override_requires_loopback"
                if _allow_insecure_local_without_api_key()
                else "api_key_not_configured"
            )
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "mcp_sse_auth_failed",
                    "reason": reason,
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        provided = (
            str(headers.get(_MCP_API_KEY_HEADER, "")).strip()
            or _extract_bearer_token(headers.get("Authorization"))
        )
        if not provided or not hmac.compare_digest(provided, configured):
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "mcp_sse_auth_failed",
                    "reason": "invalid_or_missing_api_key",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await app(scope, receive, send)

    return _auth_middleware


def create_sse_app() -> ASGIApp:
    app = mcp.sse_app()
    return apply_mcp_api_key_middleware(app)


def main():
    """
    Run the Memory Palace MCP server using SSE (Server-Sent Events) transport.
    This is required for clients that don't support stdio (like some web-based tools).
    """
    print("Initializing Memory Palace SSE Server...")
    asyncio.run(mcp_startup())
    
    # Create the Starlette app for SSE with optional API key guard.
    app = create_sse_app()
    
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    print(f"Starting SSE Server on http://{host}:{port}")
    print(f"SSE Endpoint: http://{host}:{port}/sse")
    
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
