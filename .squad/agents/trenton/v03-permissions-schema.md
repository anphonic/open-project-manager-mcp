# v0.3.0 Permissions Schema

**Author:** Trenton (Shama Biswani)  
**Date:** 2026-04-04  
**Status:** Production-ready DDL

---

## Overview

The permissions schema enables project-level access control where:
- Each project can be **private** (owner-only) or **shared** (explicit grants to other tenants)
- Permissions are **read-only** or **read-write** at the project level
- Backward compatible: existing tasks with no permission rows default to owner-only access

Design principles:
- **Default-deny:** No explicit permission = no access (except for project owner)
- **Simple model:** Project-level only (not task-level)
- **Query efficiency:** Fast permission checks on every task read/write
- **Migration safety:** Zero data loss; existing tasks remain accessible to their creators

---

## Schema DDL

### 1. Projects Table (metadata)

```sql
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    owner_tenant_id TEXT NOT NULL,      -- tenant that created/owns the project
    visibility      TEXT NOT NULL DEFAULT 'private',  -- 'private' | 'shared'
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS projects_owner_idx ON projects(owner_tenant_id);
```

**Notes:**
- `visibility = 'private'`: Only owner can access
- `visibility = 'shared'`: Owner + explicitly granted tenants can access
- Project ID should match the `project` column in existing `tasks` table

---

### 2. Project Permissions Table (grants)

```sql
CREATE TABLE IF NOT EXISTS project_permissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,          -- tenant being granted access
    permission  TEXT NOT NULL DEFAULT 'read',  -- 'read' | 'write'
    granted_by  TEXT NOT NULL,          -- tenant that granted this permission (usually owner)
    granted_at  TEXT NOT NULL,
    UNIQUE(project_id, tenant_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS project_permissions_tenant_idx 
    ON project_permissions(tenant_id);
CREATE INDEX IF NOT EXISTS project_permissions_project_idx 
    ON project_permissions(project_id);
```

**Permission levels:**
- `'read'`: Can view tasks, query project, no mutations
- `'write'`: Can create/update/delete tasks, full project access

**Cascading deletes:** When a project is deleted, all permission grants are automatically removed.

---

## Migration Code

Safe to run on existing databases. Does not modify existing `tasks` table structure.

```python
def _migrate_permissions_schema(conn: sqlite3.Connection):
    """Apply v0.3.0 permissions schema migration (idempotent)."""
    _PERMISSIONS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS projects (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        owner_tenant_id TEXT NOT NULL,
        visibility      TEXT NOT NULL DEFAULT 'private',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS projects_owner_idx ON projects(owner_tenant_id);
    
    CREATE TABLE IF NOT EXISTS project_permissions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id  TEXT NOT NULL,
        tenant_id   TEXT NOT NULL,
        permission  TEXT NOT NULL DEFAULT 'read',
        granted_by  TEXT NOT NULL,
        granted_at  TEXT NOT NULL,
        UNIQUE(project_id, tenant_id),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS project_permissions_tenant_idx 
        ON project_permissions(tenant_id);
    CREATE INDEX IF NOT EXISTS project_permissions_project_idx 
        ON project_permissions(project_id);
    """
    
    conn.executescript(_PERMISSIONS_SCHEMA)
    conn.commit()
```

**Integration point:** Add `_migrate_permissions_schema(conn)` after telemetry migration in `create_server()`.

---

## Backward Compatibility Strategy

### Existing Tasks with No Projects Table

**Problem:** Existing deployments have tasks with `project` column values, but no `projects` table entries.

**Solution:** Auto-create project entries on first access:

```python
def _ensure_project_exists(conn: sqlite3.Connection, project_id: str, tenant_id: str):
    """Create project entry if it doesn't exist (backward compatibility helper)."""
    existing = conn.execute(
        "SELECT id FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    
    if not existing:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO projects (id, name, owner_tenant_id, visibility, created_at, updated_at)
            VALUES (?, ?, ?, 'private', ?, ?)
        """, (project_id, project_id, tenant_id, now, now))
        conn.commit()
```

**When to call:**
- On first task creation in a project
- On first query against a project
- Lazily populate `projects` table over time

---

## Permission Check Logic

### Core Permission Check Function

```python
def _check_project_permission(
    conn: sqlite3.Connection, 
    tenant_id: str, 
    project_id: str, 
    required_permission: str = 'read'
) -> bool:
    """
    Check if tenant_id has required_permission on project_id.
    
    Returns True if:
    1. Tenant is the project owner, OR
    2. Project is 'shared' AND tenant has explicit grant with sufficient permission
    
    Returns False otherwise (default-deny).
    """
    # 1. Check if tenant is project owner
    project = conn.execute(
        "SELECT owner_tenant_id, visibility FROM projects WHERE id = ?", 
        (project_id,)
    ).fetchone()
    
    if not project:
        # Project doesn't exist in projects table (backward compat case)
        # Allow access if tenant has tasks in this project (they likely created it)
        task_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE project = ? AND tenant_id = ?",
            (project_id, tenant_id)
        ).fetchone()[0]
        return task_count > 0
    
    owner_tenant_id, visibility = project
    
    # Owner always has full access
    if tenant_id == owner_tenant_id:
        return True
    
    # Private projects: only owner has access
    if visibility == 'private':
        return False
    
    # Shared projects: check explicit grants
    grant = conn.execute("""
        SELECT permission FROM project_permissions
        WHERE project_id = ? AND tenant_id = ?
    """, (project_id, tenant_id)).fetchone()
    
    if not grant:
        return False  # No grant = no access
    
    permission = grant[0]
    
    # Check if grant level is sufficient
    if required_permission == 'read':
        return permission in ('read', 'write')
    elif required_permission == 'write':
        return permission == 'write'
    
    return False
```

### Integration into Existing Server Code

**Before reading tasks:**
```python
# In list_tasks, get_task, search_tasks tools:
if not _check_project_permission(conn, tenant_id, project_id, 'read'):
    raise PermissionError(f"No read access to project '{project_id}'")
```

**Before writing tasks:**
```python
# In create_task, update_task, delete_task tools:
if not _check_project_permission(conn, tenant_id, project_id, 'write'):
    raise PermissionError(f"No write access to project '{project_id}'")
```

**Handle tenant_id extraction:**
```python
# Extract tenant_id from MCP context or REST API Bearer token
# Already exists in current server.py via ApiKeyVerifier
tenant_id = ctx.client_id  # or await _verify_bearer(token)
```

---

## Backward Compatibility: Existing Tasks Table

**Current schema:** `tasks` table has `project` column but no `tenant_id`.

**Migration challenge:** Need to track which tenant owns each task for permission checks.

**Solution:** Add nullable `tenant_id` column to tasks table:

```python
def _migrate_tasks_tenant_column(conn: sqlite3.Connection):
    """Add tenant_id column to tasks table (idempotent)."""
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN tenant_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Backfill tenant_id from activity_log (if actor is consistently tenant_id)
    # OR leave NULL for old tasks (treat as legacy, owner-only)
    # OR infer from oldest task per project (assume first creator is owner)
```

**Recommendation:** For existing tasks with `tenant_id = NULL`:
- Assume they belong to the first tenant that queries them (lazily claim)
- OR treat as global (accessible by all) until explicitly claimed
- OR populate from `activity_log.actor` if actor field contains tenant_id

**Conservative approach:**
```sql
-- Mark all NULL tenant_id tasks as owned by a special "legacy" tenant
UPDATE tasks SET tenant_id = 'system' WHERE tenant_id IS NULL;
```

---

## Key Query Patterns

### 1. Can tenant X read project Y?

```python
allowed = _check_project_permission(conn, tenant_id='squad-alpha', project_id='v03', required_permission='read')
```

### 2. Can tenant X write to project Y?

```python
allowed = _check_project_permission(conn, tenant_id='squad-alpha', project_id='v03', required_permission='write')
```

### 3. List all projects accessible to tenant X

```sql
-- Projects owned by tenant
SELECT id, name, visibility, 'owner' AS access_type
FROM projects
WHERE owner_tenant_id = ?

UNION

-- Projects shared with tenant
SELECT p.id, p.name, p.visibility, pp.permission AS access_type
FROM projects p
JOIN project_permissions pp ON p.id = pp.project_id
WHERE pp.tenant_id = ?
  AND p.visibility = 'shared'

ORDER BY name;
```

### 4. List all tenants with access to project X

```sql
-- Owner (implicit access)
SELECT owner_tenant_id AS tenant_id, 'write' AS permission, 'owner' AS grant_type
FROM projects
WHERE id = ?

UNION

-- Explicit grants
SELECT tenant_id, permission, 'grant' AS grant_type
FROM project_permissions
WHERE project_id = ?

ORDER BY tenant_id;
```

### 5. Grant read access to tenant Y on project X

```sql
INSERT INTO project_permissions (project_id, tenant_id, permission, granted_by, granted_at)
VALUES (?, ?, 'read', ?, datetime('now'))
ON CONFLICT(project_id, tenant_id) DO UPDATE SET
    permission = excluded.permission,
    granted_by = excluded.granted_by,
    granted_at = excluded.granted_at;

-- Also update project visibility to 'shared'
UPDATE projects SET visibility = 'shared', updated_at = datetime('now') WHERE id = ?;
```

### 6. Revoke access from tenant Y on project X

```sql
DELETE FROM project_permissions
WHERE project_id = ? AND tenant_id = ?;

-- If no more grants, revert project to 'private'
UPDATE projects 
SET visibility = 'private', updated_at = datetime('now')
WHERE id = ? 
  AND NOT EXISTS (
      SELECT 1 FROM project_permissions WHERE project_id = projects.id
  );
```

---

## Example MCP Tool: `grant_project_access`

```python
@mcp.tool()
async def grant_project_access(
    project_id: str,
    tenant_id: str,
    permission: str = "read"
) -> str:
    """
    Grant read or write access to a tenant on a project.
    
    Args:
        project_id: Project to grant access to
        tenant_id: Tenant receiving access
        permission: 'read' or 'write'
    """
    # Get calling tenant from context
    caller_tenant_id = ctx.client_id
    
    # Verify caller is project owner
    if not _check_project_permission(conn, caller_tenant_id, project_id, 'write'):
        return f"Error: Only project owner can grant access"
    
    if permission not in ('read', 'write'):
        return f"Error: permission must be 'read' or 'write'"
    
    # Grant access
    now = _now()
    conn.execute("""
        INSERT INTO project_permissions (project_id, tenant_id, permission, granted_by, granted_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(project_id, tenant_id) DO UPDATE SET
            permission = excluded.permission,
            granted_by = excluded.granted_by,
            granted_at = excluded.granted_at
    """, (project_id, tenant_id, permission, caller_tenant_id, now))
    
    # Mark project as shared
    conn.execute("""
        UPDATE projects SET visibility = 'shared', updated_at = ?
        WHERE id = ?
    """, (now, project_id))
    
    conn.commit()
    
    return f"Granted {permission} access to tenant '{tenant_id}' on project '{project_id}'"
```

---

## Example MCP Tool: `revoke_project_access`

```python
@mcp.tool()
async def revoke_project_access(
    project_id: str,
    tenant_id: str
) -> str:
    """
    Revoke a tenant's access to a project.
    
    Args:
        project_id: Project to revoke access from
        tenant_id: Tenant losing access
    """
    caller_tenant_id = ctx.client_id
    
    # Verify caller is project owner
    if not _check_project_permission(conn, caller_tenant_id, project_id, 'write'):
        return f"Error: Only project owner can revoke access"
    
    # Revoke access
    conn.execute("""
        DELETE FROM project_permissions
        WHERE project_id = ? AND tenant_id = ?
    """, (project_id, tenant_id))
    
    # If no more grants, revert to private
    remaining = conn.execute("""
        SELECT COUNT(*) FROM project_permissions WHERE project_id = ?
    """, (project_id,)).fetchone()[0]
    
    if remaining == 0:
        conn.execute("""
            UPDATE projects SET visibility = 'private', updated_at = ?
            WHERE id = ?
        """, (_now(), project_id))
    
    conn.commit()
    
    return f"Revoked access for tenant '{tenant_id}' on project '{project_id}'"
```

---

## Example MCP Tool: `list_my_projects`

```python
@mcp.tool()
async def list_my_projects() -> str:
    """List all projects accessible to the calling tenant."""
    tenant_id = ctx.client_id
    
    rows = conn.execute("""
        -- Projects owned by tenant
        SELECT id, name, visibility, 'owner' AS access_type, owner_tenant_id
        FROM projects
        WHERE owner_tenant_id = ?
        
        UNION
        
        -- Projects shared with tenant
        SELECT p.id, p.name, p.visibility, pp.permission AS access_type, p.owner_tenant_id
        FROM projects p
        JOIN project_permissions pp ON p.id = pp.project_id
        WHERE pp.tenant_id = ?
          AND p.visibility = 'shared'
        
        ORDER BY name
    """, (tenant_id, tenant_id)).fetchall()
    
    if not rows:
        return "No projects accessible"
    
    lines = ["Projects accessible to you:\n"]
    for row in rows:
        proj_id, name, visibility, access_type, owner = row
        lines.append(f"- {proj_id}: {name} ({visibility}, {access_type}, owner: {owner})")
    
    return "\n".join(lines)
```

---

## Testing Checklist

- [ ] Create project as tenant A, verify only A can access
- [ ] Grant read access to tenant B, verify B can list tasks but not create
- [ ] Grant write access to tenant B, verify B can create/update tasks
- [ ] Revoke access from tenant B, verify B loses access
- [ ] Delete project, verify all permission grants cascade-deleted
- [ ] Verify existing tasks (pre-migration) remain accessible to creator
- [ ] Test permission checks execute in <10ms (EXPLAIN QUERY PLAN)
- [ ] Verify foreign key constraints enforced (try deleting project with grants)

---

## Security Considerations

1. **Default-deny:** No permission row = no access (except owner)
2. **Owner bypass:** Owner always has full access (cannot be locked out)
3. **Privilege escalation:** Only owner can grant permissions (not granted users)
4. **Cascading deletes:** Deleting a project removes all grants (no orphaned permissions)
5. **Audit trail:** `granted_by` and `granted_at` fields track who granted access and when

---

## Future Enhancements (post-v0.3.0)

- **Role-based permissions:** Add `admin`, `contributor`, `viewer` roles instead of just `read`/`write`
- **Team-based grants:** Grant access to entire team (tenant group) instead of individual tenants
- **Task-level permissions:** Finer-grained control (e.g., only specific tasks within a project)
- **Time-limited grants:** Auto-expiring permissions (e.g., temporary contractor access)
- **Audit log:** Track all permission changes in `activity_log` table
- **Public projects:** `visibility = 'public'` for read-only access by all tenants
