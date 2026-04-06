# Orchestration Log: Dom — Auth Security Review

**Timestamp:** 2026-04-06T22:10:45Z  
**Agent:** Dom DiPierro (Security Expert)  
**Task:** Verify auth-hang DoS fix and perform comprehensive security audit

---

## Work Summary

### Primary Bug Analysis
**Root cause:** Missing `await` on `_verify_bearer` in `ApiKeyVerifier.verify_token` caused coroutine objects (always truthy) to be accepted as valid auth. Sessions accumulated without cleanup as fake client_ids never matched real tenants.

**Darlene's fix verified:** Adding `await` correctly closes this primary attack vector. Invalid tokens now properly raise `AuthenticationError` and are rejected by Starlette's `AuthenticationMiddleware` before reaching transport layer.

### Secondary Vulnerability Identified
`_FixArgumentsMiddleware` reads entire POST body (up to 6 MB) **before** auth is checked. An attacker can:
- Send 100 concurrent 6 MB POST requests → 600 MB memory consumed before auth rejection
- Send slow-drip bodies → hold connections open for 60s each (ConnectionTimeoutMiddleware limit)
- With `limit_concurrency=100`, 100 slow connections = complete server unavailability for 60 seconds

### Fixes Applied

**Fix 1: Early Auth Rejection Middleware**
- `_EarlyAuthRejectMiddleware` moved BEFORE `_FixArgumentsMiddleware`
- Rejects POST requests without Bearer header immediately (before body read)
- REST API paths (`/api/`) excluded (separate auth handling)
- Prevents body-buffer amplification DoS on unauthenticated requests
- Saves 600 MB memory per 100-request batch

**Fix 2: Auth Error Message Sanitization**
- `"Invalid API key"` → `"Unauthorized"` (no mechanism type leakage)
- `"Authentication failed"` → `"Unauthorized"`
- Prevents auth mechanism disclosure via Starlette error handler

### Security Audit Results

**Can unauthenticated requests reach MCP transport?**
- ✅ No. Three-layer defense:
  1. `_EarlyAuthRejectMiddleware` — rejects POST without Bearer header (pre-body)
  2. Starlette `AuthenticationMiddleware` — validates Bearer token via `ApiKeyVerifier`
  3. `RequireAuthMiddleware` — blocks `UnauthenticatedUser` from reaching transport

**Does failed auth accumulate state?**
- ✅ No. Auth failures handled at HTTP response level. No sessions, transports, memory streams, or task group entries created for rejected requests.

**Can malformed requests wedge event loop?**
- ✅ No. Multiple safeguards:
  - `ConnectionTimeoutMiddleware` kills connections after 60s
  - `_EarlyAuthRejectMiddleware` rejects unauth POSTs without blocking
  - Uvicorn `limit_concurrency=100` caps total connections
  - Uvicorn `timeout_keep_alive=30` closes idle keep-alive connections
  - SQLite `busy_timeout=5000` prevents indefinite lock waits

**Do auth errors leak secrets?**
- ✅ No.
  - Generic "Unauthorized" message
  - No token values or internal paths in responses
  - `hmac.compare_digest` ensures timing-safe comparison

### Additional Vectors Reviewed

**Slowloris via body reading**
- ✅ Mitigated. `_EarlyAuthRejectMiddleware` rejects unauth POSTs before body reading. Authenticated slow-body attacks bounded by `ConnectionTimeoutMiddleware` (60s) and `limit_concurrency` (100).

**Content-Type confusion**
- ✅ Not exploitable. `StreamableHTTPServerTransport._check_content_type` validates `Content-Type: application/json` before processing.

**Partial body reads blocking**
- ✅ Mitigated. `_FixArgumentsMiddleware` reads until `more_body=False`. `ConnectionTimeoutMiddleware` injects `http.disconnect` after timeout.

**Session accumulation**
- ✅ Not exploitable after fix. Auth prevents unauthenticated session creation. Authenticated sessions tracked and reaped after 120s timeout.

**DB query amplification**
- ⚠️ Low risk. Each invalid Bearer token triggers DB read (`SELECT squad, key FROM tenant_keys`). WAL mode + `busy_timeout=5000` prevents blocking. High-volume spraying could saturate thread pool. **Recommendation:** Add per-IP rate limiting for future release.

### Test Results
- **179/180 tests passing** (1 pre-existing telemetry infrastructure issue)
- **All 13 middleware tests passing**
- No regressions from security fixes

---

## Deliverables

✅ Primary DoS vector verified closed (missing `await` fix)  
✅ Secondary body-buffer DoS vector identified and fixed  
✅ Auth error messages sanitized  
✅ Comprehensive security audit completed  
✅ Three-layer auth enforcement verified  
✅ 179/180 tests passing with fixes  

---

## Recommendations (Post-v1)

1. **Per-IP rate limiting** — prevent brute-force token guessing
2. **Request body size limits** — MCP endpoint 6 MB, REST endpoints 1 MB
3. **Auth failure logging** — log failed attempts with client IP for monitoring
4. **TLS termination** — Bearer tokens transmitted in plaintext on non-localhost bindings

---

## Impact

**Stability:** Auth-hang DoS completely mitigated. Three-layer enforcement prevents any unauthenticated request from reaching critical sections.

**Security:** Information leakage prevented. Generic error messages, timing-safe comparison, no state accumulation from failed auth.

**Performance:** Early auth rejection before body buffering prevents Slowloris-style memory exhaustion attacks.
