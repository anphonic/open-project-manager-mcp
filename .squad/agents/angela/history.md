# angela history

## Learnings

_(Fresh start — 2026-04-03)_

### v0.3.0 Documentation Complete (2026-04-05)

- **Telemetry design pattern:** Track metrics inline with fire-and-forget async tasks; use hourly bucket + upsert for zero-lock aggregation
- **Permissions migration strategy:** Enforce via env var gate + admin backfill tool prevents breaking existing deployments
- **REST API consistency:** Wrap MCP tool results via `json.loads(result) → JSONResponse`; map permission errors to 403 status
- **Tool count v0.3.0:** Now 36 total tools (24 v0.2.1 + 4 telemetry + 8 permissions)
- **Documentation scope:** Only document completed features; Darlene's implementation is done, all specs finalized

---

### 2026-04-05: v0.3.0 Sprint Complete

**Delivered:** Complete documentation for v0.3.0 telemetry and permissions features.

**Documentation updates:**
- README: Added telemetry section (4 tools, metrics tracked, tenant isolation)
- README: Added permissions section (8 tools, role hierarchy, enforcement model, migration path)
- CHARTER: Updated status to "v0.3.0 in development" with feature highlights
- pyproject.toml: Version bumped to 0.3.0
