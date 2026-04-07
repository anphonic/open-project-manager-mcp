# Decision: Fix POST /mcp 400 Bad Request — ApiKeyVerifier Protocol Violation

**Date:** 2026-04-07  
**Author:** Darlene (Backend Dev)  
**Status:** Implemented  

---

## Problem

POST `/mcp` requests from the Copilot CLI MCP client returned **HTTP 400 Bad Request**
even though REST `/api/v1/stats` returned 200 with the same bearer token.  The MCP
endpoint was unusable from the Copilot CLI.

---

## Root Cause

**`ApiKeyVerifier.verify_token()` in `server.py` raised `AuthenticationError` instead of
returning `None` for invalid/unrecognized tokens.**

The `TokenVerifier` protocol (from `mcp.server.auth.provider`) specifies that
`verify_token()` must return `None` for rejected tokens.  The OPM implementation raised
instead, which propagated unhandled through `BearerAuthBackend.authenticate()` to
Starlette's `AuthenticationMiddleware`.  Starlette's `default_on_error` handler returns
`PlainTextResponse(status_code=400)` for any exception, not the correct 401.

Contributing factor: the `_EarlyAuthRejectMiddleware` rewrite (documented 2026-04-07) was
not fully applied — the signature still used `requires_auth: bool` and did not validate
the token VALUE, so wrong tokens passed through to FastMCP's auth layer where the 400 was
generated.

---

## Decision

### 1. `server.py` — `ApiKeyVerifier.verify_token()` returns `None`, never raises

```python
async def verify_token(self, token: str) -> AccessToken | None:
    try:
        tenant_id = await self._verify(token)
        if not tenant_id:
            return None           # protocol-compliant
        return AccessToken(token=token, client_id=tenant_id, scopes=["api"])
    except Exception:
        return None               # never raise — callers expect None on failure
```

### 2. `__main__.py` — Complete `_EarlyAuthRejectMiddleware` rewrite

- Changed parameter: `tenant_keys: dict | None` (was `requires_auth: bool`)
- Guards ALL HTTP methods (was POST only)
- Validates token VALUE with `hmac.compare_digest` before body buffering
- Returns 401 JSON with `Connection: close` and explicit `more_body: False`
- Skips `/api/` prefix (REST has its own auth)

### 3. `__main__.py` — Middleware order fix

`_EarlyAuthRejectMiddleware` now wraps `SessionActivityMiddleware` (not the other way).
Execution order (outermost first):

```
ConnectionTimeoutMiddleware
  _EarlyAuthRejectMiddleware   ← rejects invalid tokens before any state mutation
    SessionActivityMiddleware
      _FixArgumentsMiddleware
        Starlette app
```

---

## Invariants

- `TokenVerifier.verify_token()` implementations in this codebase MUST return `None` for
  invalid tokens.  Never raise `AuthenticationError` — that causes 400 via Starlette's
  `default_on_error`.
- `_EarlyAuthRejectMiddleware` performs synchronous token validation against env-var keys
  only (`OPM_TENANT_KEYS`).  DB-registered keys are validated downstream by FastMCP's
  `RequireAuthMiddleware`.  This is intentional: DB key validation requires an async DB
  round-trip which is too expensive to do before body buffering for every request.

---

## Verification

```bash
# All three must succeed after deploying to skitterphuger:
# 1. Wrong token → 401 (was 400)
curl -s -w "%{http_code}" -X POST http://192.168.1.178:8765/mcp \
  -H "Authorization: Bearer wrongtoken" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
# expected: 401

# 2. Valid token → 200 SSE stream
curl -s -w "\n%{http_code}" --max-time 3 -X POST http://192.168.1.178:8765/mcp \
  -H "Authorization: Bearer $OPM_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
# expected: 200

# 3. REST still works
curl -s -w "%{http_code}" http://192.168.1.178:8765/api/v1/stats \
  -H "Authorization: Bearer $OPM_BEARER_TOKEN"
# expected: 200
```

---

## Files Changed

- `src/open_project_manager_mcp/server.py` — `ApiKeyVerifier.verify_token()` fix
- `src/open_project_manager_mcp/__main__.py` — `_EarlyAuthRejectMiddleware` rewrite +
  `import hmac` + middleware order fix
