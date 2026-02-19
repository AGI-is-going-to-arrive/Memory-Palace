import os
import sys
import hmac
import uvicorn
from typing import Optional, Callable, Awaitable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

# Ensure we can import from backend dir
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from mcp_server import mcp

_MCP_API_KEY_ENV = "MCP_API_KEY"
_MCP_API_KEY_HEADER = "X-MCP-API-Key"
_MCP_API_KEY_ALLOW_INSECURE_LOCAL_ENV = "MCP_API_KEY_ALLOW_INSECURE_LOCAL"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_LOOPBACK_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _get_configured_mcp_api_key() -> str:
    return str(os.getenv(_MCP_API_KEY_ENV) or "").strip()


def _allow_insecure_local_without_api_key() -> bool:
    value = str(os.getenv(_MCP_API_KEY_ALLOW_INSECURE_LOCAL_ENV) or "").strip().lower()
    return value in _TRUTHY_ENV_VALUES


def _is_loopback_request(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = str(getattr(client, "host", "") or "").strip().lower()
    return host in _LOOPBACK_CLIENT_HOSTS


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


def apply_mcp_api_key_middleware(app: ASGIApp) -> ASGIApp:
    async def _auth_middleware(request: Request, call_next: Callable[[Request], Awaitable]):
        configured = _get_configured_mcp_api_key()
        if not configured:
            if _allow_insecure_local_without_api_key() and _is_loopback_request(request):
                return await call_next(request)
            reason = (
                "insecure_local_override_requires_loopback"
                if _allow_insecure_local_without_api_key()
                else "api_key_not_configured"
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "mcp_sse_auth_failed",
                    "reason": reason,
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        provided = (
            str(request.headers.get(_MCP_API_KEY_HEADER, "")).strip()
            or _extract_bearer_token(request.headers.get("Authorization"))
        )
        if not provided or not hmac.compare_digest(provided, configured):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "mcp_sse_auth_failed",
                    "reason": "invalid_or_missing_api_key",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    app.middleware("http")(_auth_middleware)
    return app


def create_sse_app() -> ASGIApp:
    app = mcp.sse_app("/sse")
    return apply_mcp_api_key_middleware(app)


def main():
    """
    Run the Memory Palace MCP server using SSE (Server-Sent Events) transport.
    This is required for clients that don't support stdio (like some web-based tools).
    """
    print("Initializing Memory Palace SSE Server...")
    
    # Create the Starlette app for SSE with optional API key guard.
    app = create_sse_app()
    
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    print(f"Starting SSE Server on http://{host}:{port}")
    print(f"SSE Endpoint: http://{host}:{port}/sse")
    
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
