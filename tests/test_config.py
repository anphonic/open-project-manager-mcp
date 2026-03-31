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
    _load_tenant_keys,
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
    for key in ["OPM_DB_PATH", "OPM_HOST", "OPM_PORT", "OPM_MAX_CONNECTIONS", "OPM_TENANT_KEYS"]:
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
            _check_network_auth("192.168.1.1", 8765, tenant_keys=None, allow_unauth=False, transport_name="HTTP")

    def test_non_localhost_with_flag_does_not_exit(self):
        # Should not raise
        _check_network_auth("192.168.1.1", 8765, tenant_keys=None, allow_unauth=True, transport_name="HTTP")

    def test_localhost_does_not_exit(self):
        # Should not raise even without flag
        _check_network_auth("127.0.0.1", 8765, tenant_keys=None, allow_unauth=False, transport_name="HTTP")

    def test_localhost_ipv6_does_not_exit(self):
        _check_network_auth("::1", 8765, tenant_keys=None, allow_unauth=False, transport_name="HTTP")

    def test_non_localhost_with_tenant_keys_does_not_exit(self):
        # Auth configured — should warn but not exit
        _check_network_auth("192.168.1.1", 8765, tenant_keys={"squad": "token"}, allow_unauth=False, transport_name="HTTP")

    def test_localhost_with_tenant_keys_does_not_exit(self):
        _check_network_auth("127.0.0.1", 8765, tenant_keys={"squad": "token"}, allow_unauth=False, transport_name="HTTP")


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


# ---------------------------------------------------------------------------
# _load_tenant_keys
# ---------------------------------------------------------------------------

class TestLoadTenantKeys:
    def test_returns_none_when_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _load_tenant_keys() is None

    def test_old_format_string_value(self):
        raw = json.dumps({"westworld": "mytoken"})
        with patch.dict(os.environ, {"OPM_TENANT_KEYS": raw}, clear=True):
            result = _load_tenant_keys()
        assert result == {"westworld": {"key": "mytoken"}}

    def test_new_format_dict_value(self):
        raw = json.dumps({"westworld": {"key": "mytoken"}})
        with patch.dict(os.environ, {"OPM_TENANT_KEYS": raw}, clear=True):
            result = _load_tenant_keys()
        assert result == {"westworld": {"key": "mytoken"}}

    def test_invalid_json_exits(self):
        with patch.dict(os.environ, {"OPM_TENANT_KEYS": "not-json"}, clear=True):
            with pytest.raises(SystemExit):
                _load_tenant_keys()

    def test_empty_key_exits(self):
        raw = json.dumps({"westworld": ""})
        with patch.dict(os.environ, {"OPM_TENANT_KEYS": raw}, clear=True):
            with pytest.raises(SystemExit):
                _load_tenant_keys()


# ---------------------------------------------------------------------------
# --generate-token
# ---------------------------------------------------------------------------

class TestGenerateToken:
    def test_generate_token_exits_zero(self, capsys):
        with patch.object(sys, "argv", ["open-project-manager-mcp", "--generate-token", "westworld"]):
            with patch.dict(os.environ, {}, clear=True):
                main()  # should return without sys.exit
        out = capsys.readouterr().out
        assert "westworld" in out

    def test_generate_token_prints_token(self, capsys):
        with patch.object(sys, "argv", ["open-project-manager-mcp", "--generate-token", "westworld"]):
            with patch.dict(os.environ, {}, clear=True):
                main()
        out = capsys.readouterr().out
        assert "Token:" in out
        assert "OPM_TENANT_KEYS" in out

    def test_generate_token_is_url_safe(self, capsys):
        import re
        with patch.object(sys, "argv", ["open-project-manager-mcp", "--generate-token", "westworld"]):
            with patch.dict(os.environ, {}, clear=True):
                main()
        out = capsys.readouterr().out
        # Extract the token line
        for line in out.splitlines():
            if "Token:" in line:
                token = line.split("Token:")[-1].strip()
                # URL-safe base64 characters only (letters, digits, - and _)
                assert re.match(r'^[A-Za-z0-9\-_]+$', token), f"Token not URL-safe: {token}"
                assert len(token) >= 40, f"Token too short: {token}"
                break
        else:
            pytest.fail("No token line found in output")

    def test_generate_token_does_not_start_server(self, capsys):
        """--generate-token must exit before touching db or server."""
        with patch.object(sys, "argv", ["open-project-manager-mcp", "--generate-token", "westworld"]):
            with patch.dict(os.environ, {}, clear=True):
                with patch("open_project_manager_mcp.__main__._load_tenant_keys") as mock_load:
                    main()
                    # _load_tenant_keys should NOT be called — we return before it
                    mock_load.assert_not_called()

