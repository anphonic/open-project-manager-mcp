# Orchestration Log: Darlene — Auth-Hang Fix

**Timestamp:** 2026-04-06T22:10:45Z  
**Agent:** Darlene (Backend Developer)  
**Task:** Fix auth-hang DoS vulnerability in _EarlyAuthRejectMiddleware

---

## Work Summary

### Bug Identified
When OPM receives HTTP requests to `/mcp` with missing or invalid Authorization headers, the server hangs and stops responding entirely. Three compounding issues:

1. `_EarlyAuthRejectMiddleware` only guarded POST requests, not GET
2. Middleware only checked header *presence*, not token *value* — wrong tokens passed through
3. Missing `Connection: close` header left TCP connections in ambiguous keep-alive state

### Root Cause
- **GET requests unguarded** — SSE stream setup attempts reached FastMCP's session manager unchecked
- **Token value not validated** — `Authorization: Bearer wrongtoken` passed through to Starlette's AuthenticationMiddleware, which returned 400 (not 401) without Connection: close
- **Connection persistence** — Uvicorn kept connections alive even after partial body reads, causing stalls

### Fix Implemented
Rewrote `_EarlyAuthRejectMiddleware` in `__main__.py`:

1. **Guard all HTTP methods** — not just POST
2. **Validate token value** — synchronous hmac.compare_digest comparison against env-var keys
3. **Return proper 401** — with `Connection: close` and explicit `more_body: False`
4. **Skip /api/ paths** — REST endpoints have their own auth
5. **Reorder middleware** — auth gate now runs BEFORE `SessionActivityMiddleware`, preventing bad-auth requests from touching session state or body buffer

**Middleware Order (outermost first):**
```
ConnectionTimeoutMiddleware
  _EarlyAuthRejectMiddleware   ← fast 401 + Connection: close, before body read
    SessionActivityMiddleware
      _FixArgumentsMiddleware
        Starlette app (MCP + REST)
```

### Changes Made
- Modified `__main__.py`: Complete rewrite of `_EarlyAuthRejectMiddleware` constructor and call signature
- Changed `tenant_keys` parameter from `requires_auth: bool` to `tenant_keys: dict | None`
- Added synchronous Bearer token validation using `hmac.compare_digest`
- Implemented proper 401 response with Connection: close header
- Reordered middleware stack to guard auth before body buffering
- Added 7 regression tests in `tests/test_middleware.py::TestEarlyAuthRejectMiddleware`

### Test Results
- **7 new regression tests** covering:
  - No tenant keys → all requests pass through
  - POST without auth header → 401 with Connection: close
  - GET without auth header → 401 with Connection: close
  - Wrong Bearer token → 401 response
  - Valid Bearer token → request passes through
  - /api/ paths bypass auth gate
  - lifespan scope requests pass through
- **394 total tests passing** (non-telemetry tests)
- 4 pre-existing async telemetry verification failures unchanged

### Verification
Manual reproduction confirmed:
```bash
# No header — returns 401 immediately with Connection: close
curl -v -X POST http://host:8765/mcp

# Wrong token — returns 401 (was 400), server stays responsive
curl -v -X POST http://host:8765/mcp -H "Authorization: Bearer wrongtoken"

# GET without auth — returns 401 (was not caught before)
curl -v http://host:8765/mcp

# Valid token — reaches MCP transport normally
curl -v -X POST http://host:8765/mcp -H "Authorization: Bearer validtoken" \
     -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"initialize","id":1}'

# Server remains responsive after all calls
```

---

## Deliverables

✅ `_EarlyAuthRejectMiddleware` rewrite  
✅ Token value validation with hmac.compare_digest  
✅ Proper 401 responses with Connection: close  
✅ Middleware reordering  
✅ 7 new regression tests  
✅ 394 tests passing  

---

## Impact

**Security:** Closes primary auth-hang DoS vulnerability. Unauthenticated requests now rejected immediately at transport boundary with no session/state creation.

**Performance:** Auth gate runs before body buffering, preventing memory waste from malicious 6 MB body uploads on unauthenticated requests.

**Stability:** `Connection: close` prevents TCP half-read stalls. Server remains responsive under repeated auth failures.
