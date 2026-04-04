# OPM v0.3.0 Architecture Design

**Author:** Elliot (Lead & Architect)  
**Date:** 2026-04-04  
**Status:** APPROVED — Ready for Implementation

---

## Overview

v0.3.0 introduces two major features:
1. **Telemetry System** — Per-tenant usage metrics, latency tracking, error counts
2. **Permissions System** — Project-level ACL (owner/contributor/reader roles)

Both follow OPM's established patterns:
- `create_server(db_path)` factory with tools as closures
- `human_approval=True` on destructive operations
- JSON-encoded arrays for complex fields
- Async database helpers (`_db_execute`, `_locked_write`)

---

## 1. Telemetry System

### 1.1 What Gets Tracked

| Metric | Granularity | Storage |
|--------|-------------|---------|
| Tool call counts | Per tool, per tenant, hourly bucket | `telemetry_metrics` |
| Call latency (p50, p95, p99) | Per tool, per tenant, hourly bucket | `telemetry_metrics` |
| Error counts | Per tool, per tenant, hourly bucket | `telemetry_metrics` |
| Active projects per tenant | Per tenant, hourly bucket | `telemetry_metrics` |
| REST API calls | Per endpoint, per tenant, hourly bucket | `telemetry_metrics` |

### 1.2 Schema Design

```sql
-- Hourly aggregated metrics (bucketed by hour start)
CREATE TABLE IF NOT EXISTS telemetry_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT    NOT NULL,
    metric_type  TEXT    NOT NULL,  -- 'tool_call', 'rest_call', 'error', 'latency'
    metric_name  TEXT    NOT NULL,  -- tool name or endpoint path
    bucket_hour  TEXT    NOT NULL,  -- ISO8601 hour start, e.g. '2026-04-04T14:00:00Z'
    count        INTEGER NOT NULL DEFAULT 0,
    sum_ms       INTEGER,           -- for latency: sum of durations in ms
    min_ms       INTEGER,           -- for latency: minimum duration
    max_ms       INTEGER,           -- for latency: maximum duration
    error_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(tenant_id, metric_type, metric_name, bucket_hour)
);
CREATE INDEX IF NOT EXISTS telemetry_tenant_hour_idx ON telemetry_metrics(tenant_id, bucket_hour DESC);
CREATE INDEX IF NOT EXISTS telemetry_type_idx ON telemetry_metrics(metric_type);

-- Daily rollups for long-term storage (computed by background task)
CREATE TABLE IF NOT EXISTS telemetry_daily (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT    NOT NULL,
    metric_type  TEXT    NOT NULL,
    metric_name  TEXT    NOT NULL,
    bucket_date  TEXT    NOT NULL,  -- ISO8601 date, e.g. '2026-04-04'
    total_count  INTEGER NOT NULL DEFAULT 0,
    total_errors INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms REAL,
    p95_latency_ms INTEGER,         -- approximate via histogram
    created_at   TEXT    NOT NULL,
    UNIQUE(tenant_id, metric_type, metric_name, bucket_date)
);
CREATE INDEX IF NOT EXISTS telemetry_daily_tenant_idx ON telemetry_daily(tenant_id, bucket_date DESC);
```

### 1.3 Aggregation Approach

**Real-time (inline):**
- On every tool call: increment count, update latency sums in `telemetry_metrics`
- Uses `INSERT ... ON CONFLICT DO UPDATE` for atomic upsert
- Latency computed as: `(time.time() - start_time) * 1000`

**Periodic (background):**
- Daily rollup task runs at midnight UTC
- Computes daily aggregates from hourly buckets
- Prunes hourly data older than 7 days
- Prunes daily data older than 90 days

**Helper function:**
```python
async def _record_metric(tenant_id: str, metric_type: str, metric_name: str, 
                         latency_ms: int = None, is_error: bool = False):
    """Record a telemetry metric. Called inline after each tool/REST call."""
    bucket_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
    now = _now()
    
    def _do_write():
        conn.execute("""
            INSERT INTO telemetry_metrics 
                (tenant_id, metric_type, metric_name, bucket_hour, count, sum_ms, min_ms, max_ms, error_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, metric_type, metric_name, bucket_hour) DO UPDATE SET
                count = count + 1,
                sum_ms = CASE WHEN excluded.sum_ms IS NOT NULL THEN COALESCE(sum_ms, 0) + excluded.sum_ms ELSE sum_ms END,
                min_ms = CASE WHEN excluded.min_ms IS NOT NULL THEN MIN(COALESCE(min_ms, excluded.min_ms), excluded.min_ms) ELSE min_ms END,
                max_ms = CASE WHEN excluded.max_ms IS NOT NULL THEN MAX(COALESCE(max_ms, 0), excluded.max_ms) ELSE max_ms END,
                error_count = error_count + excluded.error_count,
                updated_at = excluded.updated_at
        """, (tenant_id, metric_type, metric_name, bucket_hour, 
              latency_ms, latency_ms, latency_ms, 
              1 if is_error else 0, now, now))
        conn.commit()
    
    await _locked_write(_do_write)
```

### 1.4 MCP Tools

| Tool | Description |
|------|-------------|
| `get_telemetry_summary(tenant_id, hours=24)` | Return aggregated counts/latency for last N hours |
| `get_telemetry_by_tool(tool_name, tenant_id, hours=24)` | Per-tool breakdown |
| `list_top_tools(tenant_id, limit=10, hours=24)` | Most-called tools |
| `get_error_summary(tenant_id, hours=24)` | Error counts by tool |

### 1.5 REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/telemetry/summary` | Same as `get_telemetry_summary` |
| GET | `/api/v1/telemetry/tools/{tool_name}` | Per-tool metrics |
| GET | `/api/v1/telemetry/errors` | Error summary |
| GET | `/api/v1/telemetry/top` | Top tools |

---

## 2. Permissions System

### 2.1 Permission Model

**Roles:**
- `owner` — Full control: read, write, delete, manage members
- `contributor` — Read and write tasks; cannot delete or manage members
- `reader` — Read-only access to tasks

**Hierarchy:**
```
tenant (from Bearer token)
  └── project (string)
       └── members[] with roles
```

**Default behavior:**
- New projects: creator becomes `owner`
- Existing projects (migration): current tenant becomes `owner`
- No entry = no access (deny by default after migration)

### 2.2 Schema Design

```sql
-- Project-level access control
CREATE TABLE IF NOT EXISTS project_permissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project      TEXT    NOT NULL,
    tenant_id    TEXT    NOT NULL,
    role         TEXT    NOT NULL,  -- 'owner', 'contributor', 'reader'
    granted_by   TEXT,              -- tenant_id who granted this
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(project, tenant_id)
);
CREATE INDEX IF NOT EXISTS perm_project_idx ON project_permissions(project);
CREATE INDEX IF NOT EXISTS perm_tenant_idx ON project_permissions(tenant_id);

-- Valid roles constraint (enforced in code, not DB)
-- VALID_ROLES = {'owner', 'contributor', 'reader'}
```

### 2.3 Migration Strategy

**Phase 1: Schema addition (non-breaking)**
- Add `project_permissions` table
- No enforcement yet — all calls succeed as before

**Phase 2: Backfill (offline or lazy)**
- For each distinct `(project, tenant)` pair in existing tasks, insert `role='owner'`
- Run via admin tool: `migrate_permissions()`

**Phase 3: Enforcement**
- Enable `_check_project_access()` in all project-scoped tools
- Add `OPM_ENFORCE_PERMISSIONS=1` env var gate (default off for v0.3.0-beta)

### 2.4 Enforcement Implementation

```python
async def _check_project_access(project: str, required_role: str) -> str | None:
    """
    Check if current tenant has required_role on project.
    Returns None if allowed, error string if denied.
    
    Role hierarchy: owner > contributor > reader
    """
    if not os.environ.get("OPM_ENFORCE_PERMISSIONS"):
        return None  # Enforcement disabled
    
    tenant_id = _get_actor()
    if tenant_id == "system":
        return None  # System/unauthenticated mode — allow all
    
    row = await _db_execute_one(
        "SELECT role FROM project_permissions WHERE project = ? AND tenant_id = ?",
        (project, tenant_id)
    )
    if not row:
        return f"Error: access denied to project '{project}'"
    
    role = row["role"]
    hierarchy = {"owner": 3, "contributor": 2, "reader": 1}
    required_level = hierarchy.get(required_role, 0)
    actual_level = hierarchy.get(role, 0)
    
    if actual_level < required_level:
        return f"Error: insufficient permissions (have '{role}', need '{required_role}')"
    return None

# Usage in tools:
@mcp.tool()
async def create_task(..., project: str = "default", ...):
    err = await _check_project_access(project, "contributor")
    if err:
        return err
    # ... proceed with creation
```

**Where enforcement happens:**

| Operation | Required Role |
|-----------|---------------|
| `create_task`, `update_task`, `complete_task` | `contributor` |
| `delete_task` | `owner` |
| `get_task`, `list_tasks`, `search_tasks` | `reader` |
| `grant_project_access`, `revoke_project_access` | `owner` |
| `list_project_members` | `reader` |

### 2.5 MCP Tools

| Tool | Description |
|------|-------------|
| `grant_project_access(project, tenant_id, role, human_approval=True)` | Grant access |
| `revoke_project_access(project, tenant_id, human_approval=True)` | Remove access |
| `list_project_members(project)` | List members with roles |
| `get_my_projects()` | List projects caller has access to |
| `transfer_project_ownership(project, new_owner_tenant_id, human_approval=True)` | Transfer owner |
| `get_project_access(project, tenant_id)` | Check specific access |
| `migrate_permissions(human_approval=True)` | Admin: backfill existing projects |
| `set_permission_enforcement(enabled: bool, human_approval=True)` | Admin: toggle enforcement |

### 2.6 REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/projects/{project}/members` | List members |
| POST | `/api/v1/projects/{project}/members` | Grant access |
| DELETE | `/api/v1/projects/{project}/members/{tenant_id}` | Revoke access |
| GET | `/api/v1/projects/mine` | Caller's projects |

---

## 3. Implementation Order

```
v03-telemetry-schema    ← Trenton (DDL + migrations)
    ↓
v03-telemetry-api       ← Darlene (tools + REST) + Mobley (review)
    ↓
v03-permissions-schema  ← Trenton (DDL + migration helper)
    ↓
v03-permissions-enforcement ← Darlene (_check_project_access, 8 tools)
    ↓
v03-tests              ← Romero (unit + integration)
    ↓
v03-docs               ← Angela (wiki + README update)
```

---

## 4. Security Considerations

### Telemetry
- Metrics are tenant-scoped — tenants cannot see other tenants' metrics
- No PII stored in metrics (only tenant_id, tool names, counts)
- Daily pruning prevents unbounded growth

### Permissions
- Deny-by-default after migration (no row = no access)
- `human_approval=True` required on all grant/revoke/transfer operations
- Role changes logged in `activity_log` for audit
- `transfer_project_ownership` requires current owner

---

## 5. Backward Compatibility

- **Telemetry:** Additive only — no breaking changes
- **Permissions:** Gated by `OPM_ENFORCE_PERMISSIONS` env var
  - Default OFF for v0.3.0 release
  - Operators opt-in after running `migrate_permissions()`
  - Can disable mid-flight if issues arise

---

## Appendix: Key Patterns from squad-knowledge-mcp

1. **Factory pattern:** `create_server(db_path)` — all state captured in closures
2. **Async DB:** `asyncio.to_thread()` for all SQLite calls
3. **Write serialization:** `_locked_write()` with 30s timeout
4. **human_approval:** Boolean flag on destructive MCP tools
5. **Error strings:** Return `"Error: ..."` on failure, JSON on success
6. **Activity logging:** `_log(task_id, action, ...)` for audit trail

---

*Approved by Elliot — ready for Trenton (schema) and Darlene (implementation)*
