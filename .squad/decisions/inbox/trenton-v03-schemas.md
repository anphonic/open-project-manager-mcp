# Decision: v0.3.0 Telemetry & Permissions Schema Design

**Author:** Trenton (Shama Biswani)  
**Date:** 2026-04-04  
**Status:** Proposed (awaiting Darlene implementation)

---

## Summary

Designed production-ready SQLite schemas for v0.3.0 telemetry tracking and project-level permissions. Both schemas prioritize query performance, backward compatibility, and operational simplicity.

---

## Decisions

### 1. Telemetry: Three-Tier Retention Model

**Decision:** Use three tables with cascading aggregation:
- `telemetry_events` (raw): 7-day retention
- `telemetry_hourly` (aggregates): 90-day retention  
- `telemetry_daily` (long-term): 2-year retention

**Rationale:**
- Raw events generate high volume (1000s/day per tenant); keeping forever isn't feasible
- Hourly aggregates provide sufficient granularity for operational dashboards
- Daily aggregates enable long-term trend analysis without massive storage
- Progressive deletion reduces DB size while preserving historical insights

**Alternatives considered:**
- Single raw table with retention: Simple but loses historical trends
- Only daily aggregates: Insufficient granularity for recent troubleshooting
- External time-series DB: Adds operational complexity; SQLite is sufficient for v0.3.0 scale

---

### 2. Telemetry: Application-Side Percentile Calculation

**Decision:** Calculate p50/p95/p99 in Python during rollup, not via SQL.

**Rationale:**
- SQLite lacks native percentile functions (PERCENTILE_CONT, etc.)
- Pure-SQL percentile queries require nested subqueries that are slow and complex
- Rollup runs off hot path (processes events >1 hour old), so Python calculation is acceptable
- Fetch latencies into array, sort, index — simple and maintainable

**Trade-off:** Background task must fetch all latencies for each hour bucket (not just aggregates). For 1000 calls/hour, this is ~4KB of data per rollup — acceptable overhead.

---

### 3. Permissions: Project-Level Granularity

**Decision:** Permissions apply to entire projects, not individual tasks.

**Rationale:**
- Simplifies mental model: "Can I access project X?" is easier than "Can I access task Y in project X with tag Z?"
- Reduces DB queries: Single permission check per operation instead of per-task checks
- Matches common access patterns: Teams share entire projects, not individual tasks
- Task-level permissions can be added later if needed (v0.4.0+)

**Alternatives considered:**
- Task-level permissions: More flexible but adds significant complexity to every query
- Tag-based permissions: Interesting but unclear use case; project-level covers 90% of needs

---

### 4. Permissions: Default-Deny with Owner Bypass

**Decision:** 
- No explicit permission grant = no access (except project owner)
- Project owner always has full access (cannot be locked out)

**Rationale:**
- Default-deny is secure: Accidental over-sharing is prevented
- Owner bypass prevents lock-out scenarios (owner grants access then removes self)
- Clear privilege model: Only owners can grant/revoke permissions (no delegation)

**Security note:** Granted users cannot re-grant permissions to others (no transitive grants). Only the owner can manage the ACL.

---

### 5. Backward Compatibility: Lazy Project Creation

**Decision:** Auto-create `projects` table entries on first access for legacy project names in `tasks` table.

**Rationale:**
- Existing deployments have tasks with `project` column values but no `projects` table
- Migration can't infer owner from project name alone (no tenant_id on old tasks)
- Lazy creation: When tenant queries a project, create entry with that tenant as owner
- Conservative: Existing tasks remain accessible; no data loss

**Implementation:** `_ensure_project_exists()` helper called before first task read/write in a project.

---

### 6. Telemetry: Index Strategy

**Decision:** Index on `(tenant_id, created_at)` for events; `(tenant_id, hour_bucket DESC)` for hourly; `(tenant_id, day_bucket DESC)` for daily.

**Rationale:**
- Most common query: "Show metrics for tenant X in time range Y"
- Composite index on tenant + time enables efficient range scans
- DESC order on time optimizes "recent data first" queries (dashboards show latest)
- Separate `(created_at)` index on events table for rollup queries (no tenant filter)

**Trade-off:** More indexes = slower writes, but telemetry writes are already async and batched.

---

### 7. Permissions: Nullable tenant_id in tasks Table

**Decision:** Add `tenant_id TEXT` column to `tasks` table via `ALTER TABLE` (nullable for backward compat).

**Rationale:**
- Existing tasks have no tenant_id; can't backfill reliably
- Nullable column allows safe migration (no data loss)
- Permission checks treat NULL tenant_id as "legacy system task" (accessible by first querier)
- Future tasks will have tenant_id populated at creation time

**Alternative:** Could infer tenant_id from `activity_log.actor` field, but actor format is inconsistent across deployments.

---

## Integration Notes

**For Darlene (Implementation Lead):**

1. **Migration order:**
   - Add telemetry schema after existing `_SCHEMA` execution
   - Add permissions schema after telemetry
   - Add `tenant_id` column to tasks table last

2. **Background tasks needed:**
   - Hourly rollup: Every hour, aggregate `telemetry_events` → `telemetry_hourly`
   - Daily rollup: Every day at 00:00 UTC, aggregate `telemetry_hourly` → `telemetry_daily`
   - Cleanup: Every day, delete old raw events (>7d), old hourly (>90d), old daily (>2y)

3. **MCP context access:**
   - Extract `tenant_id` from `ctx.client_id` in MCP tool calls
   - Extract `tenant_id` from Bearer token in REST API calls (already implemented via `_verify_bearer`)

4. **Permission enforcement points:**
   - Wrap all `list_tasks`, `get_task`, `search_tasks` with read permission check
   - Wrap all `create_task`, `update_task`, `delete_task` with write permission check
   - Add new tools: `grant_project_access`, `revoke_project_access`, `list_my_projects`

5. **Performance targets:**
   - Raw event insert: <5ms (hot path)
   - Permission check: <10ms (cached in memory if possible)
   - Hourly rollup: <60s for 100K events
   - Dashboard query: <100ms for 30-day range

---

## Testing Requirements

**Telemetry:**
- [ ] Insert 10K raw events, verify indexes used (EXPLAIN QUERY PLAN)
- [ ] Run hourly rollup, confirm percentiles accurate (±5% of true values)
- [ ] Run cleanup, verify old data deleted
- [ ] Concurrent inserts don't block reads (WAL mode test)

**Permissions:**
- [ ] Create project as tenant A, verify only A can access
- [ ] Grant read to tenant B, verify B can list but not create tasks
- [ ] Grant write to tenant B, verify B can create/update tasks
- [ ] Revoke access from B, verify B loses access immediately
- [ ] Delete project, verify permission grants cascade-deleted
- [ ] Legacy tasks (no project entry) remain accessible

---

## Future Considerations (v0.4.0+)

- **Telemetry:** Push notifications for high error rates (integrate with event_subscriptions)
- **Telemetry:** Tenant quotas (alert/throttle when call_count exceeds limit)
- **Permissions:** Role-based access (admin/contributor/viewer instead of read/write)
- **Permissions:** Team-based grants (share with entire tenant group)
- **Permissions:** Task-level permissions (finer-grained control within projects)
- **Permissions:** Public projects (read-only access for all tenants)

---

## Files Delivered

- `J:\Coding\open-project-manager-mcp\.squad\agents\trenton\v03-telemetry-schema.md`
- `J:\Coding\open-project-manager-mcp\.squad\agents\trenton\v03-permissions-schema.md`
- `J:\Coding\open-project-manager-mcp\.squad\agents\trenton\history.md` (updated)
- `J:\Coding\open-project-manager-mcp\.squad\decisions\inbox\trenton-v03-schemas.md` (this file)

---

**Next Steps:**
1. Darlene reviews schemas and integrates into `server.py`
2. Mobley adds telemetry collection hooks to MCP tool execution
3. Elliot reviews public API changes (new permission tools)
4. Scribe updates documentation with new tools and migration guide
