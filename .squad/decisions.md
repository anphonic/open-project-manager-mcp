# Decisions

## 2026-03-31: Project bootstrapped

**Decision:** Build open-project-manager-mcp as a standalone SQLite-backed FastMCP server.
**Rationale:** squad-knowledge-mcp uses ChromaDB (wrong fit for ordered mutable task state). SQLite is the right tool for a task queue.
**Patterns to follow:** Mirror squad-knowledge-mcp's `create_server(db_path)` factory pattern, closure-based tools, stdio+TCP transport.

## 2026-03-31: Caller-supplied task IDs

**Decision:** Task IDs are caller-supplied strings (e.g., "auth-login-ui"), not auto-generated UUIDs.
**Rationale:** Agent-friendly — meaningful IDs are easier to reference in tool calls than opaque UUIDs.

## 2026-03-31: Architecture patterns confirmed (from Elliot)

*Merged from inbox: elliot-architecture-confirmed.md*

**By:** Elliot (via cross-squad query to squad-knowledge-mcp team)

**Findings:**

1. **`create_server(db_path)` factory + closures** — All tools live as nested closures inside `create_server()`, capturing shared state (DB connection, locks) with no module-level globals. Our version returns `FastMCP` directly.
2. **Tool registration** — Bare `@mcp.tool()` decorator; type annotations drive MCP schema automatically.
3. **Transport layer** — stdio default; `--http` flag enables HTTP streamable. `_FixArgumentsMiddleware` is essential for HTTP mode. FastMCP v1.26+ requires `TransportSecuritySettings(enable_dns_rebinding_protection=False)` for LAN access.
4. **Lifespan gotcha** — Starlette Mount does NOT propagate lifespan to sub-apps; must wrap manually via `_make_lifespan()`.
5. **Test pattern** — `server._tool_manager._tools["tool_name"].fn` confirmed. `_sync_wrap()` helper auto-awaits coroutines.
6. **pyproject.toml** — Deps: `mcp>=1.0,<2.0`, `platformdirs>=3.0,<5.0`. Dev: `pytest>=7.0`, `pytest-mock>=3.0`, `anyio[trio]>=3.0`.
7. **`human_approval=True`** — Apply to `delete_task`.
8. **`list_ready_tasks`** — Default `n_results=10`, cap `MAX_N_RESULTS=100`.

**Decision:** Darlene cleared to begin implementation on core scaffold. (Superseded: coordinator built full implementation this session.)

## 2026-03-31: Multi-tenant bearer token auth implemented

**Decision:** Add `OPM_TENANT_KEYS` support, mirroring the squad-knowledge-mcp pattern.  
**Rationale:** HTTP mode exposes the server on the LAN; bearer token auth gates access per squad without requiring infrastructure changes. Tasks are **not** tenant-scoped — the `project` field handles data separation. Provisioned squads: mrrobot, westworld, fsociety.  
**Implementation:** `ApiKeyVerifier` with `hmac.compare_digest`; `--generate-token SQUAD_NAME` CLI for provisioning; `AuthSettings` wired into FastMCP when keys present.

## 2026-03-31: Deployed to skitterphuger

**Decision:** Run open-project-manager-mcp in production on the home LAN server.  
**Host:** 192.168.1.178, Port: 8765, HTTP mode, 3 tenants (mrrobot, westworld, fsociety).  
**DB:** `/home/skitterphuger/mcp/open-project-manager/tasks.db`  
**Start script:** `/home/skitterphuger/mcp/open-project-manager/start.sh`  
**Tokens:** Stored in `.env` (chmod 600) alongside start script.

---

## v0.2.0 Feature Architecture (Elliot)

Seven features scoped for v0.2.0. Decisions below cover schema DDL, MCP tool signatures, integration points, risks, and build order. All features implemented inside the existing `create_server()` closure model unless stated otherwise.

---

### Feature: due-dates

**Priority:** MEDIUM  
**Build order:** 1 (unblocked; schema-only additive change; everything downstream may reference `due_date`)

**Schema DDL:**
```sql
ALTER TABLE tasks ADD COLUMN due_date TEXT;
-- ISO 8601 UTC, nullable. Applied via try/except OperationalError on startup (column-exists is benign).
```

**`_VALID_UPDATE_COLUMNS`:** add `"due_date"`.

**`create_task()` / `update_task()`:** add `due_date: Optional[str] = None`. Validate format: must parse as `YYYY-MM-DD` or full ISO 8601 datetime; reject malformed strings. Store as-is (TEXT). ISO 8601 TEXT compares lexicographically, which is correct for date ordering in SQLite.

**`get_task()`:** `SELECT *` already includes new column — no change needed.

**`list_tasks()`:** keep compact fields unchanged; `due_date` is NOT in compact rows. Only visible in `get_task()` full detail.

**New MCP tools:**
```python
list_overdue_tasks(project=None, assignee=None, limit=20) -> str
# WHERE due_date IS NOT NULL AND due_date < NOW() AND status != 'done'
# Sorted: priority desc, due_date asc
# Compact rows: id, title, priority, status, due_date

list_due_soon_tasks(days: int = 7, project=None, assignee=None, limit=20) -> str
# WHERE due_date IS NOT NULL AND due_date >= NOW() AND due_date <= NOW()+days AND status != 'done'
# Same compact shape. Cap days to [1, 365].
```
Use `datetime.now(timezone.utc).isoformat()` and `(datetime.now(timezone.utc) + timedelta(days=days)).isoformat()` for boundary strings.

**Risks:** None significant. Additive nullable column. No index needed at this scale.

---

### Feature: full-text-search

**Priority:** MEDIUM  
**Build order:** 2 (schema-only; no feature dependencies)

**Schema DDL:**
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    id UNINDEXED,
    title,
    description,
    tags,
    content='tasks',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, id, title, description, tags)
    VALUES (new.rowid, new.id, new.title, new.description, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, id, title, description, tags)
    VALUES ('delete', old.rowid, old.id, old.title, old.description, old.tags);
    INSERT INTO tasks_fts(rowid, id, title, description, tags)
    VALUES (new.rowid, new.id, new.title, new.description, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, id, title, description, tags)
    VALUES ('delete', old.rowid, old.id, old.title, old.description, old.tags);
END;
```
After schema creation, rebuild index for pre-existing rows:
```python
conn.execute("INSERT INTO tasks_fts(tasks_fts) VALUES('rebuild')")
```

**New MCP tool:**
```python
search_tasks(query: str, project=None, status=None, limit=20) -> str
# FTS5 MATCH on tasks_fts; join to tasks for filters + compact fields
# Sorted by FTS5 rank (BM25). Returns {"tasks": [...compact rows + snippet?], "has_more": bool}
# query capped at _MAX_SHORT_FIELD chars
```
Query shape:
```sql
SELECT t.id, t.title, t.priority, t.status, t.assignee
FROM tasks t JOIN tasks_fts f ON t.rowid = f.rowid
WHERE tasks_fts MATCH ?
  [AND t.project = ?] [AND t.status = ?]
ORDER BY rank LIMIT ?+1
```

**Risks:** FTS5 not compiled in on some Linux distros. Add startup check: attempt `conn.execute("SELECT fts5(1)")` in try/except — if it fails, skip FTS table/trigger creation and set a `_fts_available = False` flag. `search_tasks` returns an error string if `_fts_available` is False. Server must NOT crash on FTS unavailability.

---

### Feature: bulk-operations

**Priority:** MEDIUM  
**Build order:** 3 (no schema changes; needs due-dates stable so `due_date` param is available in bulk create)

**Constant:** `_BULK_MAX = 50`. Return error if `len(items) > _BULK_MAX`.

**Implementation pattern:** Extract shared validation from `create_task()` / `update_task()` into `_validate_create_params()` and `_validate_update_params()` inner helper functions. Reuse from both single and bulk tools. Single transaction per bulk call (`conn.execute("BEGIN IMMEDIATE")` ... `conn.commit()`). Collect per-item results — do NOT fail-fast.

**New MCP tools:**
```python
create_tasks(tasks: list[dict]) -> str
# Each dict: same fields as create_task() (id + title required; others optional)
# Returns: {"created": ["id1", ...], "errors": [{"id": "x", "error": "..."}, ...]}

update_tasks(updates: list[dict]) -> str
# Each dict: task_id required + any update fields
# Returns: {"updated": ["id1", ...], "errors": [{"id": "x", "error": "..."}, ...]}

complete_tasks(ids: list[str]) -> str
# Returns: {"completed": ["id1", ...], "not_found": ["id2", ...]}
```

**Risks:** Large transactions briefly lock the SQLite writer. `_BULK_MAX = 50` limits exposure. `check_same_thread=False` already set.

---

### Feature: activity-log

**Priority:** LOW  
**Build order:** 4 (after bulk-ops so bulk hooks are added in the same pass; before REST so REST layer can pass actor)

**Schema DDL:**
```sql
CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT    NOT NULL,
    action     TEXT    NOT NULL,  -- 'created'|'updated'|'completed'|'deleted'|'dep_added'|'dep_removed'
    field      TEXT,              -- changed field name (for 'updated'); NULL for others
    old_value  TEXT,
    new_value  TEXT,
    actor      TEXT,              -- tenant_id when auth present, else 'system'
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_log_task_idx    ON activity_log(task_id);
CREATE INDEX IF NOT EXISTS activity_log_created_idx ON activity_log(created_at DESC);
```

**Actor resolution:** `ctx = mcp.get_context(); actor = getattr(getattr(ctx, 'auth', None), 'client_id', 'system')` inside async tools. Sync tools default to `'system'`. REST handlers pass actor explicitly.

**Logging helper (inner function in `create_server()`):**
```python
def _log(task_id, action, field=None, old_value=None, new_value=None, actor='system'):
    conn.execute(
        "INSERT INTO activity_log (task_id, action, field, old_value, new_value, actor, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, action, field, old_value, new_value, actor, _now())
    )
    # No commit — caller commits as part of enclosing transaction
```
For `update_task()` / `update_tasks()`: log one row per changed field. Requires `SELECT` of old values before `UPDATE`. Accept perf cost — it's audit data.

**New MCP tool:**
```python
get_task_activity(task_id: str, limit: int = 50) -> str
# Returns: {"activity": [{"id", "action", "field", "old_value", "new_value", "actor", "created_at"}], "count": N}
# Sorted created_at DESC. Limit capped at 200. Returns error if task not found.
```

**Risks:** `activity_log` grows unbounded. No pruning in v0.2.0 — defer to v0.3.0.

---

### Feature: export-import

**Priority:** MEDIUM  
**Build order:** 5 (after due-dates so `due_date` is in the export schema; after activity-log so exported tasks reflect full state)

**New MCP tools:**
```python
export_all_tasks(project: Optional[str] = None) -> str
# Returns JSON string:
# {
#   "version": "1.0",
#   "exported_at": "<ISO timestamp>",
#   "tasks": [<full task rows; tags as list, due_date included>],
#   "deps": [{"task_id": "...", "depends_on": "..."}]
# }
# If project filter: deps only included when BOTH tasks are in the exported set.

import_tasks(data: str, merge: bool = False) -> str
# data: JSON string matching export format
# merge=False: abort if any task ID already exists (returns error listing conflicts)
# merge=True: INSERT OR IGNORE for tasks + deps, silently skip existing
# Returns: {"imported": N, "skipped": N, "errors": [...]}
# data length cap: 5_000_000 chars (~5MB)
```
**Validation in `import_tasks`:** check `version` present, `tasks` is a list, each task has `id` + `title`. Run same field-length + priority/status checks as `create_task()`. Single transaction.

**Risks:** Large imports briefly lock the writer. 5MB cap is sufficient. No async HTTP involved.

---

### Feature: rest-api

**Priority:** HIGH  
**Build order:** 6 (after all MCP tools are stable; largest change to `__main__.py`)

**Architecture:** REST routes share the same Starlette app, same port, same SQLite connection. Mount REST at `/api/v1` BEFORE the MCP catch-all `Mount("/", mcp_asgi)`. Same process. No separate port.

**Auth:** Same bearer token scheme as MCP. REST handlers check `Authorization: Bearer <token>` via `hmac.compare_digest` against the same `tenant_keys` dict. 401 if missing/invalid. If server runs unauthenticated (no `tenant_keys`), REST is also unauthenticated — consistent behavior.

**Opt-in via CLI:** Yes. `--rest-api` flag. REST routes NOT mounted unless flag is set. Preserves minimal default surface area.

**Implementation:**
- Add `enable_rest: bool = False` param to `create_server()`. No return-type change — attach result as `mcp._rest_router` (internal attribute).
- `_build_rest_router(conn, _lock, tenant_keys)` inner function returns a Starlette `Router`.
- In `__main__.py` when `--rest-api`: `routes = [Mount("/api/v1", mcp._rest_router), Mount("/", mcp_asgi)]`.
- Auth helper inside router factory: `async def _check_auth(request) -> Response | None`.

**Endpoints:**
```
GET    /api/v1/tasks                      list_tasks  (query params: project, assignee, status, priority, limit, offset)
POST   /api/v1/tasks                      create_task (JSON body)
GET    /api/v1/tasks/{id}                 get_task
PATCH  /api/v1/tasks/{id}                 update_task (JSON body, partial)
DELETE /api/v1/tasks/{id}?confirm=true    delete_task (confirm=true replaces human_approval guard)
GET    /api/v1/projects                   list_projects
GET    /api/v1/stats                      get_stats  ← closes CHARTER GET /stats gap
```

**GET /stats decision:** Yes, implement it here. CHARTER explicitly listed `GET /stats` as in-scope but it was never wired as an HTTP route. The REST API layer closes that gap.

**Response format:** `Content-Type: application/json`. Errors: `{"error": "message"}`. Success payloads mirror MCP tool return shapes (already compact JSON). HTTP status: 200 OK, 201 Created, 400 Bad Request, 401 Unauthorized, 404 Not Found, 409 Conflict (duplicate ID on POST).

**`_FixArgumentsMiddleware`:** Stays wrapping the full Starlette app. No-op for REST routes (they send proper JSON bodies).

**Breaking change assessment:** `create_server()` return type remains `FastMCP`. `_rest_router` is internal. Existing tests unaffected.

---

### Feature: webhooks

**Priority:** LOW  
**Build order:** 7 (last; new external dependency; highest risk)

**Schema DDL:**
```sql
CREATE TABLE IF NOT EXISTS webhooks (
    id         TEXT    PRIMARY KEY,
    url        TEXT    NOT NULL,
    project    TEXT,              -- NULL = global (fires for all projects)
    events     TEXT    NOT NULL,  -- JSON array: ["task.created", "task.updated", "task.completed", "task.deleted"]
    secret     TEXT,              -- optional HMAC-SHA256 signing secret (stored plaintext — acceptable for local-first)
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL
);
```

**Valid events:** `{"task.created", "task.updated", "task.completed", "task.deleted"}`.

**New MCP tools:**
```python
register_webhook(id: str, url: str, events: list[str], project: Optional[str] = None, secret: Optional[str] = None) -> str
# Validates: url MUST be https://. Resolve hostname; reject RFC1918 + loopback + link-local (SSRF guard).
# events must be non-empty subset of valid event names.
# Returns: {"id": id, "url": url, "events": events, "project": project}

list_webhooks(project: Optional[str] = None) -> str
# Returns: {"webhooks": [{"id", "url", "project", "events", "enabled"}]}
# Does NOT return secret values.

delete_webhook(id: str, human_approval: bool = False) -> str
# Requires human_approval=True (mirrors delete_task pattern).
```

**Delivery:** Inner async function `_fire_webhooks(event: str, task_id: str, payload: dict)` called via `asyncio.create_task()` (fire-and-forget) at end of write tools. Fetches matching enabled webhooks, POSTs JSON payload to each. Uses `httpx.AsyncClient` with `timeout=5.0`. Signs with HMAC-SHA256 if secret set: header `X-Hub-Signature-256: sha256=<hex>` (GitHub webhook convention). No retries in v0.2.0.

**Payload shape:**
```json
{"event": "task.created", "task_id": "auth-login-ui", "timestamp": "...", "data": {<compact task>}}
```

**SSRF protection (mandatory):**
1. URL must start with `https://`.
2. `socket.getaddrinfo(hostname, 443)` — reject if any resolved address is RFC1918 (10/8, 172.16-31/12, 192.168/16), loopback (127/8, ::1), or link-local (169.254/16, fe80::/10).
3. Validation runs at registration time, not delivery time.

**New dependency:** `httpx>=0.24` in optional extras `[webhooks]` in `pyproject.toml`. Guarded with `try/except ImportError` — if httpx not installed, `register_webhook` returns an error instructing the user to install `open-project-manager-mcp[webhooks]`.

**Risks:** SSRF if URL validation has gaps. Silent delivery failures — no dead-letter queue in v0.2.0. Webhook table unbounded but low-volume by design.

---

---

## 2026-04-01: Webhook SSRF — DNS rebinding resolution

**Decision:** Keep registration-time SSRF check only. Rely on HTTPS + TLS cert verification as primary mitigation (httpx `verify=True` explicit). DNS rebinding requires a valid TLS cert for the attacker's domain on the internal target — not practically exploitable. Per-fire DNS re-validation rejected: adds latency, availability risk, doesn't close TOCTOU window.

---

## CLOSED ITEM 2026-04-01: Webhook SSRF — DNS rebinding

**Flagged by:** Dom (security audit v0.2.0)  
**Status:** Open — needs Elliot decision  

**Issue:** SSRF validation runs at webhook registration time only. An attacker controlling a domain with a low TTL could register a legitimate HTTPS URL that passes the RFC1918/loopback blocklist check, then remap the DNS record to an internal address after registration. Subsequent webhook deliveries would reach the internal host.

**Options:**
1. **Re-validate on each fire** — resolve the hostname and re-check blocklist before every HTTP delivery. Adds latency and a DNS lookup per delivery; closes the window completely.
2. **Accept HTTPS cert validation as mitigation** — internal services typically don't have valid public TLS certificates; an invalid cert would cause `httpx` to reject the connection. Lower operational cost; not a complete mitigation (attacker with a wildcard cert or internal CA defeats it).

**Needs Elliot decision.**

## 2026-04-01: Ralph integrated with OPM

Ralph now checks list_ready_tasks first, then GitHub Issues as fallback.
Dedicated `ralph` bearer token provisioned. MCP config updated in both squad repos.

---

### Build order summary

| # | Feature | Key rationale |
|---|---------|---------------|
| 1 | due-dates | Smallest schema change; downstream features include `due_date` |
| 2 | full-text-search | Schema additive; no feature deps |
| 3 | bulk-operations | Logic-only; due-dates must be stable for `due_date` in bulk create |
| 4 | activity-log | Schema additive; hooks all write paths; before REST so REST can pass actor |
| 5 | export-import | Logic-only; needs due-dates in schema |
| 6 | rest-api | Largest surface area change; all tools must be stable first |
| 7 | webhooks | New dep; most risk; isolated last |
