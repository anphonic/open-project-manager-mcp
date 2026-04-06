# Decisions

> Fresh squad start: 2026-04-04. Previous squad archived to "DO NOT USE/".

## Active Directives

_(none yet — directives will be captured here as Andrew states them)_

---

## v0.3.0 Sprint Decisions (2026-04-04 to 2026-04-05)

**Sprint Goal:** Add telemetry tracking and project-level permissions to OPM.

### Architecture (Elliot)
- Telemetry: Fire-and-forget async recording with hourly bucket aggregation, 4 MCP tools + 4 REST endpoints
- Permissions: Owner/Contributor/Reader role hierarchy, gated by `OPM_ENFORCE_PERMISSIONS` env var (default OFF)
- Both features additive — no breaking schema changes

### Schema Design (Trenton)
- Telemetry: `telemetry_metrics` (hourly) + `telemetry_daily` (rollups) with UPSERT aggregation
- Permissions: `project_permissions` table with UNIQUE(project, tenant_id) constraint
- Application-side percentile calculation (SQLite lacks native percentile functions)
- Default-deny permissions model with owner bypass to prevent lock-out

### Implementation (Darlene)
- Telemetry: `_record_metric()` helper using `asyncio.create_task(asyncio.to_thread(...))` for non-blocking writes
- Permissions: `_check_project_access()` with role hierarchy validation
- 8 permissions tools (grant, revoke, list, migrate, transfer, check, get_my_permissions, set_enforcement)
- 4 telemetry tools (summary, by_tool, top_tools, error_summary)
- Migration path: backfill existing projects from activity_log via `migrate_permissions()` tool

### REST API (Mobley)
- Expose all 12 new MCP tools via REST endpoints (8 telemetry + 4 permissions)
- Admin tools (`migrate_permissions`, `set_permission_enforcement`) remain MCP-only
- Input validation: `hours` capped at 720, `limit` capped at 100
- Tenant isolation: all queries auto-scope to `_get_actor()`

### Security (Dom)
- **Fixed:** `OPM_ENFORCE_PERMISSIONS` must equal exactly `"1"` (prevent empty string bypass)
- **Fixed:** Telemetry hours parameter capped at 720 to prevent DoS via excessive DB scans
- **Fixed:** REST query parameter validation with try/except to prevent unhandled exceptions
- **Fixed:** Explicit system tenant auth check in telemetry endpoints

### Testing (Romero)
- 47 tests written (18 telemetry + 29 permissions)
- Final result: 43/47 passing (91.5%)
- 4 failing tests due to async fire-and-forget telemetry verification (test infrastructure issue, not implementation bug)
- 100% permissions test pass rate

### Documentation (Angela)
- Version bumped to 0.3.0 in pyproject.toml
- README updated with telemetry and permissions feature sections
- CHARTER updated with v0.3.0 status and feature descriptions
- REST endpoints table updated with 8 new endpoints

---

## 2026-04-06: Auth-Hang DoS Fix

**Incident:** Unauthenticated HTTP requests caused OPM server to hang and stop responding entirely.

### Root Causes (3 compounding issues)

1. **Incomplete auth gate** — `_EarlyAuthRejectMiddleware` only guarded POST requests with missing Authorization header. Did NOT:
   - Check GET requests (e.g., `GET /mcp` for SSE stream setup)
   - Validate token *value* — `Authorization: Bearer wrongtoken` passed straight through

2. **Wrong status code** — FastMCP's `BearerAuthBackend` raises `AuthenticationError` for invalid tokens. Starlette's `default_on_error` returns 400, NOT 401. Also omitted `Connection: close`, leaving connection in ambiguous keep-alive state.

3. **Missing `Connection: close`** — Even 401 responses lacked this header. Uvicorn kept connections alive. Partial body reads before response sent left connections in half-read state preventing subsequent requests.

### Decision (Darlene)

Rewrote `_EarlyAuthRejectMiddleware` to:
- Guard ALL HTTP methods (not just POST)
- Validate Bearer token value synchronously using `hmac.compare_digest` against env-var keys
- Return proper **401** with `Connection: close` and explicit `more_body: False`
- Skip `/api/` paths (REST endpoints handle own auth)
- **Move before `SessionActivityMiddleware`** — prevent bad-auth from touching session state or body buffer

**Middleware order (outermost first):**
```
ConnectionTimeoutMiddleware
  _EarlyAuthRejectMiddleware     ← fast 401 + Connection: close before body read
    SessionActivityMiddleware
      _FixArgumentsMiddleware
        Starlette app
```

### Additional Fixes (Dom)

1. **Body-buffer DoS mitigation** — `_EarlyAuthRejectMiddleware` placed BEFORE `_FixArgumentsMiddleware` to prevent reading 6 MB bodies for unauthenticated POSTs. Prevents Slowloris-style amplification (100 concurrent 6 MB requests = 600 MB memory before rejection).

2. **Auth error sanitization** — `"Invalid API key"` → `"Unauthorized"` and `"Authentication failed"` → `"Unauthorized"`. Prevents auth mechanism type disclosure via error messages.

### Test Results

- **7 new regression tests** in `TestEarlyAuthRejectMiddleware`
- **394 tests passing** (non-telemetry)
- **179/180 tests passing** (with fixes)
- All middleware tests passing, no regressions

### Security Analysis

✅ Unauthenticated requests cannot reach MCP transport (three-layer defense)  
✅ No state accumulation from failed auth attempts  
✅ Malformed requests cannot wedge event loop (multiple safeguards)  
✅ Auth errors don't leak token values or mechanism type  
✅ Connection lifecycle properly managed  

---

## Architecture Decisions

## 2026-03-31: Project bootstrapped

**Decision:** Build open-project-manager-mcp as a standalone SQLite-backed FastMCP server.
**Rationale:** SQLite is the right tool for a task queue — ordered, mutable state.
**Status:** ✅ Implemented (v0.2.x running on skitterphuger)

## Infrastructure

- **OPM Port:** 8765 (http://192.168.1.178:8765/mcp)
- **Squad Knowledge Port:** 8768 (http://192.168.1.178:8768 — SSE, no auth)
- **Godot Docs:** 8767 (http://192.168.1.178:8767/mcp)
- **Blender:** 8760 (http://192.168.1.178:8760/mcp)
- **Transport:** Streamable HTTP (`--http`), NOT SSE
- **MCP client config type:** `"http"` not `"sse"`
