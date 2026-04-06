# Auth DoS Security Review

**Author:** Dom DiPierro (Security Expert)  
**Date:** 2026-04-06  
**Scope:** Unauthenticated HTTP request hang / DoS vulnerability

---

## Bug Report

Unauthenticated HTTP requests (no/wrong Authorization header) cause the OPM server to hang and stop responding entirely. Both a stability bug and a security vulnerability — any unauthenticated actor on the network can trivially DoS the server.

---

## Root Cause Analysis

### Primary cause: missing `await` on `_verify_bearer` (commit f482499)

The original `ApiKeyVerifier.verify_token` called `self._verify(token)` without `await`. Since `_verify_bearer` is async, this returned a coroutine object — always truthy — so ANY token was accepted. Every unauthenticated client that sent *any* Bearer token got past auth and was allocated a full MCP session (transport, memory streams, task group entry). Sessions accumulated without limit since fake client_ids (coroutine repr strings) never matched real tenants for cleanup.

**Darlene's fix** (adding `await`) is correct and closes the primary attack vector. Invalid tokens now properly raise `AuthenticationError` and are rejected by Starlette's `AuthenticationMiddleware` before reaching the transport layer.

### Secondary cause: body buffering before auth (`_FixArgumentsMiddleware`)

Even after the `await` fix, `_FixArgumentsMiddleware` reads the entire POST body (up to 6 MB) **before** auth is checked. An attacker can:
- Send 100 concurrent POST requests with 6 MB bodies → 600 MB memory consumed before any auth rejection
- Send slow-drip POST bodies → hold connections open for up to 60s each (ConnectionTimeoutMiddleware limit)
- With `limit_concurrency=100` (default), 100 slow connections = complete server unavailability for 60 seconds

---

## Fixes Applied

### Fix 1: Early auth rejection middleware (`_EarlyAuthRejectMiddleware`)

Added to `__main__.py`. When bearer auth is required, POST requests without an `Authorization: Bearer` header are rejected with 401 **immediately** — before `_FixArgumentsMiddleware` reads the body. This prevents:
- Memory waste from buffering bodies of unauthenticated requests
- Slowloris-style attacks via slow body transmission
- Connection pool exhaustion from body-reading delays

REST API paths (`/api/`) are excluded because they have separate auth handling (including the registration endpoint which uses a body-based key, not a Bearer header).

Middleware stack is now (outermost first):
1. `ConnectionTimeoutMiddleware` — kill long connections
2. `SessionActivityMiddleware` — track activity
3. `_EarlyAuthRejectMiddleware` — reject unauth before body read
4. `_FixArgumentsMiddleware` — buffer body and patch empty args
5. Starlette app → MCP SDK auth → transport

### Fix 2: Sanitize auth error messages

Changed `ApiKeyVerifier.verify_token` error messages from:
- `"Invalid API key"` → `"Unauthorized"`
- `"Authentication failed"` → `"Unauthorized"`

The old messages leaked the auth mechanism type (API keys) to unauthenticated callers via Starlette's default `AuthenticationMiddleware.on_error` handler, which sends `PlainTextResponse(str(exc))`.

---

## Audit Results

### Can an unauthenticated request reach the MCP transport handler?

**After fix:** No. Three layers prevent this:
1. `_EarlyAuthRejectMiddleware` — rejects POST without Bearer header (pre-body)
2. Starlette `AuthenticationMiddleware` — validates Bearer token via `ApiKeyVerifier`
3. `RequireAuthMiddleware` — blocks `UnauthenticatedUser` from reaching `StreamableHTTPASGIApp`

### Is there state that accumulates from failed auth attempts?

**After fix:** No. Auth failures are handled at the HTTP response level. No sessions, transports, memory streams, or task group entries are created for rejected requests. The `_session_creation_lock` in `StreamableHTTPSessionManager` is never acquired.

**Note:** Each auth attempt with a Bearer token still queries the DB (`SELECT squad, key FROM tenant_keys`) via `_verify_bearer`. This is a read-only operation with `busy_timeout=5000` and runs in the thread pool. Not a hang risk, but high-volume brute-force could degrade performance. Rate limiting is recommended for a future release.

### Can a single malformed request permanently wedge the event loop?

**After fix:** No. The middleware stack provides multiple safeguards:
- `ConnectionTimeoutMiddleware` kills connections after `max_connection_age` (default 60s)
- `_EarlyAuthRejectMiddleware` rejects unauth POSTs without blocking
- Uvicorn's `limit_concurrency` caps total connections (default 100)
- Uvicorn's `timeout_keep_alive=30` closes idle keep-alive connections
- SQLite's `busy_timeout=5000` prevents indefinite lock waits

### Are auth errors leaking token values or internal state?

**After fix:** No.
- `ApiKeyVerifier.verify_token` uses generic "Unauthorized" message
- `RequireAuthMiddleware._send_auth_error` sends `"Authentication required"` (standard)
- Token comparison uses `hmac.compare_digest` (timing-safe) ✅
- No stack traces or internal paths in error responses ✅

---

## Additional Vectors Reviewed

### Slowloris via body reading
**Mitigated.** `_EarlyAuthRejectMiddleware` rejects unauthenticated POSTs before body reading. Authenticated slow-body attacks are bounded by `ConnectionTimeoutMiddleware` (60s) and `limit_concurrency` (100).

### Content-Type confusion
**Not exploitable.** `StreamableHTTPServerTransport._check_content_type` validates `Content-Type: application/json` before processing. Non-JSON content types get a 415 response.

### Partial body reads that block
**Mitigated.** `_FixArgumentsMiddleware` reads until `more_body=False`. `ConnectionTimeoutMiddleware` injects `http.disconnect` after timeout, causing the loop to exit cleanly.

### Session accumulation from repeated init requests
**Not exploitable after fix.** Auth prevents unauthenticated session creation. Authenticated sessions are tracked by `SessionActivityTracker` and reaped by `session_reaper` after timeout (default 120s).

### DB query amplification via auth spraying
**Low risk.** Each invalid Bearer token triggers a DB read (`SELECT squad, key FROM tenant_keys`). With WAL mode and `busy_timeout=5000`, reads don't block writes. However, high-volume spraying could saturate the thread pool. **Recommendation:** Add per-IP rate limiting for auth failures in a future release.

---

## Remaining Recommendations (Post-v1)

1. **Per-IP rate limiting on auth failures** — prevent brute-force token guessing
2. **Request body size limit per path** — MCP endpoint needs 6 MB (import_tasks), REST endpoints need only 1 MB
3. **Auth failure logging** — log failed auth attempts with client IP for monitoring
4. **TLS termination** — Bearer tokens transmitted in plaintext on non-localhost bindings

---

## Test Results

- 179/180 tests passing (1 pre-existing telemetry test infra issue)
- All 13 middleware tests passing
- No regressions from security fixes

---

*Review complete. Primary DoS vector closed by Darlene's `await` fix. Secondary body-buffering vector closed by `_EarlyAuthRejectMiddleware`. Auth error messages sanitized.*
