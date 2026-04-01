"""Tests for self-service token registration endpoints.

POST  /api/v1/register
DELETE /api/v1/register/{squad}
"""

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(tmp_path, tenant_keys=None):
    pytest.importorskip("httpx")
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient
    from open_project_manager_mcp.server import create_server

    server = create_server(
        str(tmp_path / "reg.db"),
        tenant_keys=tenant_keys,
        enable_rest=True,
    )
    app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])
    return TestClient(app, raise_server_exceptions=True)


REG_KEY = "my-super-secret-registration-key"


# ---------------------------------------------------------------------------
# POST /register — feature disabled
# ---------------------------------------------------------------------------

class TestRegisterFeatureDisabled:
    def test_no_registration_key_returns_404(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPM_REGISTRATION_KEY", raising=False)
        client = _make_client(tmp_path)
        r = client.post("/api/v1/register", json={"squad": "alpha", "registration_key": "x"})
        assert r.status_code == 404

    def test_delete_no_registration_key_returns_404(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPM_REGISTRATION_KEY", raising=False)
        client = _make_client(tmp_path)
        r = client.delete("/api/v1/register/alpha", headers={"X-Registration-Key": "x"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Helper — client with OPM_REGISTRATION_KEY set
# ---------------------------------------------------------------------------

@pytest.fixture()
def reg_client(tmp_path):
    """TestClient with OPM_REGISTRATION_KEY set in environment."""
    pytest.importorskip("httpx")
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient
    from open_project_manager_mcp.server import create_server

    server = create_server(str(tmp_path / "reg.db"), enable_rest=True)
    app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])
    with patch.dict("os.environ", {"OPM_REGISTRATION_KEY": REG_KEY}):
        yield TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# POST /register — happy path
# ---------------------------------------------------------------------------

class TestRegisterSuccess:
    def test_returns_201_with_token(self, reg_client):
        r = reg_client.post(
            "/api/v1/register",
            json={"squad": "alpha", "registration_key": REG_KEY},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["squad"] == "alpha"
        assert isinstance(data["token"], str)
        assert len(data["token"]) > 16
        assert "note" in data

    def test_token_not_shown_again_note(self, reg_client):
        r = reg_client.post(
            "/api/v1/register",
            json={"squad": "beta", "registration_key": REG_KEY},
        )
        assert "will not be shown again" in r.json()["note"]


# ---------------------------------------------------------------------------
# POST /register — auth failures
# ---------------------------------------------------------------------------

class TestRegisterAuth:
    def test_wrong_registration_key_returns_401(self, reg_client):
        r = reg_client.post(
            "/api/v1/register",
            json={"squad": "alpha", "registration_key": "wrongkey"},
        )
        assert r.status_code == 401

    def test_missing_registration_key_field_returns_401(self, reg_client):
        r = reg_client.post("/api/v1/register", json={"squad": "alpha"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /register — validation
# ---------------------------------------------------------------------------

class TestRegisterValidation:
    def test_invalid_squad_spaces_returns_400(self, reg_client):
        r = reg_client.post(
            "/api/v1/register",
            json={"squad": "my squad", "registration_key": REG_KEY},
        )
        assert r.status_code == 400

    def test_invalid_squad_empty_returns_400(self, reg_client):
        r = reg_client.post(
            "/api/v1/register",
            json={"squad": "", "registration_key": REG_KEY},
        )
        assert r.status_code == 400

    def test_invalid_squad_too_long_returns_400(self, reg_client):
        r = reg_client.post(
            "/api/v1/register",
            json={"squad": "a" * 65, "registration_key": REG_KEY},
        )
        assert r.status_code == 400

    def test_valid_squad_with_hyphens_and_underscores(self, reg_client):
        r = reg_client.post(
            "/api/v1/register",
            json={"squad": "my-squad_01", "registration_key": REG_KEY},
        )
        assert r.status_code == 201

    def test_missing_squad_field_returns_400(self, reg_client):
        r = reg_client.post(
            "/api/v1/register",
            json={"registration_key": REG_KEY},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /register — conflict
# ---------------------------------------------------------------------------

class TestRegisterConflict:
    def test_duplicate_squad_returns_409(self, reg_client):
        reg_client.post(
            "/api/v1/register",
            json={"squad": "gamma", "registration_key": REG_KEY},
        )
        r = reg_client.post(
            "/api/v1/register",
            json={"squad": "gamma", "registration_key": REG_KEY},
        )
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# POST /register — rate limiting
# ---------------------------------------------------------------------------

class TestRegisterRateLimit:
    def test_five_attempts_allowed(self, reg_client):
        for i in range(5):
            r = reg_client.post(
                "/api/v1/register",
                json={"squad": f"rl{i}", "registration_key": REG_KEY},
            )
            assert r.status_code in (201, 400, 401, 409)

    def test_sixth_attempt_returns_429(self, tmp_path):
        """6 attempts from same IP within 60s → 429 on 6th."""
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        server = create_server(str(tmp_path / "rl.db"), enable_rest=True)
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])

        with patch.dict("os.environ", {"OPM_REGISTRATION_KEY": REG_KEY}):
            client = TestClient(app, raise_server_exceptions=True)
            # First 5 attempts consume rate limit slots
            for i in range(5):
                client.post(
                    "/api/v1/register",
                    json={"squad": f"squad{i}", "registration_key": REG_KEY},
                )
            # 6th attempt from same IP must be rejected
            r = client.post(
                "/api/v1/register",
                json={"squad": "squad5", "registration_key": REG_KEY},
            )
        assert r.status_code == 429


# ---------------------------------------------------------------------------
# DELETE /register/{squad}
# ---------------------------------------------------------------------------

class TestDeregister:
    def _register(self, client, squad):
        return client.post(
            "/api/v1/register",
            json={"squad": squad, "registration_key": REG_KEY},
        )

    def test_wrong_key_returns_401(self, reg_client):
        self._register(reg_client, "del-squad")
        r = reg_client.delete(
            "/api/v1/register/del-squad",
            headers={"X-Registration-Key": "wrongkey"},
        )
        assert r.status_code == 401

    def test_missing_key_returns_401(self, reg_client):
        self._register(reg_client, "del-squad2")
        r = reg_client.delete("/api/v1/register/del-squad2")
        assert r.status_code == 401

    def test_nonexistent_squad_returns_404(self, reg_client):
        r = reg_client.delete(
            "/api/v1/register/ghost-squad",
            headers={"X-Registration-Key": REG_KEY},
        )
        assert r.status_code == 404

    def test_valid_delete_returns_204(self, reg_client):
        self._register(reg_client, "del-ok")
        r = reg_client.delete(
            "/api/v1/register/del-ok",
            headers={"X-Registration-Key": REG_KEY},
        )
        assert r.status_code == 204


# ---------------------------------------------------------------------------
# Token auth integration
# ---------------------------------------------------------------------------

class TestDbTokenAuth:
    def test_registered_token_grants_access(self, tmp_path):
        """Token returned by registration endpoint works for API auth."""
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        server = create_server(str(tmp_path / "tok.db"), enable_rest=True)
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])

        with patch.dict("os.environ", {"OPM_REGISTRATION_KEY": REG_KEY}):
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post(
                "/api/v1/register",
                json={"squad": "tok-squad", "registration_key": REG_KEY},
            )
            assert r.status_code == 201
            token = r.json()["token"]

            r2 = client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})
            assert r2.status_code == 200

    def test_revoked_token_returns_401(self, tmp_path):
        """Token that was registered then deleted no longer works.

        An env var key is used to keep auth active (non-unauthenticated mode)
        after the only DB squad is deleted.
        """
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        # anchor_token keeps auth mode active even after rev-squad DB key is deleted
        anchor_token = "anchor-token-keeps-auth-active-1234"
        server = create_server(
            str(tmp_path / "rev.db"),
            tenant_keys={"anchor": anchor_token},
            enable_rest=True,
        )
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])

        with patch.dict("os.environ", {"OPM_REGISTRATION_KEY": REG_KEY}):
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post(
                "/api/v1/register",
                json={"squad": "rev-squad", "registration_key": REG_KEY},
            )
            token = r.json()["token"]

            client.delete(
                "/api/v1/register/rev-squad",
                headers={"X-Registration-Key": REG_KEY},
            )

            r3 = client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})
            assert r3.status_code == 401

    def test_env_var_key_works_alongside_db_keys(self, tmp_path):
        """Env var key for a squad works even when DB also has registered keys."""
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        env_token = "env-var-token-12345678"
        server = create_server(
            str(tmp_path / "prec.db"),
            tenant_keys={"squad-env": env_token},
            enable_rest=True,
        )
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])

        with patch.dict("os.environ", {"OPM_REGISTRATION_KEY": REG_KEY}):
            client = TestClient(app, raise_server_exceptions=True)
            # Register a different squad via DB
            client.post(
                "/api/v1/register",
                json={"squad": "db-only-squad", "registration_key": REG_KEY},
            )
            # Env var token must still work
            r = client.get(
                "/api/v1/tasks",
                headers={"Authorization": f"Bearer {env_token}"},
            )
            assert r.status_code == 200

    def test_db_key_supplements_env_var_keys(self, tmp_path):
        """Both env var key and DB key are accepted when both exist."""
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        env_token = "env-var-token-abcdefgh"
        server = create_server(
            str(tmp_path / "both.db"),
            tenant_keys={"env-squad": env_token},
            enable_rest=True,
        )
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])

        with patch.dict("os.environ", {"OPM_REGISTRATION_KEY": REG_KEY}):
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post(
                "/api/v1/register",
                json={"squad": "db-squad", "registration_key": REG_KEY},
            )
            assert r.status_code == 201
            db_token = r.json()["token"]

            # Both tokens work
            r_env = client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {env_token}"})
            r_db = client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {db_token}"})
            assert r_env.status_code == 200
            assert r_db.status_code == 200

    def test_auth_required_when_only_registration_key_set(self, tmp_path):
        """OPM_REGISTRATION_KEY set + empty DB + no env keys → auth required (not unauthenticated)."""
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        # No tenant_keys env var, no DB rows — but OPM_REGISTRATION_KEY IS set
        server = create_server(str(tmp_path / "regauth.db"), enable_rest=True)
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])
        with patch.dict("os.environ", {"OPM_REGISTRATION_KEY": REG_KEY}):
            client = TestClient(app, raise_server_exceptions=True)
            r = client.get("/api/v1/tasks")
        assert r.status_code == 401, (
            "Server must require auth when OPM_REGISTRATION_KEY is set, "
            "even with an empty tenant_keys table and no OPM_TENANT_KEYS"
        )

    def test_no_auth_when_db_empty_and_no_env_keys(self, tmp_path, monkeypatch):
        """Unauthenticated mode: no env keys AND empty DB → requests allowed."""
        monkeypatch.delenv("OPM_REGISTRATION_KEY", raising=False)
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        server = create_server(str(tmp_path / "noauth.db"), enable_rest=True)
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])
        client = TestClient(app, raise_server_exceptions=True)
        r = client.get("/api/v1/tasks")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# OPM_REGISTRATION_KEY length warning
# ---------------------------------------------------------------------------

class TestRegistrationKeyWarning:
    def _run_main_stdio(self, tmp_path, extra_env=None):
        """Run main() in stdio mode (no transport flags) with asyncio.run mocked."""
        import open_project_manager_mcp.server as _server_mod
        from open_project_manager_mcp.__main__ import main

        mock_server = MagicMock()
        env = {"OPM_DB_PATH": str(tmp_path / "warn.db")}
        if extra_env:
            env.update(extra_env)

        with patch.dict("os.environ", env), \
             patch("sys.argv", ["opm"]), \
             patch.object(_server_mod, "create_server", return_value=mock_server), \
             patch("asyncio.run"):
            main()

    def test_short_key_warns_to_stderr(self, tmp_path, capsys):
        self._run_main_stdio(tmp_path, {"OPM_REGISTRATION_KEY": "tooshort"})
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "16" in captured.err

    def test_long_key_no_warning(self, tmp_path, capsys):
        self._run_main_stdio(tmp_path, {"OPM_REGISTRATION_KEY": "a" * 16})
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    def test_no_key_no_warning(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("OPM_REGISTRATION_KEY", raising=False)
        import open_project_manager_mcp.server as _server_mod
        from open_project_manager_mcp.__main__ import main

        mock_server = MagicMock()
        with patch("sys.argv", ["opm"]), \
             patch.dict("os.environ", {"OPM_DB_PATH": str(tmp_path / "nokey.db")}), \
             patch.object(_server_mod, "create_server", return_value=mock_server), \
             patch("asyncio.run"):
            main()

        captured = capsys.readouterr()
        assert "WARNING" not in captured.err
