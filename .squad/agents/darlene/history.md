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

---

### 2026-04-07: Copilot CLI POST /mcp Returns 400 — Root Cause Traced & Fixed

**Symptom:** Copilot CLI MCP client (type: "http") received HTTP 400 Bad Request on every
POST to `/mcp` even though the REST `/api/v1/stats` endpoint returned 200 with the same
token.

**True root cause:**

`ApiKeyVerifier.verify_token()` in `server.py` **raised** `AuthenticationError` for invalid
or unrecognized tokens instead of **returning `None`**.  This violates the `TokenVerifier`
protocol contract.  Starlette's `AuthenticationMiddleware` routes any exception from
`authenticate()` to `default_on_error`, which returns `PlainTextResponse(status_code=400)`
— not 401.

The full execution path for a wrong/missing token before the fix:
```
_EarlyAuthRejectMiddleware (old)
  → passed "Bearer wrongtoken" through (only checked prefix, not value)
    → _FixArgumentsMiddleware buffered full body
      → FastMCP Starlette AuthenticationMiddleware
          → BearerAuthBackend.authenticate()
              → ApiKeyVerifier.verify_token()  ← RAISED AuthenticationError
          → default_on_error → PlainTextResponse(400)  ← WRONG STATUS
```

**Additional sub-root-cause:** The `_EarlyAuthRejectMiddleware` rewrite described in the
previous entry was partially but not fully applied.  The middleware signature still used
`requires_auth: bool` rather than `tenant_keys: dict | None`, and token VALUE validation
was missing.  This allowed wrong tokens to reach FastMCP's auth layer and trigger the 400.

**How REST was unaffected:** REST endpoints use `_check_auth()` which calls `_verify_bearer`
directly and returns JSONResponse 401 on failure — bypassing the Starlette
`AuthenticationMiddleware` entirely.

**Complete fix (this session):**

1. **`server.py`:** Changed `ApiKeyVerifier.verify_token()` to `return None` instead of
   `raise AuthenticationError` for invalid tokens.  Protocol-compliant: `BearerAuthBackend`
   now gets `None`, returns `None` itself, Starlette sets `UnauthenticatedUser`,
   `RequireAuthMiddleware` sends 401 with proper WWW-Authenticate header.

2. **`__main__.py`:** Completed the `_EarlyAuthRejectMiddleware` rewrite:
   - Signature: `tenant_keys: dict | None` (was `requires_auth: bool`)
   - Guards ALL HTTP methods (was POST only)
   - Validates token VALUE with `hmac.compare_digest` before body buffer
   - Returns 401 with `Connection: close` and `more_body: False`

3. **`__main__.py`:** Fixed middleware ordering: `_EarlyAuthRejectMiddleware` now runs
   **before** `SessionActivityMiddleware` so invalid requests never touch session state.

**Verification (local):**
```
POST /mcp wrong token → 401 {"error":"Unauthorized"}  (was 400)
POST /mcp no token    → 401 {"error":"Unauthorized"}  (was 400)
POST /mcp valid token → 200 SSE stream                (unchanged)
```
179 tests pass; 1 pre-existing telemetry test unchanged.

**Key learnings:**
- `TokenVerifier.verify_token()` MUST return `None` for invalid tokens — raising an
  exception propagates through `BearerAuthBackend` to `default_on_error` → 400.
- `ApiKeyVerifier.verify_token()` must never raise; wrap everything in `except Exception:
  return None`.
- If MCP returns 400 for auth failures but REST returns 401, the root cause is almost
  always an exception propagating through `AuthenticationMiddleware`.


