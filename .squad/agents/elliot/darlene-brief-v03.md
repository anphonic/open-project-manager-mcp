# Darlene Brief: v0.3.0 Implementation

**From:** Elliot (Lead & Architect)  
**To:** Darlene (Backend Engineer)  
**Date:** 2026-04-04

---

## Overview

You're implementing two features after Trenton adds the schema:
1. **Telemetry** — metrics recording + query tools + REST endpoints
2. **Permissions** — access control + 8 MCP tools

Reference: `.squad/agents/elliot/v03-architecture.md` for full design.

---

## 1. Telemetry Implementation

### 1.1 Core Helper: `_record_metric()`

Add this after the existing `_log()` helper (~line 324):

```python
async def _record_metric(tenant_id: str, metric_type: str, metric_name: str, 
                         latency_ms: int = None, is_error: bool = False):
    """Record a telemetry metric. Called inline after each tool/REST call."""
    bucket_hour = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ).isoformat().replace('+00:00', 'Z')
    now = _now()
    
    def _do_write():
        conn.execute("""
            INSERT INTO telemetry_metrics 
                (tenant_id, metric_type, metric_name, bucket_hour, 
                 count, sum_ms, min_ms, max_ms, error_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, metric_type, metric_name, bucket_hour) DO UPDATE SET
                count = count + 1,
                sum_ms = CASE WHEN excluded.sum_ms IS NOT NULL 
                         THEN COALESCE(sum_ms, 0) + excluded.sum_ms ELSE sum_ms END,
                min_ms = CASE WHEN excluded.min_ms IS NOT NULL 
                         THEN MIN(COALESCE(min_ms, excluded.min_ms), excluded.min_ms) ELSE min_ms END,
                max_ms = CASE WHEN excluded.max_ms IS NOT NULL 
                         THEN MAX(COALESCE(max_ms, 0), excluded.max_ms) ELSE max_ms END,
                error_count = error_count + excluded.error_count,
                updated_at = excluded.updated_at
        """, (tenant_id, metric_type, metric_name, bucket_hour, 
              latency_ms, latency_ms, latency_ms, 
              1 if is_error else 0, now, now))
        conn.commit()
    
    # Fire-and-forget — don't block the caller
    asyncio.create_task(asyncio.to_thread(_do_write))
```

### 1.2 Instrumentation Pattern

Wrap high-traffic tools with timing. Example for `create_task`:

```python
@mcp.tool()
async def create_task(...) -> str:
    start_time = time.time()
    actor = _get_actor()
    # ... existing logic ...
    latency_ms = int((time.time() - start_time) * 1000)
    is_error = result.startswith("Error:")
    asyncio.create_task(_record_metric(actor, "tool_call", "create_task", latency_ms, is_error))
    return result
```

**Instrument these tools first:** `create_task`, `update_task`, `list_tasks`, `search_tasks`, `get_task`

### 1.3 MCP Tools (4 new tools)

```python
@mcp.tool()
async def get_telemetry_summary(hours: int = 24) -> str:
    """Get aggregated telemetry for calling tenant over last N hours."""
    actor = _get_actor()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = await _db_execute("""
        SELECT metric_type, metric_name, 
               SUM(count) as total_calls,
               SUM(error_count) as total_errors,
               ROUND(SUM(sum_ms) * 1.0 / SUM(count), 2) as avg_latency_ms
        FROM telemetry_metrics
        WHERE tenant_id = ? AND bucket_hour >= ?
        GROUP BY metric_type, metric_name
        ORDER BY total_calls DESC
    """, (actor, cutoff))
    return json.dumps({"hours": hours, "metrics": [dict(r) for r in rows]})

@mcp.tool()
async def get_telemetry_by_tool(tool_name: str, hours: int = 24) -> str:
    """Get detailed metrics for a specific tool."""
    actor = _get_actor()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = await _db_execute("""
        SELECT bucket_hour, count, error_count, 
               ROUND(sum_ms * 1.0 / count, 2) as avg_latency_ms,
               min_ms, max_ms
        FROM telemetry_metrics
        WHERE tenant_id = ? AND metric_name = ? AND bucket_hour >= ?
        ORDER BY bucket_hour DESC
    """, (actor, tool_name, cutoff))
    return json.dumps({"tool": tool_name, "hours": hours, "buckets": [dict(r) for r in rows]})

@mcp.tool()
async def list_top_tools(limit: int = 10, hours: int = 24) -> str:
    """List most-called tools for calling tenant."""
    actor = _get_actor()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = await _db_execute("""
        SELECT metric_name, SUM(count) as total_calls
        FROM telemetry_metrics
        WHERE tenant_id = ? AND metric_type = 'tool_call' AND bucket_hour >= ?
        GROUP BY metric_name
        ORDER BY total_calls DESC
        LIMIT ?
    """, (actor, cutoff, min(limit, 100)))
    return json.dumps({"hours": hours, "top_tools": [dict(r) for r in rows]})

@mcp.tool()
async def get_error_summary(hours: int = 24) -> str:
    """Get error counts by tool for calling tenant."""
    actor = _get_actor()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = await _db_execute("""
        SELECT metric_name, SUM(error_count) as total_errors, SUM(count) as total_calls
        FROM telemetry_metrics
        WHERE tenant_id = ? AND bucket_hour >= ? AND error_count > 0
        GROUP BY metric_name
        ORDER BY total_errors DESC
    """, (actor, cutoff))
    return json.dumps({"hours": hours, "errors": [dict(r) for r in rows]})
```

### 1.4 REST Endpoints (4 new routes)

Add to the REST router section (~line 2300+):

```python
async def telemetry_summary_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    hours = int(request.query_params.get("hours", 24))
    result = await get_telemetry_summary(hours)
    return JSONResponse(json.loads(result))

async def telemetry_tool_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    tool_name = request.path_params["tool_name"]
    hours = int(request.query_params.get("hours", 24))
    result = await get_telemetry_by_tool(tool_name, hours)
    return JSONResponse(json.loads(result))

async def telemetry_top_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    limit = int(request.query_params.get("limit", 10))
    hours = int(request.query_params.get("hours", 24))
    result = await list_top_tools(limit, hours)
    return JSONResponse(json.loads(result))

async def telemetry_errors_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    hours = int(request.query_params.get("hours", 24))
    result = await get_error_summary(hours)
    return JSONResponse(json.loads(result))

# Add routes:
Route("/api/v1/telemetry/summary", telemetry_summary_endpoint, methods=["GET"]),
Route("/api/v1/telemetry/tools/{tool_name}", telemetry_tool_endpoint, methods=["GET"]),
Route("/api/v1/telemetry/top", telemetry_top_endpoint, methods=["GET"]),
Route("/api/v1/telemetry/errors", telemetry_errors_endpoint, methods=["GET"]),
```

---

## 2. Permissions Implementation

### 2.1 Core Helper: `_check_project_access()`

Add after `_record_metric()`:

```python
VALID_ROLES = {"owner", "contributor", "reader"}
_ROLE_HIERARCHY = {"owner": 3, "contributor": 2, "reader": 1}

async def _check_project_access(project: str, required_role: str) -> str | None:
    """
    Check if current tenant has required_role on project.
    Returns None if allowed, error string if denied.
    """
    if not os.environ.get("OPM_ENFORCE_PERMISSIONS"):
        return None  # Enforcement disabled (default for v0.3.0)
    
    tenant_id = _get_actor()
    if tenant_id == "system":
        return None  # Unauthenticated mode — allow all
    
    row = await _db_execute_one(
        "SELECT role FROM project_permissions WHERE project = ? AND tenant_id = ?",
        (project, tenant_id)
    )
    if not row:
        return f"Error: access denied to project '{project}'"
    
    role = row["role"]
    required_level = _ROLE_HIERARCHY.get(required_role, 0)
    actual_level = _ROLE_HIERARCHY.get(role, 0)
    
    if actual_level < required_level:
        return f"Error: insufficient permissions (have '{role}', need '{required_role}')"
    return None
```

### 2.2 Enforcement Points

Modify existing tools to check permissions:

```python
# create_task — add near top of function:
err = await _check_project_access(project, "contributor")
if err:
    return err

# update_task — need to get task first to check project:
row = await _db_execute_one("SELECT project FROM tasks WHERE id = ?", (task_id,))
if not row:
    return f"Error: task '{task_id}' not found"
err = await _check_project_access(row["project"], "contributor")
if err:
    return err

# delete_task — requires owner:
err = await _check_project_access(row["project"], "owner")
if err:
    return err

# list_tasks, get_task, search_tasks — reader:
err = await _check_project_access(project, "reader")
if err:
    return err
```

### 2.3 MCP Tools (8 new tools)

```python
@mcp.tool()
async def grant_project_access(
    project: str, 
    target_tenant_id: str, 
    role: str,
    human_approval: bool = False
) -> str:
    """Grant a tenant access to a project. Requires owner role."""
    if not human_approval:
        return "Error: human_approval=True required for grant_project_access"
    if role not in VALID_ROLES:
        return f"Error: invalid role '{role}' (must be owner/contributor/reader)"
    
    # Check caller is owner
    err = await _check_project_access(project, "owner")
    if err:
        return err
    
    actor = _get_actor()
    now = _now()
    
    def _do_write():
        try:
            conn.execute("""
                INSERT INTO project_permissions (project, tenant_id, role, granted_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project, tenant_id) DO UPDATE SET
                    role = excluded.role,
                    granted_by = excluded.granted_by,
                    updated_at = excluded.updated_at
            """, (project, target_tenant_id, role, actor, now, now))
            conn.commit()
            return None
        except sqlite3.Error:
            return "Error: database error granting access"
    
    result = await _locked_write(_do_write)
    if result:
        return result
    return json.dumps({"project": project, "tenant_id": target_tenant_id, "role": role, "granted_by": actor})

@mcp.tool()
async def revoke_project_access(
    project: str, 
    target_tenant_id: str,
    human_approval: bool = False
) -> str:
    """Revoke a tenant's access to a project. Requires owner role."""
    if not human_approval:
        return "Error: human_approval=True required for revoke_project_access"
    
    err = await _check_project_access(project, "owner")
    if err:
        return err
    
    actor = _get_actor()
    if target_tenant_id == actor:
        return "Error: cannot revoke your own access (use transfer_project_ownership)"
    
    def _do_write():
        cur = conn.execute(
            "DELETE FROM project_permissions WHERE project = ? AND tenant_id = ?",
            (project, target_tenant_id)
        )
        conn.commit()
        return cur.rowcount
    
    rows = await _locked_write(_do_write)
    if rows == 0:
        return f"Error: no access entry found for '{target_tenant_id}' on '{project}'"
    return json.dumps({"revoked": True, "project": project, "tenant_id": target_tenant_id})

@mcp.tool()
async def list_project_members(project: str) -> str:
    """List all members with access to a project."""
    err = await _check_project_access(project, "reader")
    if err:
        return err
    
    rows = await _db_execute("""
        SELECT tenant_id, role, granted_by, created_at
        FROM project_permissions
        WHERE project = ?
        ORDER BY 
            CASE role WHEN 'owner' THEN 0 WHEN 'contributor' THEN 1 ELSE 2 END,
            created_at
    """, (project,))
    return json.dumps({"project": project, "members": [dict(r) for r in rows]})

@mcp.tool()
async def get_my_projects() -> str:
    """List all projects the calling tenant has access to."""
    actor = _get_actor()
    rows = await _db_execute("""
        SELECT project, role, created_at
        FROM project_permissions
        WHERE tenant_id = ?
        ORDER BY project
    """, (actor,))
    return json.dumps({"tenant_id": actor, "projects": [dict(r) for r in rows]})

@mcp.tool()
async def transfer_project_ownership(
    project: str,
    new_owner_tenant_id: str,
    human_approval: bool = False
) -> str:
    """Transfer project ownership to another tenant. Current owner becomes contributor."""
    if not human_approval:
        return "Error: human_approval=True required for transfer_project_ownership"
    
    err = await _check_project_access(project, "owner")
    if err:
        return err
    
    actor = _get_actor()
    now = _now()
    
    def _do_write():
        # Make new tenant owner
        conn.execute("""
            INSERT INTO project_permissions (project, tenant_id, role, granted_by, created_at, updated_at)
            VALUES (?, ?, 'owner', ?, ?, ?)
            ON CONFLICT(project, tenant_id) DO UPDATE SET
                role = 'owner',
                granted_by = excluded.granted_by,
                updated_at = excluded.updated_at
        """, (project, new_owner_tenant_id, actor, now, now))
        # Demote current owner to contributor
        conn.execute("""
            UPDATE project_permissions SET role = 'contributor', updated_at = ?
            WHERE project = ? AND tenant_id = ? AND role = 'owner'
        """, (now, project, actor))
        conn.commit()
        return None
    
    result = await _locked_write(_do_write)
    if result:
        return result
    return json.dumps({
        "transferred": True, 
        "project": project, 
        "new_owner": new_owner_tenant_id,
        "previous_owner": actor,
        "previous_owner_new_role": "contributor"
    })

@mcp.tool()
async def get_project_access(project: str, target_tenant_id: str) -> str:
    """Check a specific tenant's access to a project."""
    err = await _check_project_access(project, "reader")
    if err:
        return err
    
    row = await _db_execute_one(
        "SELECT role, granted_by, created_at FROM project_permissions WHERE project = ? AND tenant_id = ?",
        (project, target_tenant_id)
    )
    if not row:
        return json.dumps({"project": project, "tenant_id": target_tenant_id, "access": None})
    return json.dumps({"project": project, "tenant_id": target_tenant_id, "access": dict(row)})

@mcp.tool()
async def migrate_permissions(human_approval: bool = False) -> str:
    """Admin: Backfill permissions for existing projects. Makes each tenant owner of their projects."""
    if not human_approval:
        return "Error: human_approval=True required for migrate_permissions"
    
    actor = _get_actor()
    now = _now()
    
    # Get distinct (project, tenant) pairs from activity_log (created actions)
    # This is the most reliable way to determine who "owns" existing projects
    rows = await _db_execute("""
        SELECT DISTINCT t.project, al.actor
        FROM tasks t
        JOIN activity_log al ON al.task_id = t.id AND al.action = 'created'
        WHERE al.actor != 'system'
          AND NOT EXISTS (
              SELECT 1 FROM project_permissions pp 
              WHERE pp.project = t.project AND pp.tenant_id = al.actor
          )
    """, ())
    
    count = 0
    def _do_write():
        nonlocal count
        for row in rows:
            conn.execute("""
                INSERT OR IGNORE INTO project_permissions 
                    (project, tenant_id, role, granted_by, created_at, updated_at)
                VALUES (?, ?, 'owner', 'migration', ?, ?)
            """, (row["project"], row["actor"], now, now))
            count += 1
        conn.commit()
        return None
    
    await _locked_write(_do_write)
    return json.dumps({"migrated": count, "message": f"Granted owner role on {count} project-tenant pairs"})

@mcp.tool()
async def set_permission_enforcement(enabled: bool, human_approval: bool = False) -> str:
    """Admin: Toggle permission enforcement. WARNING: affects all operations."""
    if not human_approval:
        return "Error: human_approval=True required for set_permission_enforcement"
    
    # This is a runtime toggle — doesn't persist across restarts
    # For production, use OPM_ENFORCE_PERMISSIONS env var
    if enabled:
        os.environ["OPM_ENFORCE_PERMISSIONS"] = "1"
    else:
        os.environ.pop("OPM_ENFORCE_PERMISSIONS", None)
    
    return json.dumps({
        "enforcement_enabled": enabled,
        "note": "Runtime change — set OPM_ENFORCE_PERMISSIONS=1 for persistent enforcement"
    })
```

### 2.4 REST Endpoints (4 new routes)

```python
async def project_members_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    project = request.path_params["project"]
    
    if request.method == "GET":
        result = await list_project_members(project)
        if result.startswith("Error:"):
            return JSONResponse({"error": result}, status_code=403)
        return JSONResponse(json.loads(result))
    
    elif request.method == "POST":
        body, body_err = await _read_json_body(request)
        if body_err:
            return body_err
        result = await grant_project_access(
            project, 
            body.get("tenant_id", ""), 
            body.get("role", "reader"),
            human_approval=True  # REST assumes human approval
        )
        if result.startswith("Error:"):
            return JSONResponse({"error": result}, status_code=400)
        return JSONResponse(json.loads(result), status_code=201)
    
    return JSONResponse({"error": "Method not allowed"}, status_code=405)

async def project_member_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    project = request.path_params["project"]
    tenant_id = request.path_params["tenant_id"]
    
    if request.method == "DELETE":
        result = await revoke_project_access(project, tenant_id, human_approval=True)
        if result.startswith("Error:"):
            return JSONResponse({"error": result}, status_code=400)
        return JSONResponse(json.loads(result))
    
    return JSONResponse({"error": "Method not allowed"}, status_code=405)

async def my_projects_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    result = await get_my_projects()
    return JSONResponse(json.loads(result))

# Add routes:
Route("/api/v1/projects/{project}/members", project_members_endpoint, methods=["GET", "POST"]),
Route("/api/v1/projects/{project}/members/{tenant_id}", project_member_endpoint, methods=["DELETE"]),
Route("/api/v1/projects/mine", my_projects_endpoint, methods=["GET"]),
```

---

## 3. Testing Checklist

For Romero — key scenarios to cover:

### Telemetry
- [ ] Metrics increment on tool calls
- [ ] Latency recorded correctly
- [ ] Error counts increment on failures
- [ ] Hourly bucket boundaries work correctly
- [ ] Tenant isolation (can't see other tenant's metrics)

### Permissions
- [ ] Owner can grant/revoke access
- [ ] Contributor can create/update but not delete
- [ ] Reader can only view
- [ ] Deny-by-default when enforcement enabled
- [ ] Migration backfills correctly
- [ ] Cannot revoke own access
- [ ] Transfer ownership demotes previous owner

---

## 4. Deployment Notes

1. **Telemetry:** Safe to deploy immediately — no breaking changes
2. **Permissions:** 
   - Deploy with `OPM_ENFORCE_PERMISSIONS` unset (default off)
   - Run `migrate_permissions(human_approval=True)` to backfill
   - Test with a few projects first
   - Enable enforcement when ready: `set_permission_enforcement(True, human_approval=True)`

---

*Ready to implement — ask questions in squad-knowledge board if blocked.*

— Elliot
