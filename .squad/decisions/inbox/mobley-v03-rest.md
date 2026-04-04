# Decision: v0.3.0 REST API Design & Permissions Exposure

**Author:** Mobley  
**Date:** 2026-04-04  
**Status:** APPROVED

---

## Context

OPM v0.3.0 adds two major features with REST API exposure:
1. **Telemetry** — usage metrics (4 endpoints)
2. **Permissions** — project-level ACL (4–6 endpoints)

Reviewed Elliot's architecture and Darlene implementation brief to finalize REST endpoint design.

---

## Decision 1: Expose Permissions via REST API

**Decision:** ✅ **YES** — expose project permissions management via REST endpoints.

**Rationale:**
- External systems require programmatic access (CI/CD pipelines, admin dashboards, SSO integrations)
- Enables webhook-driven team lifecycle automation (e.g., on employee offboarding → revoke all project access)
- Consistent with OPM pattern of exposing all core MCP tools via REST
- Risks mitigated by:
  - `human_approval=true` requirement on all destructive operations
  - Activity logging for audit trail (recommended addition)
  - Role-based enforcement prevents privilege escalation

**Exceptions:**
- Admin tools (`migrate_permissions`, `set_permission_enforcement`) remain MCP-only
- MCP's approval flow is stronger for system-wide operations

---

## Decision 2: Add Missing Permission Endpoints

**Decision:** Recommend adding 2 endpoints not in Elliot's original spec:

1. **POST /api/v1/projects/{project}/transfer** — Transfer project ownership
   - **Rationale:** Common automation use case (team handoff, reorganization)
   - **Alternative:** Force users to use MCP for ownership transfers
   - **Choice:** Add to REST for completeness

2. **GET /api/v1/projects/{project}/members/{tenant_id}** — Check specific access
   - **Rationale:** External authorization layers need single-tenant access checks
   - **Alternative:** Clients filter `GET /members` response (wasteful)
   - **Choice:** Add dedicated endpoint

**Status:** Recommended in spec, implementation optional for v0.3.0.

---

## Decision 3: Telemetry Query Parameter Bounds

**Decision:** Add input validation for `hours` and `limit` parameters:

| Parameter | Min | Max | Default | Error on out-of-bounds |
|-----------|-----|-----|---------|------------------------|
| `hours` | 1 | 720 | 24 | 400 Bad Request |
| `limit` | 1 | 100 | 10 | 400 Bad Request |

**Rationale:**
- `hours=720` (30 days) prevents excessive DB scans (hourly buckets table)
- `limit=100` prevents memory exhaustion on large result sets
- Matches existing OPM pattern of capping `_MAX_LIMIT = 500` for task lists

**Implementation:** Validate in REST endpoint handlers before calling MCP tools.

---

## Decision 4: Tenant Isolation in Telemetry

**Decision:** Add explicit `actor == "system"` check in telemetry endpoints.

**Issue:** If OPM runs in unauthenticated mode (`actor = "system"`), telemetry queries would aggregate ALL tenants' metrics (data leakage).

**Current mitigation:** All queries filter by `tenant_id = _get_actor()`, but `"system"` is not a real tenant.

**Enhanced mitigation:**
```python
actor = _get_actor()
if actor == "system":
    return JSONResponse({"error": "Error: telemetry requires authentication"}, status_code=401)
```

**Rationale:** Defense-in-depth — explicit auth check even if `_check_auth()` passes in unauthenticated mode.

---

## Decision 5: Error Response Consistency

**Decision:** Follow existing OPM pattern for REST error responses:

1. **MCP tools:** Return error strings starting with `"Error:"`
2. **REST endpoints:** Wrap in JSON `{"error": "Error: ..."}`
3. **Status codes:** Use `_error_status()` helper for consistent mapping

**Status code mappings:**
- "not found" → 404
- "already exists" → 409
- "database error" → 500
- "access denied" OR "insufficient permissions" → 403
- All other errors → 400

**Rationale:** Maintains backward compatibility with existing OPM v0.2.x REST API patterns.

---

## Decision 6: Integration Testing Approach

**Decision:** Use curl-based smoke tests, not pytest.

**Rationale:**
- REST endpoints are thin wrappers around MCP tools (minimal logic)
- Unit tests for MCP tools cover business logic (Romero's responsibility)
- Integration tests verify HTTP layer (auth, routing, error mapping)
- curl tests are simpler to run in CI/CD without Python dependencies
- Bash test runner provides immediate feedback

**Deliverable:** `.squad/agents/mobley/v03-integration-tests.md` with 40+ test cases.

---

## Decision 7: Recommended Additions (Future)

**Not blocking for v0.3.0:**

1. **Rate limiting:**
   - Permission changes: 10 req/min per tenant
   - Telemetry reads: 60 req/min per tenant
   - **Defer to v0.4.0:** Requires new infra (no rate limiting exists in OPM today)

2. **Pagination for telemetry:**
   - Current design: max 720 hourly buckets (30 days × 24 hours)
   - **Defer to v0.4.0:** Manageable size for v0.3.0

3. **Activity logging for permissions:**
   - Recommend adding `_log()` calls in permission tools
   - **Defer to Darlene:** May already be in implementation plan

---

## Artifacts

1. **REST API Spec:** `.squad/agents/mobley/v03-rest-spec.md` (full endpoint documentation)
2. **Integration Tests:** `.squad/agents/mobley/v03-integration-tests.md` (curl-based test suite)
3. **This decision:** `.squad/decisions/inbox/mobley-v03-rest.md`

---

## Next Steps

1. **Darlene:** Implement REST endpoints per spec
2. **Romero:** Run integration tests after implementation
3. **Squad:** Review decision, move to `.squad/decisions.md` if approved

---

**Approved by:** Mobley  
**Implementation owner:** Darlene
