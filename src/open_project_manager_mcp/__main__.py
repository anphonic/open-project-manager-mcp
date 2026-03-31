"""CLI entry point for open-project-manager-mcp."""

import argparse
import asyncio
import json
import os
import sys

import platformdirs


class _FixArgumentsMiddleware:
    """Coerce tools/call arguments from [] to {} for non-compliant MCP clients.

    Some clients send arguments: [] (empty list) instead of arguments: {}
    (empty dict) when calling a tool with no parameters. FastMCP's pydantic
    validation rejects a list, causing the call to fail silently.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, original_send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, original_send)
            return

        chunks = []
        more_body = True
        while more_body:
            message = await receive()
            chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body = b"".join(chunks)

        patched = body
        try:
            data = json.loads(body)
            if (
                isinstance(data, dict)
                and data.get("method") == "tools/call"
                and isinstance(data.get("params"), dict)
                and isinstance(data["params"].get("arguments"), list)
                and len(data["params"]["arguments"]) == 0
            ):
                data["params"]["arguments"] = {}
                patched = json.dumps(data).encode()
        except (json.JSONDecodeError, KeyError):
            pass

        body_iter = iter([patched])

        async def patched_receive():
            try:
                chunk = next(body_iter)
                return {"type": "http.request", "body": chunk, "more_body": False}
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, patched_receive, original_send)


def _check_network_auth(host: str, port: int, allow_unauth: bool, transport_name: str) -> None:
    """Warn or refuse to bind a network transport to non-localhost without acknowledgement."""
    localhost_addrs = ("127.0.0.1", "localhost", "::1")
    if host not in localhost_addrs and not allow_unauth:
        print(
            f"FATAL: Refusing to bind to {host}:{port} in {transport_name} mode without "
            "--allow-unauthenticated-network. This server has no authentication in v1; "
            "pass the flag to confirm you accept the risk (development/LAN only).",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"Notice: {transport_name} mode active — server is unauthenticated. "
        "Any process that can reach this endpoint can read/write tasks.",
        file=sys.stderr,
    )


def main():
    if sys.platform == "win32":
        default_db_dir = platformdirs.user_data_dir("open-project-manager-mcp", appauthor=False)
    else:
        default_db_dir = platformdirs.user_data_dir(
            "open-project-manager-mcp", "open-project-manager-mcp"
        )
    default_db = os.path.join(default_db_dir, "tasks.db")

    parser = argparse.ArgumentParser(description="Open Project Manager MCP Server")
    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database (env: OPM_DB_PATH)",
    )
    parser.add_argument(
        "--host",
        type=str,
        help="Host to bind for network transports (env: OPM_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port for network transports (env: OPM_PORT, default: 8765)",
    )

    transport_group = parser.add_mutually_exclusive_group()
    transport_group.add_argument(
        "--http",
        action="store_true",
        help="HTTP (streamable-http) transport — multi-client",
    )
    transport_group.add_argument(
        "--sse",
        action="store_true",
        help="SSE (Server-Sent Events) transport — older MCP protocol",
    )

    parser.add_argument(
        "--allow-unauthenticated-network",
        action="store_true",
        dest="allow_unauthenticated_network",
        help="Allow HTTP/SSE on non-localhost without auth (development/LAN only)",
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        help="Max concurrent connections for HTTP/SSE mode (env: OPM_MAX_CONNECTIONS, default: 100)",
    )

    args = parser.parse_args()

    raw_db_path = args.db_path or os.environ.get("OPM_DB_PATH") or default_db
    db_path = os.path.abspath(raw_db_path)

    if not os.path.isabs(raw_db_path):
        print(
            f'Warning: relative db path "{raw_db_path}" resolved to "{db_path}"',
            file=sys.stderr,
        )

    host = args.host or os.environ.get("OPM_HOST", "127.0.0.1")
    port = int(args.port or os.environ.get("OPM_PORT", "8765"))
    max_connections = int(args.max_connections or os.environ.get("OPM_MAX_CONNECTIONS", "100"))
    if max_connections < 1:
        print(
            f"FATAL: --max-connections must be at least 1 (got {max_connections})",
            file=sys.stderr,
        )
        sys.exit(1)

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    print(f"Database path: {db_path}", file=sys.stderr)

    from .server import create_server

    # Resolve transport security before creating the server so FastMCP can be
    # configured correctly.  When binding to a non-localhost interface, disable
    # DNS rebinding protection so LAN clients whose Host header differs from
    # 127.0.0.1 are not rejected by FastMCP.
    _transport_security = None
    localhost_addrs = ("127.0.0.1", "localhost", "::1")
    if (args.http or args.sse) and host not in localhost_addrs:
        from mcp.server.transport_security import TransportSecuritySettings
        _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    mcp = create_server(db_path, transport_security=_transport_security)

    if args.http or args.sse:
        _check_network_auth(host, port, args.allow_unauthenticated_network, "HTTP" if args.http else "SSE")
        try:
            import uvicorn
            from starlette.applications import Starlette
            from starlette.routing import Mount
        except ImportError as exc:
            print(
                f"Error: HTTP/SSE transport requires uvicorn and starlette: {exc}\n"
                "Install with: pip install 'open-project-manager-mcp[http]'",
                file=sys.stderr,
            )
            sys.exit(1)

        def _make_lifespan(inner_app):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _lifespan(app):
                async with inner_app.router.lifespan_context(inner_app):
                    yield

            return _lifespan

        if args.http:
            print(f"Starting open-project-manager-mcp in HTTP mode on {host}:{port}", file=sys.stderr)
            mcp_asgi = mcp.streamable_http_app()
            app = Starlette(
                routes=[Mount("/", mcp_asgi)],
                lifespan=_make_lifespan(mcp_asgi),
            )
            app = _FixArgumentsMiddleware(app)
        else:
            print(
                f"Starting open-project-manager-mcp in SSE mode on {host}:{port}\n"
                f"  SSE endpoint:     http://{host}:{port}/sse\n"
                f"  Message endpoint: http://{host}:{port}/messages/",
                file=sys.stderr,
            )
            mcp_sse_asgi = mcp.sse_app()
            app = Starlette(
                routes=[Mount("/", mcp_sse_asgi)],
                lifespan=_make_lifespan(mcp_sse_asgi),
            )
            app = _FixArgumentsMiddleware(app)

        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            timeout_keep_alive=30,
            limit_concurrency=max_connections,
            limit_max_requests=10000,
            timeout_graceful_shutdown=30,
        )
    else:
        asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
