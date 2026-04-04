# Angela: v0.3.0 Documentation Complete

**Date:** 2026-04-05  
**Author:** Angela (DevRel & Docs)  
**Status:** COMPLETE

---

## Summary

Completed comprehensive documentation update for v0.3.0 release (telemetry + permissions).

## Changes Made

### pyproject.toml
- Version bump: `0.2.1` → `0.3.0`

### README.md
- **Added Telemetry section (v0.3.0):**
  - 4 MCP tools overview (summary, by_tool, top, error_summary)
  - What gets tracked: call counts, latency, error counts, REST metrics
  - Tenant-scoped visibility and <1ms overhead
  
- **Added Permissions section (v0.3.0):**
  - 8 MCP tools overview (grant, revoke, list members, get_my_projects, etc.)
  - Role hierarchy: owner > contributor > reader
  - Enforcement model (OFF by default for backward compatibility)
  - Migration path: backfill → grant access → enable enforcement

- **Updated REST API endpoints table:**
  - Added 4 telemetry endpoints (summary, tools, top, errors)
  - Added 4 permissions endpoints (mine, members GET/POST/DELETE)

### CHARTER.md
- Status line updated: "v0.3.0 in development — adding telemetry and permissions"
- Replaced "Planned (v0.3.0)" with "In Development (v0.3.0)"
- Added detailed feature descriptions with implementation highlights
- Last-updated timestamp: 2026-04-05

### angela/history.md
- Added v0.3.0 learnings section with key patterns and design insights

## Key Documentation Decisions

1. **Telemetry as automatic opt-in:** No setup required; metrics recorded on every call by default.
2. **Permissions as opt-in enforcement:** Backward compatible—OFF by default, enable after migration step.
3. **REST endpoint parity:** All 8 permission tools exposed via REST (following existing OPM pattern).
4. **Migration guidance:** Clear step-by-step path for existing users (upgrade → backfill → enable).
5. **Tool count update:** Documented 36 total tools (24 existing + 4 telemetry + 8 permissions).

## Next Steps

- Docs ready for v0.3.0 release
- No additional documentation work needed (implementation complete per Darlene)
- Knowledge server post will happen separately if needed
