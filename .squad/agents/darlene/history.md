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

---

### 2026-04-07: Auth-Hang Bug Fix

**Bug:** When OPM receives an HTTP request to `/mcp` (or any non-REST path) with
no Authorization header OR a wrong Bearer token, the server could hang and stop
responding entirely.

**Root causes (three compounding issues):**

1. **Incomplete gate** — `_EarlyAuthRejectMiddleware` only rejected POST requests
   with a *missing* Authorization header. It did NOT:
   - Check GET requests (e.g. `GET /mcp` to establish an SSE stream)
   - Validate the token *value* — `Authorization: Bearer wrongtoken` passed straight
     through to FastMCP's Starlette `AuthenticationMiddleware`.

2. **400 instead of 401 for wrong tokens** — FastMCP's `BearerAuthBackend` raises
   `AuthenticationError` for an invalid token. Starlette's `default_on_error`
   returns `PlainTextResponse(status_code=400)`, NOT 401. This also omits
   `Connection: close`, leaving the connection in an ambiguous keep-alive state.

3. **Missing `Connection: close`** — the old 401 response for missing headers did
   not include `Connection: close`. Uvicorn kept the connection alive; if the
   request body was not fully consumed before the response was sent the connection
   could be left in a half-read state that prevents subsequent requests from being
   processed on the same TCP connection.

**Fix (in `__main__.py`):**
- Rewrote `_EarlyAuthRejectMiddleware` to:
   - Accept `tenant_keys: dict | None` (flat env-var keys) instead of `requires_auth: bool`
   - Guard ALL HTTP methods (not just POST) against bad auth
   - Validate the Bearer token value synchronously against env-var keys using
     `hmac.compare_digest` (constant-time) — no DB call, no `asyncio.to_thread`
   - Return proper **401** (not 400) with `Connection: close` and explicit
     `more_body: False`
   - Skip `/api/` prefix (REST endpoints have their own `_check_auth`)
   - Reordered middleware: auth gate now runs **before** `SessionActivityMiddleware`
     so bad-auth requests never update session state or touch the body buffer

**Middleware order after fix (outermost first):**
```
ConnectionTimeoutMiddleware
  _EarlyAuthRejectMiddleware   ← fast 401, Connection: close, before body read
    SessionActivityMiddleware
      _FixArgumentsMiddleware
        Starlette app (MCP + REST)
```

**Manual repro for Dom to verify:**
```bash
# Server started with OPM_TENANT_KEYS='{"squad":"validtoken"}' --http
# 1. No header — must return 401 immediately with Connection: close
curl -v -X POST http://host:8765/mcp
# 2. Wrong token — must return 401 (was 400 before fix), server must stay responsive
curl -v -X POST http://host:8765/mcp -H "Authorization: Bearer wrongtoken"
# 3. GET without auth — must return 401 (was not caught before)
curl -v http://host:8765/mcp
# 4. Valid token — must reach MCP transport normally
curl -v -X POST http://host:8765/mcp -H "Authorization: Bearer validtoken" \
     -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"initialize","id":1}'
# After all 4 calls the server must still respond to new requests.
```

**Testing Notes:**
- 7 new regression tests in `TestEarlyAuthRejectMiddleware` in `tests/test_middleware.py`
- All 394 non-telemetry tests pass; 4 pre-existing async telemetry failures unchanged

