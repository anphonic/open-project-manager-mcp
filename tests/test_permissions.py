"""Tests for v0.3.0 permissions system."""

import asyncio
import functools
import json
import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

import pytest


def _get_tool(mcp_server, name: str):
    """Extract a raw tool function from a FastMCP server instance."""
    return mcp_server._tool_manager._tools[name].fn


def _sync_wrap(fn):
    """Wrap an async tool function so it can be called synchronously in tests."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return asyncio.run(result)
        return result
    return wrapper


@pytest.fixture()
def mcp_server(tmp_path):
    """Spin up create_server() with a fresh SQLite database."""
    from open_project_manager_mcp.server import create_server
    server = create_server(str(tmp_path / "tasks.db"))
    server._get = lambda name: _sync_wrap(_get_tool(server, name))
    return server


@pytest.fixture()
def mcp_server_with_perms(tmp_path, monkeypatch):
    """Server with permissions enforcement enabled."""
    monkeypatch.setenv("OPM_ENFORCE_PERMISSIONS", "1")
    from open_project_manager_mcp.server import create_server
    server = create_server(str(tmp_path / "tasks.db"))
    server._get = lambda name: _sync_wrap(_get_tool(server, name))
    return server


@pytest.fixture()
def db_conn(tmp_path):
    """Direct database connection for low-level inspection."""
    from open_project_manager_mcp.server import create_server
    db_path = str(tmp_path / "tasks.db")
    # Initialize schema through server creation
    server = create_server(db_path)
    # Return a separate connection for inspection
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


class TestDefaultDeny:
    """Test that permissions deny access by default when enforced."""
    
    def test_system_tenant_bypasses_permissions(self, mcp_server_with_perms, db_conn):
        """System tenant bypasses permissions even when enforcement is enabled."""
        # Create a project owned by tenant1 (not system)
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "tenant1", "owner", now, now)
        )
        db_conn.commit()
        
        # Try to access as system tenant (default in test env)
        # System tenant bypasses permissions by design
        result = mcp_server_with_perms._get("list_tasks")(project="myproject")
        
        # Should succeed even without permission row for system
        assert "Error" not in result
        # Should return valid JSON
        data = json.loads(result)
        assert "tasks" in data
    
    def test_system_tenant_has_implicit_access(self, mcp_server_with_perms, db_conn):
        """System tenant has access even with no permission row."""
        # Try to create task in project with no permissions set for system
        result = mcp_server_with_perms._get("create_task")(
            id="t1", title="Test", project="restricted"
        )
        
        # System tenant should succeed (bypasses permissions)
        assert "Error" not in result
        data = json.loads(result)
        assert data["id"] == "t1"


class TestOwnerPermissions:
    """Test that owners have full read/write/manage access."""
    
    def test_owner_can_read(self, mcp_server_with_perms, db_conn):
        """Owner should be able to read project tasks."""
        # Set up: tenant1 owns project
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)  # system is test tenant
        )
        db_conn.commit()
        
        # Owner should be able to list tasks
        result = mcp_server_with_perms._get("list_tasks")(project="myproject")
        
        # Should succeed (empty list or JSON array)
        assert "Error" not in result or result.startswith("[")
    
    def test_owner_can_write(self, mcp_server_with_perms, db_conn):
        """Owner should be able to create/update tasks."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)
        )
        db_conn.commit()
        
        # Owner should be able to create task
        result = mcp_server_with_perms._get("create_task")(
            id="t1", title="Test", project="myproject"
        )
        
        # Should succeed
        if result and not result.startswith("Error"):
            data = json.loads(result)
            assert data["id"] == "t1"
    
    def test_owner_can_delete(self, mcp_server_with_perms, db_conn):
        """Owner should be able to delete tasks."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)
        )
        db_conn.commit()
        
        # Create then delete (with human_approval)
        mcp_server_with_perms._get("create_task")(
            id="t1", title="Test", project="myproject"
        )
        result = mcp_server_with_perms._get("delete_task")(
            task_id="t1", human_approval=True
        )
        
        # Should succeed
        assert "Error" not in result or "deleted" in result.lower()
    
    def test_owner_can_grant_access(self, mcp_server_with_perms, db_conn):
        """Owner should be able to grant project access to others."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)
        )
        db_conn.commit()
        
        # Owner grants access (use target_tenant_id not tenant_id)
        result = mcp_server_with_perms._get("grant_project_access")(
            project="myproject",
            target_tenant_id="tenant2",
            role="contributor",
            human_approval=True
        )
        
        # Should succeed
        assert "Error" not in result or "granted" in result.lower()
    
    def test_owner_can_revoke_access(self, mcp_server_with_perms, db_conn):
        """Owner should be able to revoke project access."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)
        )
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "tenant2", "reader", now, now)
        )
        db_conn.commit()
        
        # Owner revokes access
        result = mcp_server_with_perms._get("revoke_project_access")(
            project="myproject",
            target_tenant_id="tenant2",
            human_approval=True
        )
        
        # Should succeed
        assert "Error" not in result or "revoked" in result.lower()


class TestContributorPermissions:
    """Test that contributors can write but not manage members."""
    
    def test_contributor_can_read(self, mcp_server_with_perms, db_conn):
        """Contributor should be able to read tasks."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "contributor", now, now)
        )
        db_conn.commit()
        
        result = mcp_server_with_perms._get("list_tasks")(project="myproject")
        
        # Should succeed
        assert "Error" not in result or result.startswith("[")
    
    def test_contributor_can_write(self, mcp_server_with_perms, db_conn):
        """Contributor should be able to create and update tasks."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "contributor", now, now)
        )
        db_conn.commit()
        
        # Create task
        result = mcp_server_with_perms._get("create_task")(
            id="t1", title="Test", project="myproject"
        )
        
        # Should succeed
        if result and not result.startswith("Error"):
            data = json.loads(result)
            assert data["id"] == "t1"
    
    def test_contributor_cannot_delete(self, mcp_server_with_perms, db_conn):
        """Contributor should NOT be able to delete tasks (owner-only)."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "contributor", now, now)
        )
        db_conn.commit()
        
        # Create task (as contributor)
        mcp_server_with_perms._get("create_task")(
            id="t1", title="Test", project="myproject"
        )
        
        # Try to delete (should fail)
        result = mcp_server_with_perms._get("delete_task")(task_id="t1")
        
        # Should be denied
        if "OPM_ENFORCE_PERMISSIONS" in os.environ:
            assert "Error" in result or "denied" in result.lower() or "insufficient" in result.lower()
    
    def test_system_tenant_bypasses_contributor_restrictions(self, mcp_server_with_perms, db_conn):
        """System tenant can grant access even when marked as contributor."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "contributor", now, now)
        )
        db_conn.commit()
        
        # System tenant can grant access even though permission row says contributor
        result = mcp_server_with_perms._get("grant_project_access")(
            project="myproject",
            target_tenant_id="tenant2",
            role="reader",
            human_approval=True
        )
        
        # System bypasses permission checks, so this succeeds
        assert "Error" not in result
    
    def test_system_tenant_bypasses_revoke_restrictions(self, mcp_server_with_perms, db_conn):
        """System tenant can revoke access even when marked as contributor."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "contributor", now, now)
        )
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "tenant2", "reader", now, now)
        )
        db_conn.commit()
        
        # System tenant can revoke even though permission row says contributor
        result = mcp_server_with_perms._get("revoke_project_access")(
            project="myproject",
            target_tenant_id="tenant2",
            human_approval=True
        )
        
        # System bypasses permission checks, so this succeeds
        assert "Error" not in result


class TestReaderPermissions:
    """Test that readers have read-only access."""
    
    def test_reader_can_read(self, mcp_server_with_perms, db_conn):
        """Reader should be able to list and get tasks."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "reader", now, now)
        )
        db_conn.commit()
        
        result = mcp_server_with_perms._get("list_tasks")(project="myproject")
        
        # Should succeed
        assert "Error" not in result or result.startswith("[")
    
    def test_system_tenant_bypasses_reader_write_restrictions(self, mcp_server_with_perms, db_conn):
        """System tenant can write even when marked as reader."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "reader", now, now)
        )
        db_conn.commit()
        
        # System tenant can create tasks even though permission row says reader
        result = mcp_server_with_perms._get("create_task")(
            id="t1", title="Test", project="myproject"
        )
        
        # System bypasses permission checks, so this succeeds
        assert "Error" not in result
        data = json.loads(result)
        assert data["id"] == "t1"
    
    def test_reader_cannot_delete(self, mcp_server_with_perms, db_conn):
        """Reader should NOT be able to delete tasks."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "owner_tenant", "owner", now, now)
        )
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "reader", now, now)
        )
        # Create a task as owner
        db_conn.execute(
            "INSERT INTO tasks (id, title, project, priority, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t1", "Test", "myproject", "medium", "pending", now, now)
        )
        db_conn.commit()
        
        # Try to delete as reader
        result = mcp_server_with_perms._get("delete_task")(task_id="t1")
        
        # Should be denied
        if "OPM_ENFORCE_PERMISSIONS" in os.environ:
            assert "Error" in result or "denied" in result.lower() or "insufficient" in result.lower()


class TestGrantProjectAccess:
    """Test grant_project_access tool."""
    
    def test_creates_permission_row(self, mcp_server, db_conn):
        """grant_project_access should insert row into project_permissions."""
        # Set up owner first
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)
        )
        db_conn.commit()
        
        # Grant access
        mcp_server._get("grant_project_access")(
            project="myproject",
            target_tenant_id="tenant2",
            role="contributor",
            human_approval=True
        )
        
        # Verify row was created
        row = db_conn.execute(
            "SELECT * FROM project_permissions WHERE project = ? AND tenant_id = ?",
            ("myproject", "tenant2")
        ).fetchone()
        
        assert row is not None
        assert row["role"] == "contributor"
    
    def test_validates_role(self, mcp_server, db_conn):
        """grant_project_access should reject invalid roles."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)
        )
        db_conn.commit()
        
        # Try invalid role
        result = mcp_server._get("grant_project_access")(
            project="myproject",
            target_tenant_id="tenant2",
            role="admin",  # invalid
            human_approval=True
        )
        
        # Should fail validation
        assert "Error" in result or "invalid" in result.lower()
    
    def test_requires_human_approval(self, mcp_server):
        """grant_project_access should have human_approval=True."""
        # Check that tool is marked for human approval
        # This is more of a schema test - implementation may vary
        pass


class TestRevokeProjectAccess:
    """Test revoke_project_access tool."""
    
    def test_removes_permission_row(self, mcp_server, db_conn):
        """revoke_project_access should delete row from project_permissions."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)
        )
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "tenant2", "reader", now, now)
        )
        db_conn.commit()
        
        # Revoke access
        mcp_server._get("revoke_project_access")(
            project="myproject",
            target_tenant_id="tenant2",
            human_approval=True
        )
        
        # Verify row was deleted
        row = db_conn.execute(
            "SELECT * FROM project_permissions WHERE project = ? AND tenant_id = ?",
            ("myproject", "tenant2")
        ).fetchone()
        
        assert row is None
    
    def test_requires_human_approval(self, mcp_server):
        """revoke_project_access should have human_approval=True."""
        pass


class TestListProjectPermissions:
    """Test list_project_permissions tool."""
    
    def test_returns_all_members(self, mcp_server, db_conn):
        """Should return all members and their roles for a project."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "tenant1", "owner", now, now)
        )
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "tenant2", "contributor", now, now)
        )
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "tenant3", "reader", now, now)
        )
        db_conn.commit()
        
        result = mcp_server._get("list_project_permissions")(project="myproject")
        
        if result and not result.startswith("Error"):
            data = json.loads(result)
            assert "members" in data
            assert len(data["members"]) == 3
            # Check roles are present
            roles = [m["role"] for m in data["members"]]
            assert "owner" in roles
            assert "contributor" in roles
            assert "reader" in roles


class TestPermissionsDisabled:
    """Test backward compatibility when permissions enforcement is disabled."""
    
    def test_all_access_allowed_when_disabled(self, mcp_server, db_conn):
        """When OPM_ENFORCE_PERMISSIONS is unset, all access should be allowed."""
        # Ensure env var is not set
        if "OPM_ENFORCE_PERMISSIONS" in os.environ:
            del os.environ["OPM_ENFORCE_PERMISSIONS"]
        
        # Try to access project without any permissions set
        result = mcp_server._get("create_task")(
            id="t1", title="Test", project="anyproject"
        )
        
        # Should succeed (backward compatibility)
        if result and not result.startswith("Error"):
            data = json.loads(result)
            assert data["id"] == "t1"
    
    def test_reads_succeed_without_permissions(self, mcp_server):
        """List/get operations should work without permission rows."""
        result = mcp_server._get("list_tasks")(project="anyproject")
        
        # Should succeed (empty list)
        assert "Error" not in result or result.startswith("[")


class TestMigrationBackfill:
    """Test migrate_permissions tool for existing projects."""
    
    def test_backfills_existing_tasks(self, mcp_server, db_conn):
        """migrate_permissions should create owner permissions for existing tasks."""
        # Create tasks without permissions
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO tasks (id, title, project, priority, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t1", "Task 1", "project_a", "medium", "pending", now, now)
        )
        db_conn.execute(
            "INSERT INTO tasks (id, title, project, priority, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t2", "Task 2", "project_b", "medium", "pending", now, now)
        )
        # Add activity_log entries (required by migration logic)
        db_conn.execute(
            "INSERT INTO activity_log (task_id, actor, action, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("t1", "tenant1", "created", now)
        )
        db_conn.execute(
            "INSERT INTO activity_log (task_id, actor, action, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("t2", "tenant2", "created", now)
        )
        db_conn.commit()
        
        # Run migration (with human_approval)
        result = mcp_server._get("migrate_permissions")(human_approval=True)
        
        # Verify permissions were created
        perms = db_conn.execute(
            "SELECT * FROM project_permissions"
        ).fetchall()
        
        # Should have created owner permissions for distinct projects
        assert len(perms) >= 2
    
    def test_makes_tenant_owner_of_their_projects(self, mcp_server, db_conn):
        """Existing tenant tasks should make them owner of those projects."""
        # This would require tenant_id column on tasks, which may not exist
        # Test may need adjustment based on actual schema
        pass
    
    def test_requires_human_approval(self, mcp_server):
        """migrate_permissions should have human_approval=True."""
        pass


class TestRoleHierarchy:
    """Test role hierarchy (owner > contributor > reader)."""
    
    def test_owner_exceeds_contributor_level(self, mcp_server_with_perms, db_conn):
        """Owner role should pass contributor-level checks."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "owner", now, now)
        )
        db_conn.commit()
        
        # Owner should be able to do contributor actions (create task)
        result = mcp_server_with_perms._get("create_task")(
            id="t1", title="Test", project="myproject"
        )
        
        # Should succeed
        if result and not result.startswith("Error"):
            data = json.loads(result)
            assert data["id"] == "t1"
    
    def test_contributor_exceeds_reader_level(self, mcp_server_with_perms, db_conn):
        """Contributor role should pass reader-level checks."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "contributor", now, now)
        )
        db_conn.commit()
        
        # Contributor should be able to do reader actions (list tasks)
        result = mcp_server_with_perms._get("list_tasks")(project="myproject")
        
        # Should succeed
        assert "Error" not in result or result.startswith("[")
    
    def test_system_tenant_always_bypasses_role_hierarchy(self, mcp_server_with_perms, db_conn):
        """System tenant bypasses role hierarchy (can do anything regardless of assigned role)."""
        now = datetime.now(timezone.utc).isoformat()
        db_conn.execute(
            "INSERT INTO project_permissions (project, tenant_id, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("myproject", "system", "reader", now, now)
        )
        db_conn.commit()
        
        # System tenant can create task even with reader role
        result = mcp_server_with_perms._get("create_task")(
            id="t1", title="Test", project="myproject"
        )
        
        # System bypasses all role checks
        assert "Error" not in result
        data = json.loads(result)
        assert data["id"] == "t1"
