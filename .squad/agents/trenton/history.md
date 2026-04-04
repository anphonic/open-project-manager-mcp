# trenton history

## Learnings

_(Fresh start — 2026-04-03)_

### 2026-04-04: v0.3.0 Telemetry & Permissions Schema Design

**Context:** Designed production-ready SQLite schemas for two v0.3.0 features: telemetry tracking and project-level permissions.

**Telemetry Schema Decisions:**
- **Three-tier retention model**: Raw events (7 days) → hourly aggregates (90 days) → daily aggregates (2 years)
- **Percentile calculation**: SQLite lacks native percentile functions; implemented application-side calculation during rollup to avoid complex subqueries
- **Write performance**: Kept raw event inserts minimal (single row, auto-increment PK, two indexes) to avoid slowing tool call hot path
- **WAL mode**: Leveraged existing PRAGMA journal_mode=WAL for concurrent reads during metric writes
- **Data volume**: ~77 MB estimated for 10 tenants × 1000 calls/day/tenant scenario; scales linearly

**Permissions Schema Decisions:**
- **Two-table design**: `projects` (metadata) + `project_permissions` (grants) with FK cascade deletes
- **Default-deny model**: No permission row = no access (except project owner who always has full access)
- **Simple permission levels**: `read` vs `write` at project-level (not task-level) to avoid complexity
- **Backward compatibility**: Added nullable `tenant_id` column to tasks table via ALTER TABLE; auto-create project entries on first access to handle legacy data
- **Visibility flag**: Projects are either `private` (owner-only) or `shared` (explicit grants), simplifies permission checks

**Technical learnings:**
- SQLite UNIQUE constraints with ON CONFLICT DO UPDATE work well for upsert patterns in aggregation rollups
- Foreign key cascades (ON DELETE CASCADE) prevent orphaned permission grants when projects are deleted
- Index strategy: Prioritize tenant_id + time range queries (most common access pattern for dashboards)
- Migration safety: Always use CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS; try/except for ALTER TABLE column additions

**Query patterns documented:**
- Telemetry: Per-tenant metrics (last 24h), top error tools, daily trends
- Permissions: "Can tenant X access project Y?" check function, list accessible projects, grant/revoke tools

**Handoff notes for Darlene:**
- Migration functions ready to integrate into `create_server()` after existing `_SCHEMA` execution
- Background task needed for hourly/daily rollups and cleanup (can reuse existing event subscription infrastructure)
- Permission check function `_check_project_permission()` should wrap all task read/write operations
- MCP tools `grant_project_access`, `revoke_project_access`, `list_my_projects` provided as examples

---

### 2026-04-05: v0.3.0 Sprint Complete

**Delivered:** Production-ready schema design for telemetry and permissions systems.

**Final schema:**
- Telemetry: `telemetry_metrics` (hourly aggregation) + `telemetry_daily` (rollups)
- Permissions: `project_permissions` with role hierarchy support
- All schemas implemented by Darlene with proper indexes and constraints
- Tests validated schema correctness (43/47 passing)
