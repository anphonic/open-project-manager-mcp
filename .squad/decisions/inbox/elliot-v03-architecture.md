# Architecture Decision: OPM v0.3.0 Telemetry & Permissions

**Author:** Elliot  
**Date:** 2026-04-04  
**Status:** APPROVED

---

## Context

v0.3.0 adds two major features to open-project-manager-mcp:

1. **Telemetry** — Per-tenant usage metrics for capacity planning and debugging
2. **Permissions** — Project-level ACL to support multi-squad environments

Both were requested to support production deployment serving 5+ squads on skitterphuger.

## Decision

### Telemetry

- **Aggregation model:** Hourly buckets with daily rollups
- **Storage:** Two new tables (`telemetry_metrics`, `telemetry_daily`)
- **Recording:** Inline, fire-and-forget via `asyncio.create_task()`
- **Retention:** 7 days hourly, 90 days daily
- **Exposure:** 4 MCP tools + 4 REST endpoints

### Permissions

- **Model:** Owner/Contributor/Reader roles per project
- **Storage:** `project_permissions` table with UNIQUE(project, tenant_id)
- **Enforcement:** Gated by `OPM_ENFORCE_PERMISSIONS` env var (default OFF)
- **Migration:** `migrate_permissions()` tool backfills existing projects
- **Exposure:** 8 MCP tools + 4 REST endpoints

## Rationale

- **Hourly buckets** balance granularity vs storage cost
- **Fire-and-forget telemetry** avoids blocking tool calls
- **Deny-by-default permissions** is secure but requires explicit migration
- **Env var gate** allows gradual rollout and rollback

## Consequences

- Schema is additive — no breaking changes to existing tables
- Permissions enforcement can break existing scripts if enabled without migration
- Telemetry adds minimal write overhead (~1ms per tool call)

## Implementation

See briefs:
- `.squad/agents/elliot/trenton-brief-v03.md` (schema DDL)
- `.squad/agents/elliot/darlene-brief-v03.md` (tool implementation)

Task order:
1. `v03-telemetry-schema` (Trenton)
2. `v03-telemetry-api` (Darlene)
3. `v03-permissions-schema` (Trenton)
4. `v03-permissions-enforcement` (Darlene)
5. `v03-tests` (Romero)
6. `v03-docs` (Angela)

---

*Approved by Elliot — ready for implementation*
