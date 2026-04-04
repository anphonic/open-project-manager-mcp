# v0.3.0 Implementation Decision

**Author:** Darlene (Backend Dev)  
**Date:** 2026-04-04  
**Status:** Implemented

---

## Summary

Implemented v0.3.0 telemetry and permissions features in server.py. Both features are production-ready and backward-compatible.

---

## Implementation Details

### Telemetry

**Schema:**
- `telemetry_metrics` — hourly buckets tracking per-tenant, per-tool usage
- `telemetry_daily` — daily rollups for long-term retention
- Both tables use `UNIQUE(tenant_id, metric_type, metric_name, bucket_hour/date)` for UPSERT-based aggregation

**Helper Function:**
- `_record_metric(tenant_id, metric_type, metric_name, latency_ms, is_error)` — fire-and-forget async recording
- Uses `asyncio.create_task(asyncio.to_thread(...))` to avoid blocking caller
- Silent failure on DB errors (telemetry should never break operations)

**Instrumented Tools:**
1. `create_task` — tracks creation latency and errors
2. `update_task` — tracks update latency and errors
3. `get_task` — tracks read latency
4. `list_tasks` — tracks query latency
5. `search_tasks` — tracks FTS query latency

**MCP Tools:**
1. `get_telemetry_summary(hours)` — aggregate metrics for calling tenant
2. `get_telemetry_by_tool(tool_name, hours)` — hourly breakdown for specific tool
3. `list_top_tools(limit, hours)` — most-called tools
4. `get_error_summary(hours)` — error counts by tool

**REST Endpoints:**
- `GET /telemetry/summary?hours=24`
- `GET /telemetry/tools/{tool_name}?hours=24`
- `GET /telemetry/top?limit=10&hours=24`
- `GET /telemetry/errors?hours=24`

---

### Permissions

**Schema:**
- `project_permissions` — stores (project, tenant_id, role, granted_by) tuples
- Role hierarchy: owner > contributor > reader
- `UNIQUE(project, tenant_id)` constraint prevents duplicate grants

**Helper Function:**
- `_check_project_access(project, required_role)` — returns `None` if allowed, error string if denied
- Checks role hierarchy: `_ROLE_HIERARCHY = {"owner": 3, "contributor": 2, "reader": 1}`
- Disabled by default — requires `OPM_ENFORCE_PERMISSIONS=1` env var

**Enforcement Points:**
1. `create_task` — requires contributor role
2. `update_task` — requires contributor role
3. `complete_task` — requires contributor role
4. `delete_task` — requires owner role
5. `get_task` — requires reader role
6. `list_tasks` — requires reader role (if project specified)

**MCP Tools:**
1. `grant_project_access(project, target_tenant_id, role, human_approval)` — grant access (requires owner)
2. `revoke_project_access(project, target_tenant_id, human_approval)` — revoke access (requires owner)
3. `list_project_permissions(project)` — list all members (requires reader)
4. `get_my_permissions()` — list caller's projects
5. `transfer_project_ownership(project, new_owner_tenant_id, human_approval)` — transfer ownership
6. `get_project_access(project, target_tenant_id)` — check specific tenant's access
7. `migrate_permissions(human_approval)` — backfill permissions from activity_log
8. `set_permission_enforcement(enabled, human_approval)` — toggle runtime enforcement

---

## Design Decisions

### Why Fire-and-Forget Telemetry?

Telemetry should **never** slow down or break core operations. Using `asyncio.create_task(asyncio.to_thread(...))` ensures:
- No blocking on metric writes
- Silent failure on DB errors
- Minimal performance impact

Trade-off: Small chance of losing metrics if server crashes during write.

### Why Opt-In Permissions?

Permissions enforcement disabled by default (`OPM_ENFORCE_PERMISSIONS=1` to enable) because:
- Existing deployments have no permission entries
- Migration path requires manual `migrate_permissions()` call
- Gradual rollout: enable per-tenant or per-deployment

Trade-off: Security relies on admins explicitly enabling enforcement.

### Why Role Hierarchy?

Three roles (owner/contributor/reader) instead of binary (read/write) because:
- Clearer intent: "reader" vs "write access"
- Future expansion: can add more roles (admin, viewer, etc.)
- Delete requires owner (higher bar than update)

Trade-off: More complex permission checks (need hierarchy comparison).

### Why Hourly Buckets?

Telemetry uses hourly aggregation instead of raw events because:
- Lower storage overhead (1 row per hour vs N rows per hour)
- Faster queries (aggregates already computed)
- Supports retention policies (daily rollups, eventual deletion)

Trade-off: Cannot query sub-hour granularity.

---

## Migration Path

### Existing Deployments

1. **Schema Migration:** Tables created automatically via `CREATE TABLE IF NOT EXISTS` on server start
2. **Permissions Backfill:** Run `migrate_permissions(human_approval=True)` to grant owners based on `activity_log.actor`
3. **Enable Enforcement:** Set `OPM_ENFORCE_PERMISSIONS=1` env var and restart server

### New Deployments

1. Permissions disabled by default — tasks work without permission entries
2. Telemetry starts recording immediately (no setup needed)
3. Admin can enable permissions when ready

---

## Testing Recommendations

1. **Telemetry:**
   - Create 100+ tasks and verify `telemetry_metrics` entries
   - Query `get_telemetry_summary()` and validate counts match
   - Check latency values are reasonable (< 1000ms for normal ops)

2. **Permissions:**
   - Grant access to tenant B, verify B can list tasks
   - Revoke access from tenant B, verify 403 error
   - Transfer ownership, verify previous owner can still contribute
   - Run `migrate_permissions()` on existing DB, verify backfill works

3. **REST API:**
   - `curl -H "Authorization: Bearer TOKEN" http://host:8765/api/v1/telemetry/summary`
   - Verify all 4 telemetry endpoints return valid JSON

---

## Known Limitations

1. **Telemetry:**
   - No percentile calculation (p50, p95, p99) in current implementation
   - No automatic rollup jobs (requires external cron or background task)
   - No retention cleanup (old metrics accumulate indefinitely)

2. **Permissions:**
   - No task-level permissions (only project-level)
   - No team/group grants (only individual tenants)
   - No time-limited access (permanent until revoked)

3. **Both:**
   - No audit log for permission changes
   - No notifications for access grants/revokes

---

## Next Steps (Post-v0.3.0)

1. Add background task for hourly → daily rollups
2. Implement retention policies (delete old metrics)
3. Add audit logging for permission changes
4. Support team-based permissions (grant access to entire squad)
5. Add telemetry push to event bus for real-time monitoring

---

## References

- Elliot's v0.3.0 architecture: `.squad/agents/elliot/darlene-brief-v03.md`
- Trenton's schema DDL: `.squad/agents/trenton/v03-telemetry-schema.md`, `.squad/agents/trenton/v03-permissions-schema.md`
- Implementation: `src/open_project_manager_mcp/server.py`
