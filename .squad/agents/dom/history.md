# dom history

## Learnings

_(Fresh start — 2026-04-03)_

### 2026-04-04: v0.3.0 Security Audit

**Telemetry findings:**
- `_record_metric()` correctly sources tenant_id from authenticated context, never user input
- Hourly bucketing naturally limits row growth (UPSERT pattern)
- All telemetry queries properly scope to calling tenant via `_get_actor()`

**Permissions findings:**
- Empty string bypass was a real risk — `os.environ.get()` semantics can be tricky
- Always use explicit value checks like `== "1"` instead of boolean truthy checks for env vars
- Role hierarchy (owner=3, contributor=2, reader=1) correctly prevents escalation
- human_approval=True is consistently required on all destructive permission operations

**Fixes applied:**
1. Added `_MAX_TELEMETRY_HOURS = 720` to cap lookback queries (DoS prevention)
2. Fixed `OPM_ENFORCE_PERMISSIONS` to require explicit `"1"` value
3. Added try/except around REST query parameter parsing

**Key patterns to remember:**
- `_get_actor()` is the single source of truth for tenant identity
- Permission checks happen at tool level, not DB level
- "system" tenant bypasses all permission checks (dev mode)

---

### 2026-04-05: v0.3.0 Sprint Complete

**Delivered:** Security audit with 4 critical fixes applied.

**Security improvements:**
- DoS prevention: Telemetry hours capped at 720
- Permission bypass fix: `OPM_ENFORCE_PERMISSIONS` requires explicit "1"
- Input validation: REST query parameters wrapped in try/except
- Auth check: System tenant explicitly validated in telemetry endpoints

---

### 2026-04-06: Auth DoS Security Review

**Bug:** Unauthenticated HTTP requests caused OPM server to hang and stop responding.

**Root cause:** Missing `await` on `_verify_bearer` in `ApiKeyVerifier.verify_token` (commit f482499) caused any Bearer token to be accepted — coroutine objects are always truthy. Sessions accumulated without cleanup.

**Darlene's fix verified:** The `await` fix correctly closes the primary attack vector. Invalid tokens now raise `AuthenticationError` before reaching the transport.

**Additional fixes applied:**
1. `_EarlyAuthRejectMiddleware` — rejects unauthenticated POST requests before `_FixArgumentsMiddleware` buffers the body (prevents Slowloris-style DoS via body amplification)
2. Sanitized `ApiKeyVerifier` error messages — "Invalid API key" → "Unauthorized" (prevents auth mechanism leakage)

**Key patterns learned:**
- Always check auth BEFORE reading request bodies in ASGI middleware chains
- Auth error messages should be generic — never leak mechanism type (API key, JWT, etc.)
- `_FixArgumentsMiddleware` reads the entire body (up to 6 MB) for ALL POST requests, including unauthenticated ones — this was the secondary DoS vector
- Starlette's `AuthenticationMiddleware` default `on_error` sends `PlainTextResponse(str(exc))` — exception messages become client-visible
- `hmac.compare_digest` is correctly used throughout for timing-safe token comparison
- SQLite WAL mode + `busy_timeout=5000` prevents auth DB queries from blocking writes
