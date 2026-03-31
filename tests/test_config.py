"""Tests for CLI argument parsing and transport config in __main__.py."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_project_manager_mcp.__main__ import (
    _FixArgumentsMiddleware,
    _check_network_auth,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_main(argv, env_overrides=None):
    """Run main() with given argv and optional env overrides.

    Mocks create_server, uvicorn, and filesystem so no server actually starts.
    Returns (mock_mcp, captured_db_path, mock_uvicorn_module).
    """
    mock_mcp = MagicMock()
    mock_mcp.run_stdio_async = AsyncMock()
    mock_uvicorn = MagicMock()
    captured_db_path = []

    def fake_create_server(db_path, **kwargs):
        captured_db_path.append(db_path)
        return mock_mcp

    env = dict(os.environ)
    for key in ["OPM_DB_PATH", "OPM_HOST", "OPM_PORT", "OPM_MAX_CONNECTIONS"]:
        env.pop(key, None)
    if env_overrides:
        env.update(env_overrides)

    with (
        patch.object(sys, "argv", argv),
        patch.dict(os.environ, env, clear=True),
        patch("open_project_manager_mcp.server.create_server", fake_create_server),
        patch.dict(sys.modules, {"uvicorn": mock_uvicorn}),
        patch("os.makedirs"),
        patch("os.path.exists", return_value=False),
    ):
        main()

    return mock_mcp, captured_db_path, mock_uvicorn


# ---------------------------------------------------------------------------
# stdio (default transport)
# ---------------------------------------------------------------------------

class TestStdioTransport:
    def test_stdio_is_default(self):
        mock_mcp, _, _ = _run_main(["open-project-manager-mcp"])
        mock_mcp.run_stdio_async.assert_called_once()

    def test_db_path_from_env(self):
        _, captured, _ = _run_main(
            ["open-project-manager-mcp"],
            env_overrides={"OPM_DB_PATH": "/custom/tasks.db"},
        )
        assert captured[0] == os.path.abspath("/custom/tasks.db")

    def test_db_path_from_cli_flag(self):
        _, captured, _ = _run_main(
            ["open-project-manager-mcp", "--db-path", "/cli/tasks.db"],
        )
        assert captured[0] == os.path.abspath("/cli/tasks.db")

    def test_cli_overrides_env(self):
        _, captured, _ = _run_main(
            ["open-project-manager-mcp", "--db-path", "/cli/tasks.db"],
            env_overrides={"OPM_DB_PATH": "/env/tasks.db"},
        )
        assert captured[0] == os.path.abspath("/cli/tasks.db")


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------

class TestHttpTransport:
    def test_http_mode_calls_uvicorn_run(self):
        _, _, mock_uvicorn = _run_main(
            ["open-project-manager-mcp", "--http", "--allow-unauthenticated-network"]
        )
        assert mock_uvicorn.run.called

    def test_http_mode_uses_specified_port(self):
        _, _, mock_uvicorn = _run_main(
            ["open-project-manager-mcp", "--http", "--port", "9090", "--allow-unauthenticated-network"]
        )
        call_kwargs = mock_uvicorn.run.call_args.kwargs
        assert call_kwargs["port"] == 9090

    def test_http_mode_does_not_call_run_stdio_async(self):
        mock_mcp, _, _ = _run_main(
            ["open-project-manager-mcp", "--http", "--allow-unauthenticated-network"]
        )
        mock_mcp.run_stdio_async.assert_not_called()


# ---------------------------------------------------------------------------
# SSE transport
# ---------------------------------------------------------------------------

class TestSseTransport:
    def test_sse_mode_calls_uvicorn_run(self):
        _, _, mock_uvicorn = _run_main(
            ["open-project-manager-mcp", "--sse", "--allow-unauthenticated-network"]
        )
        assert mock_uvicorn.run.called

    def test_sse_mode_uses_specified_port(self):
        _, _, mock_uvicorn = _run_main(
            ["open-project-manager-mcp", "--sse", "--port", "9091", "--allow-unauthenticated-network"]
        )
        call_kwargs = mock_uvicorn.run.call_args.kwargs
        assert call_kwargs["port"] == 9091

    def test_sse_mode_does_not_call_run_stdio_async(self):
        mock_mcp, _, _ = _run_main(
            ["open-project-manager-mcp", "--sse", "--allow-unauthenticated-network"]
        )
        mock_mcp.run_stdio_async.assert_not_called()


# ---------------------------------------------------------------------------
# _check_network_auth
# ---------------------------------------------------------------------------

class TestCheckNetworkAuth:
    def test_non_localhost_without_flag_exits(self):
        with pytest.raises(SystemExit):
            _check_network_auth("192.168.1.1", 8765, allow_unauth=False, transport_name="HTTP")

    def test_non_localhost_with_flag_does_not_exit(self):
        # Should not raise
        _check_network_auth("192.168.1.1", 8765, allow_unauth=True, transport_name="HTTP")

    def test_localhost_does_not_exit(self):
        # Should not raise even without flag
        _check_network_auth("127.0.0.1", 8765, allow_unauth=False, transport_name="HTTP")

    def test_localhost_ipv6_does_not_exit(self):
        _check_network_auth("::1", 8765, allow_unauth=False, transport_name="HTTP")


# ---------------------------------------------------------------------------
# _FixArgumentsMiddleware
# ---------------------------------------------------------------------------

class TestFixArgumentsMiddleware:
    def _make_receive(self, body: bytes):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return receive

    def _run(self, middleware, scope, body: bytes) -> bytes:
        captured = []

        async def inner_app(scope, receive, send):
            msg = await receive()
            captured.append(msg["body"])

        asyncio.run(middleware(scope, self._make_receive(body), lambda _: None))
        return captured[0] if captured else b""

    def test_coerces_empty_list_to_empty_dict(self):
        body = json.dumps({
            "method": "tools/call",
            "params": {"name": "get_stats", "arguments": []},
        }).encode()

        captured = []

        async def inner(scope, receive, send):
            captured.append((await receive())["body"])

        asyncio.run(_FixArgumentsMiddleware(inner)(
            {"type": "http", "method": "POST"},
            self._make_receive(body),
            lambda _: None,
        ))

        patched = json.loads(captured[0])
        assert patched["params"]["arguments"] == {}

    def test_non_empty_dict_is_untouched(self):
        captured = []

        async def inner(scope, receive, send):
            captured.append((await receive())["body"])

        body = json.dumps({
            "method": "tools/call",
            "params": {"name": "create_task", "arguments": {"id": "x", "title": "y"}},
        }).encode()

        asyncio.run(_FixArgumentsMiddleware(inner)(
            {"type": "http", "method": "POST"},
            self._make_receive(body),
            lambda _: None,
        ))
        patched = json.loads(captured[0])
        assert patched["params"]["arguments"] == {"id": "x", "title": "y"}

    def test_non_http_scope_passes_through_unchanged(self):
        called = []

        async def inner(scope, receive, send):
            called.append(True)

        asyncio.run(_FixArgumentsMiddleware(inner)(
            {"type": "websocket"},
            self._make_receive(b""),
            lambda _: None,
        ))
        assert called == [True]

    def test_non_post_method_passes_through_unchanged(self):
        called = []

        async def inner(scope, receive, send):
            called.append(True)

        asyncio.run(_FixArgumentsMiddleware(inner)(
            {"type": "http", "method": "GET"},
            self._make_receive(b"{}"),
            lambda _: None,
        ))
        assert called == [True]

    def test_malformed_json_passes_through_unchanged(self):
        captured = []

        async def inner(scope, receive, send):
            captured.append((await receive())["body"])

        body = b"not-json"
        asyncio.run(_FixArgumentsMiddleware(inner)(
            {"type": "http", "method": "POST"},
            self._make_receive(body),
            lambda _: None,
        ))
        assert captured[0] == body
