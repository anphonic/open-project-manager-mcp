# mobley history

## Learnings

_(Fresh start — 2026-04-03)_

### 2026-04-04: v0.3.0 REST API Design Review

**Context:** Reviewed Elliot's proposed REST endpoints for telemetry and permissions systems.

**Key findings:**

1. **Error response consistency:** OPM uses dual error pattern:
   - MCP tools return error strings: `"Error: message"`
   - REST wraps in JSON: `{"error": "Error: message"}`
   - Status code mapping via `_error_status()` helper (404 for "not found", 409 for "already exists", 500 for "database error", 403 for permissions, 400 default)

2. **Permissions REST exposure decision:** Recommended YES for REST API exposure because:
   - External systems (CI/CD, admin dashboards, auth providers) need programmatic access
   - Enables webhook integrations for team lifecycle management
   - `human_approval=true` requirement provides audit trail
   - Risk mitigated by activity logging and role-based enforcement

3. **Missing endpoints identified:**
   - Elliot's spec had 8 MCP tools but only 4 REST endpoints for permissions
   - Recommended adding: `POST /transfer` (ownership transfer) and `GET /{tenant_id}` (specific access check)
   - Admin tools (`migrate_permissions`, `set_permission_enforcement`) should remain MCP-only for stronger approval flow

4. **Tenant isolation pattern:** All telemetry queries use `tenant_id = _get_actor()` filter — prevents cross-tenant leakage. Added recommendation to explicitly check for `actor == "system"` in telemetry endpoints to prevent unauthenticated mode from leaking all metrics.

5. **Input validation gaps:** Elliot's spec didn't specify bounds on `hours` and `limit` params. Recommended:
   - `hours`: 1–720 (prevent excessive DB scans)
   - `limit`: 1–100 (prevent memory exhaustion)
   - Return 400 on out-of-bounds

**Integration test strategy:**
- curl-based smoke tests (no pytest overhead for REST endpoints)
- Separate tenant isolation testing (alpha/beta tokens)
- Error path coverage (401, 403, 404, 409, 500)
- Bash test runner for CI/CD integration

**Handoff to Darlene:** Spec includes implementation notes for consistent error handling and route registration patterns.

---

### 2026-04-05: v0.3.0 Sprint Complete

**Delivered:** REST API design for telemetry and permissions endpoints.

**Final API:**
- 4 telemetry REST endpoints: /summary, /tools/{name}, /top, /errors
- 4 permissions REST endpoints: /mine, /members (GET/POST/DELETE)
- Input validation bounds (hours: 1-720, limit: 1-100)
- Tenant isolation and auth checks for all endpoints
