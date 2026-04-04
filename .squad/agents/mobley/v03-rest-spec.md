# OPM v0.3.0 REST API Specification

**Author:** Mobley (Integration & External Systems Specialist)  
**Date:** 2026-04-04  
**Status:** APPROVED

---

## Overview

This document specifies the REST API endpoints for OPM v0.3.0, covering:
1. **Telemetry** — usage metrics and error tracking (4 endpoints)
2. **Permissions** — project-level access control (4 endpoints)

All endpoints follow existing OPM REST patterns:
- Bearer token authentication via `Authorization: Bearer <token>`
- Consistent error response shape: `{"error": "Error: message"}`
- Status code mapping via `_error_status()` helper
- Behind `--rest-api` CLI flag

---

## 1. Authentication & Authorization

**Method:** Bearer token in `Authorization` header  
**Validation:** Handled by `_check_auth(request)` helper  
**Tenant isolation:** All queries automatically scoped to calling tenant via `_get_actor()`

**Error responses:**
- `401 Unauthorized` — missing/invalid token
- `403 Forbidden` — insufficient permissions (permissions system only)
- `404 Not Found` — resource doesn't exist
- `409 Conflict` — resource already exists
- `500 Internal Server Error` — database/system error

---

## 2. Error Response Format

All error responses follow existing OPM pattern:

```json
{
  "error": "Error: descriptive message"
}
```

**Status code mapping** (from `_error_status()` in server.py):
- "not found" → 404
- "already exists" → 409
- "database error" → 500
- "access denied" or "insufficient permissions" → 403
- All others → 400

**Consistency note:** MCP tools return error strings starting with `"Error:"`, REST endpoints wrap these in `{"error": "..."}` JSON objects.

---

## 3. Telemetry Endpoints

### 3.1 GET /api/v1/telemetry/summary

**Description:** Aggregated metrics for calling tenant over last N hours  
**Auth:** Required (Bearer token)  
**Method:** GET

**Query Parameters:**
- `hours` (optional, integer, default: 24) — lookback window

**Response (200 OK):**
```json
{
  "hours": 24,
  "metrics": [
    {
      "metric_type": "tool_call",
      "metric_name": "create_task",
      "total_calls": 145,
      "total_errors": 3,
      "avg_latency_ms": 24.67
    },
    {
      "metric_type": "rest_call",
      "metric_name": "/api/v1/tasks",
      "total_calls": 89,
      "total_errors": 1,
      "avg_latency_ms": 12.34
    }
  ]
}
```

**Error responses:**
- `401 Unauthorized` — invalid/missing token
- `500 Internal Server Error` — database error

---

### 3.2 GET /api/v1/telemetry/tools/{tool_name}

**Description:** Per-tool metrics breakdown with hourly buckets  
**Auth:** Required (Bearer token)  
**Method:** GET

**Path Parameters:**
- `tool_name` (required, string) — tool name (e.g., "create_task")

**Query Parameters:**
- `hours` (optional, integer, default: 24) — lookback window

**Response (200 OK):**
```json
{
  "tool": "create_task",
  "hours": 24,
  "buckets": [
    {
      "bucket_hour": "2026-04-04T15:00:00Z",
      "count": 23,
      "error_count": 1,
      "avg_latency_ms": 26.5,
      "min_ms": 12,
      "max_ms": 142
    },
    {
      "bucket_hour": "2026-04-04T14:00:00Z",
      "count": 18,
      "error_count": 0,
      "avg_latency_ms": 22.1,
      "min_ms": 15,
      "max_ms": 58
    }
  ]
}
```

**Error responses:**
- `401 Unauthorized` — invalid/missing token
- `500 Internal Server Error` — database error

**Note:** Empty tool_name or non-existent tools return empty buckets array (not an error).

---

### 3.3 GET /api/v1/telemetry/top

**Description:** Most-called tools for calling tenant  
**Auth:** Required (Bearer token)  
**Method:** GET

**Query Parameters:**
- `limit` (optional, integer, default: 10) — number of tools to return (max: 100)
- `hours` (optional, integer, default: 24) — lookback window

**Response (200 OK):**
```json
{
  "hours": 24,
  "top_tools": [
    {
      "metric_name": "list_tasks",
      "total_calls": 342
    },
    {
      "metric_name": "create_task",
      "total_calls": 145
    },
    {
      "metric_name": "update_task",
      "total_calls": 89
    }
  ]
}
```

**Error responses:**
- `401 Unauthorized` — invalid/missing token
- `500 Internal Server Error` — database error

---

### 3.4 GET /api/v1/telemetry/errors

**Description:** Error summary by tool for calling tenant  
**Auth:** Required (Bearer token)  
**Method:** GET

**Query Parameters:**
- `hours` (optional, integer, default: 24) — lookback window

**Response (200 OK):**
```json
{
  "hours": 24,
  "errors": [
    {
      "metric_name": "create_task",
      "total_errors": 3,
      "total_calls": 145
    },
    {
      "metric_name": "update_task",
      "total_errors": 1,
      "total_calls": 89
    }
  ]
}
```

**Notes:**
- Only returns tools with `total_errors > 0`
- If no errors in the time window, `errors` array is empty

**Error responses:**
- `401 Unauthorized` — invalid/missing token
- `500 Internal Server Error` — database error

---

## 4. Permissions Endpoints

### 4.1 GET /api/v1/projects/{project}/members

**Description:** List all members with access to a project  
**Auth:** Required (Bearer token + `reader` role or higher)  
**Method:** GET

**Path Parameters:**
- `project` (required, string) — project name

**Response (200 OK):**
```json
{
  "project": "acme-web",
  "members": [
    {
      "tenant_id": "squad-alpha",
      "role": "owner",
      "granted_by": "squad-alpha",
      "created_at": "2026-04-01T10:00:00Z"
    },
    {
      "tenant_id": "squad-beta",
      "role": "contributor",
      "granted_by": "squad-alpha",
      "created_at": "2026-04-02T14:30:00Z"
    }
  ]
}
```

**Error responses:**
- `401 Unauthorized` — invalid/missing token
- `403 Forbidden` — caller lacks `reader` access to project
- `500 Internal Server Error` — database error

---

### 4.2 POST /api/v1/projects/{project}/members

**Description:** Grant access to a project (requires `owner` role)  
**Auth:** Required (Bearer token + `owner` role)  
**Method:** POST

**Path Parameters:**
- `project` (required, string) — project name

**Request Body:**
```json
{
  "tenant_id": "squad-gamma",
  "role": "contributor",
  "human_approval": true
}
```

**Required fields:**
- `tenant_id` (string) — tenant to grant access to
- `role` (string) — one of: "owner", "contributor", "reader"
- `human_approval` (boolean) — must be `true`

**Response (200 OK or 201 Created):**
```json
{
  "project": "acme-web",
  "tenant_id": "squad-gamma",
  "role": "contributor",
  "granted_by": "squad-alpha"
}
```

**Error responses:**
- `400 Bad Request` — invalid role or `human_approval != true`
- `401 Unauthorized` — invalid/missing token
- `403 Forbidden` — caller lacks `owner` access
- `500 Internal Server Error` — database error

**Notes:**
- Upserting existing access updates the role
- `human_approval` is required per OPM destructive operation policy

---

### 4.3 DELETE /api/v1/projects/{project}/members/{tenant_id}

**Description:** Revoke a tenant's access to a project (requires `owner` role)  
**Auth:** Required (Bearer token + `owner` role)  
**Method:** DELETE

**Path Parameters:**
- `project` (required, string) — project name
- `tenant_id` (required, string) — tenant to revoke access from

**Query Parameters:**
- `confirm` (required, boolean) — must be `true`

**Response (204 No Content):** Empty body

**Error responses:**
- `400 Bad Request` — `confirm=true` missing OR attempting to revoke own access
- `401 Unauthorized` — invalid/missing token
- `403 Forbidden` — caller lacks `owner` access
- `404 Not Found` — no access entry found for target tenant
- `500 Internal Server Error` — database error

**Notes:**
- Cannot revoke own access (use `transfer_project_ownership` instead)
- `confirm=true` query param mimics existing OPM destructive operation pattern

---

### 4.4 GET /api/v1/projects/mine

**Description:** List all projects the calling tenant has access to  
**Auth:** Required (Bearer token)  
**Method:** GET

**Response (200 OK):**
```json
{
  "tenant_id": "squad-alpha",
  "projects": [
    {
      "project": "acme-web",
      "role": "owner",
      "created_at": "2026-04-01T10:00:00Z"
    },
    {
      "project": "acme-api",
      "role": "contributor",
      "created_at": "2026-04-02T12:00:00Z"
    }
  ]
}
```

**Error responses:**
- `401 Unauthorized` — invalid/missing token
- `500 Internal Server Error` — database error

**Notes:**
- Returns all projects where caller has any role (owner/contributor/reader)
- If caller has no access to any projects, `projects` array is empty (not an error)

---

## 5. Design Issues & Recommendations

### 5.1 Permissions REST Exposure — RECOMMENDATION: YES

**Issue:** Should permissions be exposed via REST at all?

**Analysis:**
- ✅ **Pro:** External systems need programmatic access to grant/revoke permissions (e.g., CI/CD pipelines, external auth providers, admin dashboards)
- ✅ **Pro:** REST endpoints enable webhook-based integrations (e.g., on team member removal, revoke OPM access)
- ✅ **Pro:** Consistent with existing OPM pattern of exposing all MCP tools via REST
- ⚠️ **Con:** Increases attack surface for privilege escalation
- ⚠️ **Con:** `human_approval` over REST is weaker than MCP approval flow

**Mitigation:**
- Keep `human_approval=true` requirement in REST handlers (body param)
- Add rate limiting on permission-change endpoints (future enhancement)
- Audit all permission changes via `activity_log` (already in MCP design)

**Recommendation:** ✅ **YES, expose via REST** — benefits outweigh risks with proper auditing.

---

### 5.2 Missing Endpoints

**Issue:** Elliot's architecture doc lists 8 MCP tools but only specifies 4 REST endpoints.

**Missing REST equivalents:**
1. `transfer_project_ownership` — not in REST spec
2. `get_project_access` — not in REST spec
3. `migrate_permissions` — not in REST spec (ADMIN only)
4. `set_permission_enforcement` — not in REST spec (ADMIN only)

**Recommendation:**
- **SKIP admin tools** (`migrate_permissions`, `set_permission_enforcement`) in REST API — these should only be accessible via authenticated MCP (safer approval flow)
- **ADD:**
  - `POST /api/v1/projects/{project}/transfer` — transfer ownership
  - `GET /api/v1/projects/{project}/members/{tenant_id}` — check specific access

**Rationale:** Transfer ownership is a common external integration use case (e.g., team handoff automation). Specific access checks enable external authorization layers.

---

### 5.3 Telemetry Query Parameters Validation

**Issue:** No input validation specified for `hours` and `limit` parameters.

**Recommendation:**
- Add bounds checking:
  - `hours`: min=1, max=720 (30 days) — prevents excessive DB scans
  - `limit`: min=1, max=100 — prevents memory exhaustion
- Return `400 Bad Request` if out of bounds

**Implementation:** Add validation in each endpoint before calling MCP tool.

---

### 5.4 Telemetry Cross-Tenant Isolation

**Issue:** Telemetry endpoints must prevent cross-tenant data leakage.

**Current design:** All queries filter by `tenant_id = _get_actor()` — correct ✅

**Risk scenario:** If `_get_actor()` returns `"system"` (unauthenticated mode), telemetry queries would leak all tenants' data.

**Recommendation:**
- Add explicit check in telemetry endpoints:
  ```python
  actor = _get_actor()
  if actor == "system":
      return JSONResponse({"error": "Error: telemetry requires authentication"}, status_code=401)
  ```

---

### 5.5 Rate Limiting (Future Enhancement)

**Issue:** No rate limiting on permission-change or telemetry endpoints.

**Recommendation:** Not blocking for v0.3.0, but document for v0.4.0:
- Permission changes: 10 req/min per tenant
- Telemetry reads: 60 req/min per tenant

**Note:** Existing OPM has no rate limiting — this would be new infra. Defer to v0.4.0.

---

## 6. Endpoint Summary Table

| Method | Path | Auth | Purpose | Status Codes |
|--------|------|------|---------|--------------|
| GET | `/api/v1/telemetry/summary` | Required | Aggregated metrics | 200, 401, 500 |
| GET | `/api/v1/telemetry/tools/{tool_name}` | Required | Per-tool hourly buckets | 200, 401, 500 |
| GET | `/api/v1/telemetry/top` | Required | Most-called tools | 200, 401, 500 |
| GET | `/api/v1/telemetry/errors` | Required | Error summary | 200, 401, 500 |
| GET | `/api/v1/projects/{project}/members` | Required (reader+) | List project members | 200, 401, 403, 500 |
| POST | `/api/v1/projects/{project}/members` | Required (owner) | Grant access | 200, 400, 401, 403, 500 |
| DELETE | `/api/v1/projects/{project}/members/{tenant_id}` | Required (owner) | Revoke access | 204, 400, 401, 403, 404, 500 |
| GET | `/api/v1/projects/mine` | Required | Caller's projects | 200, 401, 500 |

**Recommended additions:**
| Method | Path | Auth | Purpose | Status Codes |
|--------|------|------|---------|--------------|
| POST | `/api/v1/projects/{project}/transfer` | Required (owner) | Transfer ownership | 200, 400, 401, 403, 500 |
| GET | `/api/v1/projects/{project}/members/{tenant_id}` | Required (reader+) | Check specific access | 200, 401, 403, 500 |

---

## 7. Implementation Notes for Darlene

### 7.1 Telemetry Endpoints
- Reuse existing MCP tool functions (no duplication)
- Wrap MCP tool results via `json.loads(result)` → return as JSONResponse
- Add tenant isolation check if `actor == "system"`
- Validate query params (`hours`, `limit`) before passing to tools

### 7.2 Permissions Endpoints
- Follow existing `_tool_resp()` pattern for error handling
- `DELETE` returns `204 No Content` (existing OPM pattern)
- `POST` requires `human_approval` in request body (check before calling tool)
- Map permission errors ("access denied", "insufficient permissions") to 403

### 7.3 Route Registration
Add to `api_routes` list around line 2550 (after existing routes):

```python
# Telemetry
Route("/api/v1/telemetry/summary", telemetry_summary_endpoint, methods=["GET"]),
Route("/api/v1/telemetry/tools/{tool_name}", telemetry_tool_endpoint, methods=["GET"]),
Route("/api/v1/telemetry/top", telemetry_top_endpoint, methods=["GET"]),
Route("/api/v1/telemetry/errors", telemetry_errors_endpoint, methods=["GET"]),

# Permissions
Route("/api/v1/projects/{project}/members", project_members_endpoint, methods=["GET", "POST"]),
Route("/api/v1/projects/{project}/members/{tenant_id}", project_member_delete_endpoint, methods=["DELETE"]),
Route("/api/v1/projects/mine", my_projects_endpoint, methods=["GET"]),
```

---

## 8. Open Questions

1. **Telemetry retention:** Daily rollups prune after 90 days (per architecture doc). Should REST API expose daily rollups, or only hourly?  
   **Answer:** v0.3.0 exposes only hourly metrics (via `telemetry_metrics` table). Daily rollups are for future optimization.

2. **Pagination:** Should telemetry endpoints support pagination if result sets are large?  
   **Answer:** Not needed for v0.3.0 — hourly buckets over 30 days = max 720 rows, manageable. Add in v0.4.0 if needed.

3. **Permissions audit log:** Are permission changes logged in `activity_log`?  
   **Answer:** Not specified in Elliot's design. Recommend adding `_log()` calls in permission tools (defer to Darlene).

---

**Reviewed & Approved by:** Mobley  
**Next steps:** Darlene implements endpoints per this spec; Romero writes integration tests per companion test plan.
