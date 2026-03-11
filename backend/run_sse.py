import os
import sys
import hmac
import asyncio
import uvicorn
from contextlib import asynccontextmanager
from ipaddress import ip_address
from typing import Optional
from urllib.parse import quote
from uuid import UUID, uuid4

import anyio
from anyio import ClosedResourceError
import mcp.types as types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pydantic import ValidationError
from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

# Ensure we can import from backend dir
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from mcp_server import mcp, startup as mcp_startup
from mcp.server.sse import SseServerTransport
from mcp.shared.message import ServerMessageMetadata, SessionMessage

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
_SSE_HTTP_PATHS = {"/sse", "/sse/", "/messages", "/messages/", "/sse/messages", "/sse/messages/"}
_PUBLIC_HTTP_PATHS = {"/health", "/health/"}


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


def _is_loopback_hostname(value: Optional[str]) -> bool:
    if not value:
        return False
    hostname = str(value).strip().lower()
    if not hostname:
        return False
    if hostname.startswith("[") and hostname.endswith("]"):
        hostname = hostname[1:-1]
    if ":" in hostname and hostname.count(":") == 1 and hostname.rsplit(":", 1)[1].isdigit():
        hostname = hostname.rsplit(":", 1)[0]
    if hostname in _LOOPBACK_CLIENT_HOSTS:
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _extract_host_from_scope(scope: Scope) -> Optional[str]:
    headers = Headers(scope=scope)
    host_header = headers.get("host")
    if isinstance(host_header, str) and host_header.strip():
        return host_header
    server = scope.get("server")
    if isinstance(server, tuple) and server:
        return str(server[0] or "").strip()
    return None


def _is_direct_loopback_scope(scope: Scope) -> bool:
    if not _is_loopback_scope(scope):
        return False
    return _is_loopback_hostname(_extract_host_from_scope(scope))


def _should_suppress_stream_shutdown_runtime_error(scope: Scope, exc: RuntimeError) -> bool:
    if scope.get("type") != "http":
        return False
    path = str(scope.get("path") or "")
    if path not in _SSE_HTTP_PATHS:
        return False
    message = str(exc)
    return (
        "Expected ASGI message 'http.response.body'" in message
        and "'http.response.start'" in message
    )


def _should_suppress_request_validation_value_error(scope: Scope, exc: ValueError) -> bool:
    if scope.get("type") != "http":
        return False
    path = str(scope.get("path") or "")
    if path not in _SSE_HTTP_PATHS:
        return False
    return str(exc).strip() == "Request validation failed"


def _should_suppress_closed_resource_error(scope: Scope) -> bool:
    if scope.get("type") != "http":
        return False
    path = str(scope.get("path") or "")
    return path in {"/sse", "/sse/"}


class MemoryPalaceSseServerTransport(SseServerTransport):
    @asynccontextmanager
    async def connect_sse(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            raise ValueError("connect_sse can only handle HTTP requests")

        request = Request(scope, receive)
        error_response = await self._security.validate_request(request, is_post=False)
        if error_response:
            await error_response(scope, receive, send)
            raise ValueError("Request validation failed")

        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        session_id = uuid4()
        self._read_stream_writers[session_id] = read_stream_writer

        root_path = scope.get("root_path", "")
        full_message_path_for_client = root_path.rstrip("/") + self._endpoint
        client_post_uri_data = (
            f"{quote(full_message_path_for_client)}?session_id={session_id.hex}"
        )

        sse_stream_writer, sse_stream_reader = anyio.create_memory_object_stream(0)

        async def sse_writer():
            async with sse_stream_writer, write_stream_reader:
                await sse_stream_writer.send(
                    {"event": "endpoint", "data": client_post_uri_data}
                )
                async for session_message in write_stream_reader:
                    await sse_stream_writer.send(
                        {
                            "event": "message",
                            "data": session_message.message.model_dump_json(
                                by_alias=True, exclude_none=True
                            ),
                        }
                    )

        async with anyio.create_task_group() as tg:

            async def response_wrapper(
                scope: Scope, receive: Receive, send: Send
            ) -> None:
                try:
                    await EventSourceResponse(
                        content=sse_stream_reader,
                        data_sender_callable=sse_writer,
                    )(scope, receive, send)
                finally:
                    self._read_stream_writers.pop(session_id, None)
                    await read_stream_writer.aclose()
                    await write_stream_reader.aclose()

            tg.start_soon(response_wrapper, scope, receive, send)
            yield (read_stream, write_stream)

    async def handle_post_message(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        request = Request(scope, receive)

        error_response = await self._security.validate_request(request, is_post=True)
        if error_response:
            return await error_response(scope, receive, send)

        session_id_param = request.query_params.get("session_id")
        if session_id_param is None:
            response = Response("session_id is required", status_code=400)
            return await response(scope, receive, send)

        try:
            session_id = UUID(hex=session_id_param)
        except ValueError:
            response = Response("Invalid session ID", status_code=400)
            return await response(scope, receive, send)

        writer = self._read_stream_writers.get(session_id)
        if not writer:
            response = Response("Could not find session", status_code=404)
            return await response(scope, receive, send)

        body = await request.body()

        try:
            message = types.JSONRPCMessage.model_validate_json(body)
        except ValidationError as err:
            response = Response("Could not parse message", status_code=400)
            await response(scope, receive, send)
            return

        metadata = ServerMessageMetadata(request_context=request)
        session_message = SessionMessage(message, metadata=metadata)
        try:
            await writer.send(session_message)
        except ClosedResourceError:
            self._read_stream_writers.pop(session_id, None)
            response = Response("Session is closed", status_code=410)
            return await response(scope, receive, send)

        response = Response("Accepted", status_code=202)
        await response(scope, receive, send)


def apply_mcp_api_key_middleware(app: ASGIApp) -> ASGIApp:
    async def _auth_middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path in _PUBLIC_HTTP_PATHS:
            await app(scope, receive, send)
            return

        configured = _get_configured_mcp_api_key()
        headers = Headers(scope=scope)
        if not configured:
            if _allow_insecure_local_without_api_key() and _is_direct_loopback_scope(scope):
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
        try:
            await app(scope, receive, send)
        except ClosedResourceError:
            if _should_suppress_closed_resource_error(scope):
                return
            raise
        except RuntimeError as exc:
            if _should_suppress_stream_shutdown_runtime_error(scope, exc):
                return
            raise
        except ValueError as exc:
            if _should_suppress_request_validation_value_error(scope, exc):
                return
            raise

    return _auth_middleware


def create_sse_app() -> ASGIApp:
    transport = MemoryPalaceSseServerTransport(
        mcp._normalize_path(mcp.settings.mount_path, mcp.settings.message_path),
        security_settings=mcp.settings.transport_security,
    )

    async def handle_sse(scope: Scope, receive: Receive, send: Send) -> Response:
        async with transport.connect_sse(scope, receive, send) as streams:
            await mcp._mcp_server.run(
                streams[0],
                streams[1],
                mcp._mcp_server.create_initialization_options(),
            )
        return Response()

    async def sse_endpoint(request: Request) -> Response:
        return await handle_sse(request.scope, request.receive, request._send)  # type: ignore[reportPrivateUsage]

    async def health_endpoint(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "service": "memory-palace-sse"})

    routes = [
        Route("/health", endpoint=health_endpoint, methods=["GET"]),
        Route(mcp.settings.sse_path, endpoint=sse_endpoint, methods=["GET"]),
        Mount(mcp.settings.message_path, app=transport.handle_post_message),
        *mcp._custom_starlette_routes,
    ]
    app = Starlette(debug=mcp.settings.debug, routes=routes)
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
