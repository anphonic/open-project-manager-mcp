# darlene history

## Learnings

_(Fresh start — 2026-04-03)_

### 2026-04-04: v0.3.0 Telemetry and Permissions Implementation

Successfully implemented two major features for v0.3.0:

**Telemetry Feature:**
- Added `telemetry_metrics` and `telemetry_daily` tables to `_SCHEMA` with proper indexes
- Implemented `_record_metric()` fire-and-forget async helper for performance
- Instrumented 5 high-traffic tools: `create_task`, `update_task`, `get_task`, `list_tasks`, `search_tasks`
- Added 4 new MCP tools: `get_telemetry_summary`, `get_telemetry_by_tool`, `list_top_tools`, `get_error_summary`
- Added 4 REST endpoints: `/telemetry/summary`, `/telemetry/tools/{tool_name}`, `/telemetry/top`, `/telemetry/errors`
- Metrics track latency (min/max/sum), call counts, and error rates per tenant per hour

**Permissions Feature:**
- Added `project_permissions` table with project/tenant_id unique constraint
- Implemented `_check_project_access(project, required_role)` helper with role hierarchy (owner > contributor > reader)
- Enforced permissions on 6 tools: `create_task`, `update_task`, `complete_task`, `delete_task`, `get_task`, `list_tasks`
- Enforcement gated by `OPM_ENFORCE_PERMISSIONS` env var (disabled by default for v0.3.0)
- Added 8 new MCP tools: `grant_project_access`, `revoke_project_access`, `list_project_permissions`, `get_my_permissions`, `transfer_project_ownership`, `get_project_access`, `migrate_permissions`, `set_permission_enforcement`
- All dangerous operations require `human_approval=True` to prevent accidental changes

**Key Learnings:**
- Fire-and-forget telemetry using `asyncio.create_task(asyncio.to_thread(...))` prevents blocking hot path
- Permission checks happen before validation to fail fast on access denial
- Early returns with telemetry recording on error paths ensure all call paths are tracked
- Role hierarchy pattern (`_ROLE_HIERARCHY` dict) makes permission level comparisons clean
- Using UPSERT (`ON CONFLICT ... DO UPDATE`) for telemetry keeps hourly aggregation idempotent
- Permissions feature is opt-in (disabled by default) for smooth migration path

**Testing Notes:**
- server.py compiles without syntax errors
- All new tables use `CREATE TABLE IF NOT EXISTS` for safe migrations
- Backward compatible: existing projects without permission entries work in unauthenticated mode

---

### 2026-04-05: v0.3.0 Sprint Complete

**Delivered:** Core implementation of telemetry and permissions features, passed 43/47 tests (91.5%).

**Final outcome:**
- 4 telemetry tools + 4 REST endpoints
- 8 permissions tools + 4 REST endpoints  
- 4 security fixes applied (DoS prevention, input validation, permission bypass fix)
- Migration path complete with backfill tooling

