"""CLI entry point for open-project-manager-mcp."""

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
import time

import platformdirs


_MAX_MCP_BODY = 6_000_000  # 6 MB — accommodates the 5 MB import_tasks payload plus overhead


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
        total_size = 0
        more_body = True
        while more_body:
            message = await receive()
            chunk = message.get("body", b"")
            total_size += len(chunk)
            if total_size > _MAX_MCP_BODY:
                await original_send({
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [(b"content-type", b"application/json")],
                })
                await original_send({
                    "type": "http.response.body",
                    "body": b'{"error":"Request body too large"}',
                    "more_body": False,
                })
                return
            chunks.append(chunk)
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


class SessionActivityTracker:
    """Track last activity time for each session."""
    
    def __init__(self, session_timeout: int = 120):
        self.session_timeout = session_timeout
        self._sessions: dict[str, float] = {}  # session_id -> last_activity_time
    
    def touch(self, session_id: str) -> None:
        """Update last activity time for session."""
        self._sessions[session_id] = time.monotonic()
    
    def remove(self, session_id: str) -> None:
        """Remove session from tracking."""
        self._sessions.pop(session_id, None)
    
    def get_stale_sessions(self) -> list[str]:
        """Return session IDs that have exceeded timeout."""
        now = time.monotonic()
        return [
            sid for sid, last_active in self._sessions.items()
            if (now - last_active) > self.session_timeout
        ]


class SessionActivityMiddleware:
    """Track session activity on every HTTP request."""
    
    def __init__(self, app, tracker: SessionActivityTracker):
        self.app = app
        self.tracker = tracker
    
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Extract session ID from headers
            headers = dict(scope.get("headers", []))
            session_id = headers.get(b"mcp-session-id", b"").decode("utf-8", errors="ignore")
            
            if session_id:
                self.tracker.touch(session_id)
        
        await self.app(scope, receive, send)


class ConnectionTimeoutMiddleware:
    """Kill connections that have been open longer than max_connection_age seconds.
    
    This middleware prevents event loop saturation from long-lived SSE connections
    by forcibly closing connections that exceed a configurable age threshold.
    """

    def __init__(self, app, max_connection_age: int = 60):
        self.app = app
        self.max_connection_age = max_connection_age

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        start_time = time.monotonic()
        response_started = False
        
        async def timeout_aware_receive():
            elapsed = time.monotonic() - start_time
            if elapsed > self.max_connection_age:
                import logging
                logging.warning(
                    f"[ConnectionTimeoutMiddleware] Killed connection after {elapsed:.1f}s"
                )
                return {"type": "http.disconnect"}
            return await receive()
        
        async def timeout_aware_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
        
        try:
            await self.app(scope, timeout_aware_receive, timeout_aware_send)
        except Exception:
            elapsed = time.monotonic() - start_time
            if elapsed > self.max_connection_age and not response_started:
                await send({
                    "type": "http.response.start",
                    "status": 408,
                    "headers": [(b"content-type", b"text/plain")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b"Connection timeout",
                })
            else:
                raise


def _load_tenant_keys() -> dict[str, dict[str, object]] | None:
    """Load tenant API keys from OPM_TENANT_KEYS env var.

    Supports two formats (may be mixed):
      - Old: {"squadname": "bearer-token"}
      - New: {"squadname": {"key": "bearer-token"}}

    Returns a normalized dict-of-dicts:
      {"squadname": {"key": "bearer-token"}}
    """
    raw = os.environ.get("OPM_TENANT_KEYS")
    if not raw:
        return None
    try:
        keys = json.loads(raw)
        if not isinstance(keys, dict):
            raise ValueError("OPM_TENANT_KEYS must be a JSON object")
        normalized: dict[str, dict[str, object]] = {}
        for tenant_id, value in keys.items():
            if not isinstance(tenant_id, str) or not tenant_id:
                raise ValueError(f"Tenant ID must be a non-empty string, got: {tenant_id!r}")
            if isinstance(value, str):
                if not value:
                    raise ValueError(f"API key for tenant '{tenant_id}' must be a non-empty string")
                normalized[tenant_id] = {"key": value}
            elif isinstance(value, dict):
                key = value.get("key")
                if not isinstance(key, str) or not key:
                    raise ValueError(f"Tenant '{tenant_id}' must have a non-empty string 'key' field")
                normalized[tenant_id] = {"key": key}
            else:
                raise ValueError(f"Tenant '{tenant_id}' value must be a string or object")
        return normalized
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"FATAL: Invalid OPM_TENANT_KEYS: {exc}", file=sys.stderr)
        sys.exit(1)


def _check_network_auth(
    host: str, port: int, tenant_keys: dict | None, allow_unauth: bool, transport_name: str
) -> None:
    """Refuse to bind a network transport to non-localhost without auth."""
    localhost_addrs = ("127.0.0.1", "localhost", "::1")
    if not tenant_keys:
        if host not in localhost_addrs and not allow_unauth:
            print(
                f"FATAL: Refusing to bind to {host}:{port} in {transport_name} mode without authentication. "
                "Set OPM_TENANT_KEYS or pass --allow-unauthenticated-network to override (development only).",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            print(
                f"Notice: {transport_name} mode active without OPM_TENANT_KEYS — server is unauthenticated. "
                "Any process that can reach this endpoint can read/write tasks.",
                file=sys.stderr,
            )
    elif host not in localhost_addrs:
        print(
            f"WARNING: {transport_name} mode on {host}:{port} with bearer token auth. "
            "Tokens will be transmitted in plaintext — use a TLS-terminating reverse proxy in production.",
            file=sys.stderr,
        )


async def session_reaper(
    session_manager,
    tracker: SessionActivityTracker,
    check_interval: int = 30,
):
    """Background task that terminates stale sessions."""
    logger = logging.getLogger("opm.session_reaper")
    
    while True:
        await asyncio.sleep(check_interval)
        
        stale = tracker.get_stale_sessions()
        if not stale:
            continue
        
        logger.info(f"[SessionReaper] Found {len(stale)} stale sessions")
        
        for session_id in stale:
            # Access the internal session transport
            transport = session_manager._server_instances.get(session_id)
            if transport:
                try:
                    await transport.terminate()
                    logger.info(f"[SessionReaper] Terminated session {session_id}")
                except Exception as e:
                    logger.warning(f"[SessionReaper] Failed to terminate {session_id}: {e}")
            
            # Remove from tracker regardless
            tracker.remove(session_id)
            
            # Also clean up _server_instances dict
            session_manager._server_instances.pop(session_id, None)


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
    parser.add_argument(
        "--rest-api",
        action="store_true",
        dest="rest_api",
        help="Mount REST API at /api/v1 (HTTP mode only). Requires --http.",
    )
    parser.add_argument(
        "--connection-timeout",
        type=int,
        help="Max connection age in seconds before forced disconnect (env: OPM_CONNECTION_TIMEOUT, default: 60)",
    )
    parser.add_argument(
        "--generate-token",
        type=str,
        metavar="SQUAD_NAME",
        help="Generate a bearer token for the named squad and print OPM_TENANT_KEYS instructions, then exit",
    )
    parser.add_argument(
        "--session-timeout",
        type=int,
        help="Session inactivity timeout in seconds (env: OPM_SESSION_TIMEOUT, default: 120)",
    )

    args = parser.parse_args()

    # Handle --generate-token before doing anything else
    if args.generate_token:
        squad = args.generate_token
        token = secrets.token_urlsafe(32)
        existing_hint = json.dumps({squad: {"key": token}})
        print(f"Generated bearer token for squad '{squad}':")
        print(f"  Token: {token}")
        print()
        print("Add to OPM_TENANT_KEYS (merge with any existing entries):")
        print(f"  OPM_TENANT_KEYS='{existing_hint}'")
        print()
        print("Restart open-project-manager-mcp with --http to enable bearer token auth.")
        return

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
    
    connection_timeout = int(
        args.connection_timeout or os.environ.get("OPM_CONNECTION_TIMEOUT", "60")
    )
    if connection_timeout < 5:
        print(
            f"FATAL: --connection-timeout must be at least 5 seconds (got {connection_timeout})",
            file=sys.stderr,
        )
        sys.exit(1)
    
    session_timeout = int(
        args.session_timeout or os.environ.get("OPM_SESSION_TIMEOUT", "120")
    )
    if session_timeout < 10:
        print(
            f"FATAL: --session-timeout must be at least 10 seconds (got {session_timeout})",
            file=sys.stderr,
        )
        sys.exit(1)

    tenant_keys = _load_tenant_keys()
    flat_keys = {t: v["key"] for t, v in tenant_keys.items()} if tenant_keys else None

    _reg_key = os.environ.get("OPM_REGISTRATION_KEY")
    if _reg_key is not None and len(_reg_key) < 16:
        print(
            "WARNING: OPM_REGISTRATION_KEY is shorter than 16 characters — "
            "use a longer key in production.",
            file=sys.stderr,
        )

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

    server_url = f"http://{host}:{port}"
    mcp = create_server(db_path, tenant_keys=flat_keys, server_url=server_url, transport_security=_transport_security, enable_rest=bool(args.rest_api and args.http))

    if args.http or args.sse:
        _check_network_auth(host, port, flat_keys, args.allow_unauthenticated_network, "HTTP" if args.http else "SSE")
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

        def _make_lifespan(inner_app, session_manager=None, tracker=None):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _lifespan(app):
                async with inner_app.router.lifespan_context(inner_app):
                    # Start session reaper task if in HTTP mode with session tracking
                    reaper_task = None
                    if session_manager is not None and tracker is not None:
                        reaper_task = asyncio.create_task(
                            session_reaper(session_manager, tracker, check_interval=30)
                        )
                        logging.getLogger("opm.session_reaper").info(
                            f"[SessionReaper] Started with {tracker.session_timeout}s timeout"
                        )
                    try:
                        yield
                    finally:
                        if reaper_task is not None:
                            reaper_task.cancel()
                            try:
                                await reaper_task
                            except asyncio.CancelledError:
                                pass

            return _lifespan

        if args.http:
            print(f"Starting open-project-manager-mcp in HTTP mode on {host}:{port}", file=sys.stderr)
            mcp_asgi = mcp.streamable_http_app()
            
            # Get session manager from mcp for session reaper
            session_manager = mcp._session_manager
            tracker = SessionActivityTracker(session_timeout=session_timeout)
            
            if args.rest_api and hasattr(mcp, "_rest_router"):
                print("  REST API mounted at /api/v1", file=sys.stderr)
                app = Starlette(
                    routes=[Mount("/api/v1", app=mcp._rest_router), Mount("/", mcp_asgi)],
                    lifespan=_make_lifespan(mcp_asgi, session_manager, tracker),
                )
            else:
                app = Starlette(
                    routes=[Mount("/", mcp_asgi)],
                    lifespan=_make_lifespan(mcp_asgi, session_manager, tracker),
                )
            
            # Middleware order (outermost first):
            # 1. ConnectionTimeoutMiddleware (kill long connections)
            # 2. SessionActivityMiddleware (track activity)
            # 3. _FixArgumentsMiddleware (patch empty args)
            # 4. Starlette app
            app = _FixArgumentsMiddleware(app)
            app = SessionActivityMiddleware(app, tracker)
            app = ConnectionTimeoutMiddleware(app, max_connection_age=connection_timeout)
        else:
            print(
                f"Starting open-project-manager-mcp in SSE mode on {host}:{port}\n"
                f"  SSE endpoint:     http://{host}:{port}/sse\n"
                f"  Message endpoint: http://{host}:{port}/messages/",
                file=sys.stderr,
            )
            mcp_sse_asgi = mcp.sse_app()
            if args.rest_api and hasattr(mcp, "_rest_router"):
                print("  REST API mounted at /api/v1", file=sys.stderr)
                app = Starlette(
                    routes=[Mount("/api/v1", app=mcp._rest_router), Mount("/", mcp_sse_asgi)],
                    lifespan=_make_lifespan(mcp_sse_asgi),
                )
            else:
                app = Starlette(
                    routes=[Mount("/", mcp_sse_asgi)],
                    lifespan=_make_lifespan(mcp_sse_asgi),
                )
            app = _FixArgumentsMiddleware(app)
            app = ConnectionTimeoutMiddleware(app, max_connection_age=connection_timeout)

        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            timeout_keep_alive=5,
            limit_concurrency=max_connections,
            limit_max_requests=1000,
            timeout_graceful_shutdown=10,
        )
    else:
        asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
