# Darlene — Implementation Brief: Self-Service Token Registration

**From:** Elliot  
**To:** Darlene  
**Scope:** `POST /api/v1/register` + `DELETE /api/v1/register/{squad}` — self-service bearer token provisioning  
**Working files:** `src/open_project_manager_mcp/server.py`, `src/open_project_manager_mcp/__main__.py`  
**Depends on:** REST API feature (build order #6) must already be merged — this extends it.

Read this top-to-bottom. Each section is precise and complete. Do NOT deviate from patterns or add extras without checking back.

---

## Mental model — what you're changing

- `ApiKeyVerifier` currently holds a static `dict[str, str]` snapshot of env var keys. After this change it also queries the DB on each auth call.
- `_check_auth()` in the REST router currently checks only `tenant_keys` (env var). After this change it also queries the DB.
- A new `tenant_keys` SQLite table stores self-registered squads.
- Two new REST routes: `POST /register` and `DELETE /register/{squad}`, mounted in the same `/api/v1` router, gated by `OPM_REGISTRATION_KEY` env var.
- Rate limiting is entirely in-memory — no new imports, no new dependencies.

---

## Step 1 — Schema migration

### Add `tenant_keys` table to `_SCHEMA` in `server.py`

The `_SCHEMA` constant already contains `CREATE TABLE IF NOT EXISTS tasks ...` etc. Append the following block to `_SCHEMA` (before the closing triple-quote):

```sql
CREATE TABLE IF NOT EXISTS tenant_keys (
    squad       TEXT PRIMARY KEY,
    key         TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
```

`CREATE TABLE IF NOT EXISTS` is idempotent. No separate migration block needed.

---

## Step 2 — Shared `_verify_bearer` helper in `create_server()`

Add this inner function immediately after `_lock = asyncio.Lock()` and before the `ApiKeyVerifier` instantiation. It encapsulates the two-step lookup (env var first, DB second) so both MCP auth and REST auth share a single implementation:

```python
def _verify_bearer(token: str) -> str | None:
    """Return tenant_id if token is valid, else None. Env var keys take precedence."""
    # 1. Env var keys — checked first, O(n) with constant-time compare
    if tenant_keys:
        for tid, key in tenant_keys.items():
            if hmac.compare_digest(token, key):
                return tid
    # 2. DB-registered keys — re-queried on every call (no restart needed)
    try:
        rows = conn.execute("SELECT squad, key FROM tenant_keys").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        if hmac.compare_digest(token, row["key"]):
            return row["squad"]
    return None
```

---

## Step 3 — Update `ApiKeyVerifier`

`ApiKeyVerifier` is defined at module level (before `_SCHEMA`). It currently takes only `tenant_keys`. Change its signature to also accept `conn` and a `_verify_bearer_fn` callable so it can delegate:

```python
class ApiKeyVerifier(TokenVerifier):
    """Validates Bearer API keys; checks env var keys then DB-registered keys."""

    def __init__(self, verify_fn):
        self._verify = verify_fn

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            tenant_id = self._verify(token)
            if not tenant_id:
                raise AuthenticationError("Invalid API key")
            return AccessToken(
                token=token,
                client_id=tenant_id,
                scopes=["api"],
            )
        except AuthenticationError:
            raise
        except Exception:
            raise AuthenticationError("Authentication failed") from None
```

`verify_fn` will be the `_verify_bearer` closure defined in Step 2. This keeps the class free of direct `conn` / `tenant_keys` references and testable in isolation.

### Update instantiation in `create_server()`

The existing code is:
```python
if tenant_keys:
    token_verifier = ApiKeyVerifier(tenant_keys)
```

Change to:
```python
# Verifier is always created so DB-registered keys work even with no env var keys.
token_verifier = ApiKeyVerifier(_verify_bearer)
auth_settings = AuthSettings(
    issuer_url=server_url,
    resource_server_url=server_url,
)
```

> **Important:** Remove the `if tenant_keys:` guard. We want token verification active whenever the server can have keys — including when no env var keys are set but DB-registered keys exist. The verifier returns `None` (→ 401) if no keys match, which is the correct behaviour.

Also update `mcp = FastMCP(...)` — `token_verifier` and `auth_settings` are now always set:
```python
mcp = FastMCP(
    "open-project-manager-mcp",
    token_verifier=token_verifier,
    auth=auth_settings,
    transport_security=transport_security,
)
```

> **Backward compat note:** When `OPM_TENANT_KEYS` is unset AND the `tenant_keys` table is empty, `_verify_bearer` returns `None` for any token. That means all requests that send a `Bearer` token will get 401. Unauthenticated requests (no `Authorization` header) are handled by FastMCP's existing logic — when `token_verifier` is set but no header is present, FastMCP rejects with 401. This is a slight behaviour change: previously when no `tenant_keys`, auth was disabled entirely. 
>
> **To preserve the "no keys → no auth" behaviour:** wrap in a guard:
> ```python
> def _any_keys_configured() -> bool:
>     if tenant_keys:
>         return True
>     try:
>         return bool(conn.execute("SELECT 1 FROM tenant_keys LIMIT 1").fetchone())
>     except sqlite3.Error:
>         return False
> ```
> However this per-request check adds a DB call on every request before we even verify. **Simpler approach:** keep the `if tenant_keys:` guard for `token_verifier` and `auth_settings`, but also expose `_verify_bearer` to the REST router. The REST router's `_check_auth` will handle the "no keys → unauthenticated" logic itself (it already does this). MCP auth will only activate when env var keys are present — DB-only keys are accessible via REST auth but not MCP auth. This is an acceptable limitation (REST is the registration surface; MCP is the tool surface for pre-provisioned squads).
>
> **Final decision on MCP auth:** Restore the `if tenant_keys:` guard. DB-registered keys are for REST API access only in v1. Squads that need MCP access still go through `--generate-token` / `OPM_TENANT_KEYS`. Document this clearly in the endpoint response.

So the final state for `token_verifier` setup:
```python
auth_settings = None
token_verifier = None
if tenant_keys:
    token_verifier = ApiKeyVerifier(_verify_bearer)
    auth_settings = AuthSettings(
        issuer_url=server_url,
        resource_server_url=server_url,
    )
```

---

## Step 4 — Update `_check_auth` in `_build_rest_router()`

The current `_check_auth` only checks `tenant_keys` (the env var dict). Replace its inner loop with a call to `_verify_bearer`:

```python
async def _check_auth(request: Request):
    """Returns (actor, None) on success or (None, JSONResponse) on failure."""
    # Unauthenticated mode: no env var keys AND tenant_keys table is empty
    if not tenant_keys:
        try:
            has_db_keys = bool(conn.execute("SELECT 1 FROM tenant_keys LIMIT 1").fetchone())
        except sqlite3.Error:
            has_db_keys = False
        if not has_db_keys:
            return "system", None

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
    token = auth_header[7:]
    tenant_id = _verify_bearer(token)
    if tenant_id:
        return tenant_id, None
    return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
```

---

## Step 5 — Rate limiting state

Add this directly inside `_build_rest_router()`, before the endpoint functions. It's scoped to the router factory call (one set of state per server lifetime):

```python
import time
from collections import defaultdict

_reg_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60.0
_RATE_MAX = 5

def _check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if rate limit exceeded."""
    now = time.monotonic()
    attempts = [t for t in _reg_attempts[ip] if now - t < _RATE_WINDOW]
    _reg_attempts[ip] = attempts
    if len(attempts) >= _RATE_MAX:
        return False
    _reg_attempts[ip].append(now)
    return True
```

> `time` and `collections.defaultdict` are stdlib — no new imports in `pyproject.toml`.

---

## Step 6 — `POST /api/v1/register` endpoint

Add this function inside `_build_rest_router()`:

```python
import re as _re
_SQUAD_RE = _re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

async def register_endpoint(request: Request) -> JSONResponse:
    registration_key = os.environ.get("OPM_REGISTRATION_KEY")
    if not registration_key:
        return JSONResponse({"error": "Not Found"}, status_code=404)

    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return JSONResponse(
            {"error": "Too many registration attempts. Try again later."},
            status_code=429,
        )

    body, err = await _read_json_body(request)
    if err:
        return err

    squad = body.get("squad") if isinstance(body, dict) else None
    provided_key = body.get("registration_key") if isinstance(body, dict) else None

    if not isinstance(squad, str) or not _SQUAD_RE.match(squad):
        return JSONResponse(
            {"error": "Invalid squad name. Must be 1–64 characters: letters, digits, hyphens, underscores."},
            status_code=400,
        )

    if not isinstance(provided_key, str) or not hmac.compare_digest(provided_key, registration_key):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        existing = conn.execute(
            "SELECT squad FROM tenant_keys WHERE squad = ?", (squad,)
        ).fetchone()
        if existing:
            return JSONResponse(
                {"error": f"Squad '{squad}' is already registered."},
                status_code=409,
            )
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO tenant_keys (squad, key, created_at) VALUES (?, ?, ?)",
            (squad, token, _now()),
        )
        conn.commit()
    except sqlite3.Error:
        return JSONResponse({"error": "Error: database error"}, status_code=500)

    return JSONResponse(
        {
            "squad": squad,
            "token": token,
            "note": (
                "Store this token — it will not be shown again. "
                "Use it as a Bearer token in the Authorization header. "
                "This token grants REST API access only; for MCP access, "
                "ask your admin to add the squad to OPM_TENANT_KEYS."
            ),
        },
        status_code=201,
    )
```

> `secrets` is already imported in `__main__.py` but NOT in `server.py`. Add `import secrets` to the top of `server.py` (with the other stdlib imports).

> `os` is already imported in `server.py` (used in `_SCHEMA` vicinity? check — if not, add it). Actually `os` is not currently imported in `server.py` — all env var reading is done in `__main__.py`. For this endpoint, read `OPM_REGISTRATION_KEY` via `os.environ.get(...)`. Add `import os` to `server.py` imports.

---

## Step 7 — `DELETE /api/v1/register/{squad}` endpoint

Add this function inside `_build_rest_router()`:

```python
async def deregister_endpoint(request: Request) -> JSONResponse:
    registration_key = os.environ.get("OPM_REGISTRATION_KEY")
    if not registration_key:
        return JSONResponse({"error": "Not Found"}, status_code=404)

    reg_key_header = request.headers.get("X-Registration-Key", "")
    if not reg_key_header or not hmac.compare_digest(reg_key_header, registration_key):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    squad = request.path_params["squad"]

    try:
        result = conn.execute(
            "DELETE FROM tenant_keys WHERE squad = ?", (squad,)
        )
        conn.commit()
        if result.rowcount == 0:
            return JSONResponse(
                {"error": f"Squad '{squad}' not found."}, status_code=404
            )
    except sqlite3.Error:
        return JSONResponse({"error": "Error: database error"}, status_code=500)

    return JSONResponse(None, status_code=204)
```

> For `204 No Content`, pass `None` as the content to `JSONResponse`. Starlette will send an empty body. Alternatively use `Response(status_code=204)` if `JSONResponse(None)` encodes as `"null"`.

Use `Response(status_code=204)` to be safe:
```python
from starlette.responses import Response
...
return Response(status_code=204)
```

`Response` is already imported in `server.py` for REST (check — it should be, since `JSONResponse` is a subclass and both come from `starlette.responses`).

---

## Step 8 — Register routes in `_build_rest_router()`

The existing `return Router(routes=[...])` block at the bottom of `_build_rest_router()` currently returns:

```python
return Router(routes=[
    Route("/tasks", endpoint=tasks_endpoint, methods=["GET", "POST"]),
    Route("/tasks/{id:str}", endpoint=task_endpoint, methods=["GET", "PATCH", "DELETE"]),
    Route("/projects", endpoint=projects_endpoint, methods=["GET"]),
    Route("/stats", endpoint=stats_endpoint, methods=["GET"]),
])
```

Add the two new routes:

```python
return Router(routes=[
    Route("/tasks", endpoint=tasks_endpoint, methods=["GET", "POST"]),
    Route("/tasks/{id:str}", endpoint=task_endpoint, methods=["GET", "PATCH", "DELETE"]),
    Route("/projects", endpoint=projects_endpoint, methods=["GET"]),
    Route("/stats", endpoint=stats_endpoint, methods=["GET"]),
    Route("/register", endpoint=register_endpoint, methods=["POST"]),
    Route("/register/{squad:str}", endpoint=deregister_endpoint, methods=["DELETE"]),
])
```

---

## Step 9 — Startup warning for short `OPM_REGISTRATION_KEY`

In `__main__.py`, inside `main()`, after the `tenant_keys = _load_tenant_keys()` call (and before `create_server(...)` is called), add:

```python
_reg_key = os.environ.get("OPM_REGISTRATION_KEY")
if _reg_key is not None and len(_reg_key) < 16:
    print(
        "WARNING: OPM_REGISTRATION_KEY is shorter than 16 characters — "
        "use a longer key in production.",
        file=sys.stderr,
    )
```

Only warn if the key is set but too short. If it's not set at all, no warning (feature is simply disabled).

---

## Step 10 — Add missing imports to `server.py`

Check the existing imports at the top of `server.py` and add any that are missing:

```python
import os          # for os.environ.get in register/deregister endpoints
import re          # for _SQUAD_RE squad name validation
import secrets     # for secrets.token_urlsafe(32) in register endpoint
import time        # for rate limiting timestamps
from collections import defaultdict  # for _reg_attempts dict
```

`hmac`, `sqlite3`, `json`, `asyncio` are already imported. `starlette.responses.Response` — check whether it's already imported alongside `JSONResponse`; if not, add it.

---

## Testing checklist

Write tests in `tests/` following the existing pattern (`server._tool_manager._tools[...].fn` / direct REST endpoint calls via Starlette `TestClient`).

Cover:

| # | Test case |
|---|-----------|
| 1 | `POST /register` — missing `OPM_REGISTRATION_KEY` → 404 |
| 2 | `POST /register` — wrong `registration_key` → 401 |
| 3 | `POST /register` — invalid squad name (spaces, too long, empty) → 400 |
| 4 | `POST /register` — valid request → 201, token returned, row in DB |
| 5 | `POST /register` — duplicate squad → 409 |
| 6 | `POST /register` — 6th attempt from same IP within 60s → 429 |
| 7 | `DELETE /register/{squad}` — wrong key → 401 |
| 8 | `DELETE /register/{squad}` — squad not found → 404 |
| 9 | `DELETE /register/{squad}` — valid → 204, row removed |
| 10 | REST auth — DB-registered token accepted in `_check_auth` |
| 11 | REST auth — env var key takes precedence over same squad in DB |
| 12 | REST auth — unauthenticated when both env var and DB are empty |
| 13 | `OPM_REGISTRATION_KEY` length warning — printed when key < 16 chars |

---

## What NOT to change

- `--generate-token` CLI — stays stdout-only. No DB write. No changes to that code path.
- MCP `ApiKeyVerifier` — DB-registered keys are **REST only** in this version. The verifier guard `if tenant_keys:` stays. If you find the "DB keys work for REST but not MCP" distinction confusing, add a comment, not a code change.
- `_MAX_REST_BODY` — registration payload is tiny; the existing cap is fine.
- Anything in the `tests/` directory that isn't related to the features above.

---

## Edge cases to handle

**`JSONResponse(None, status_code=204)`** encodes as `"null"` in some Starlette versions. Use `Response(status_code=204)` (no body) for the DELETE success response.

**`_re` alias:** Import `re` as `_re` inside `_build_rest_router()` OR import it at module level as `import re`. Module-level is cleaner — just verify it doesn't conflict.

**Thread safety of `_reg_attempts`:** The `defaultdict` is mutated inside the async endpoint without a lock. In uvicorn's default single-worker async mode, this is safe (single event loop thread). Do NOT add a lock — it adds complexity for no benefit.

**`request.client` can be `None`:** Covered by `request.client.host if request.client else "unknown"` in the endpoint.
