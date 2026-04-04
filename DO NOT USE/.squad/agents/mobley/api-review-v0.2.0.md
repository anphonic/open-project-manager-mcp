# REST API & Webhooks — Integration Review v0.2.0

**Reviewer:** Mobley (Samar Asif) — Integration & External Systems  
**Date:** 2025-01  
**Sources reviewed:** `darlene-brief-v0.2.0.md`, `decisions.md` (v0.2.0 section), `__main__.py`, `server.py`

---

## 1. REST API Mounting — Middleware Placement

**Verdict: Correct. One note worth flagging.**

Elliot's brief wires it as:

```python
routes = [Mount("/api/v1", mcp._rest_router), Mount("/", mcp_asgi)]
app = Starlette(routes=routes, lifespan=_make_lifespan(mcp_asgi))
app = _FixArgumentsMiddleware(app)
```

This is the right Starlette pattern. REST routes live *inside* the Starlette app, before the MCP catch-all `Mount("/")`. `_FixArgumentsMiddleware` wraps the entire outer app.

**What the middleware does to REST requests:** `_FixArgumentsMiddleware` intercepts every HTTP POST. For a REST `POST /api/v1/tasks` it will buffer the full body, parse it as JSON, check for `method == "tools/call"`, not find it, and pass the original body through unchanged. This is a correct no-op — no behaviour change. Mild inefficiency (one extra buffer allocation per REST POST) but immaterial.

**Nothing to change here.** The mount order and middleware position are both right.

---

## 2. GET /stats — Response Shape

**Verdict: Shape works, but sparse keys will surprise integrators. Recommend normalising.**

The `get_stats()` MCP tool in `server.py` produces:

```json
{
  "by_status":   {"in_progress": 3, "pending": 12},
  "by_priority": {"high": 2, "critical": 1},
  "oldest_open": "2025-01-04T09:15:00+00:00"
}
```

Both `by_status` and `by_priority` are built from `GROUP BY` — they only emit keys that have at least one row. On an empty database, both will be `{}`. On a database with no `critical` tasks, `critical` simply doesn't appear.

**Problem for integrators:** Dashboard code that does `stats.by_status.done ?? 0` is fine. Code that iterates `Object.entries(stats.by_priority)` to render a table gets a variable number of columns. Any monitoring script that compares snapshots will emit spurious "key added/removed" diffs.

**Recommended response shape for `GET /api/v1/stats`:**

```json
{
  "by_status": {
    "pending":     12,
    "in_progress":  3,
    "done":         0,
    "blocked":      0
  },
  "by_priority": {
    "critical":  1,
    "high":      2,
    "medium":    0,
    "low":       0
  },
  "oldest_open": "2025-01-04T09:15:00+00:00",
  "total":       15,
  "generated_at": "2025-01-10T14:00:00+00:00"
}
```

Implementation: after the GROUP BY queries, merge results into a pre-seeded dict with zeros:

```python
status_defaults = {s: 0 for s in ("pending", "in_progress", "done", "blocked")}
status_defaults.update(by_status)

priority_defaults = {p: 0 for p in ("critical", "high", "medium", "low")}
priority_defaults.update(by_priority)

total = sum(status_defaults.values())
```

`generated_at` is just `_now()`. `oldest_open` is already nullable — that's fine.

---

## 3. Webhook Fire-and-Forget — Task Lifecycle Bug 🔴

**Verdict: Will silently drop deliveries under Python 3.12+. Fix required.**

The brief calls:
```python
asyncio.create_task(_fire_webhooks("task.created", id, {...}))
```

**The problem:** `asyncio.create_task()` returns a `Task` object. If no reference to that object is kept, the garbage collector can collect it before it completes — particularly if the event loop is briefly idle or memory pressure is high. Python 3.12 formalised this with a `DeprecationWarning`; Python 3.13+ will make it an error.

**Fix — store references in a module-level set inside `create_server()`:**

```python
_background_tasks: set[asyncio.Task] = set()

def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
```

Then every call site becomes:
```python
_fire_and_forget(_fire_webhooks("task.created", id, {...}))
```

This is a one-liner change and costs nothing at runtime. `_background_tasks` drains itself automatically via the done callback.

**Secondary concern:** `_fire_webhooks` does a `conn.execute("SELECT project FROM tasks WHERE id = ?")` call inside the background task, outside the `_lock`. This is fine for reads on `check_same_thread=False` connections, and since the task is created after `conn.commit()` the data is visible. No bug here — just worth knowing if someone later adds writes to `_fire_webhooks`.

---

## 4. HMAC-SHA256 Signing

**Verdict: Implementation is correct. Header name choice is fine. One import redundancy.**

The brief signs with:
```python
import hmac as _hmac, hashlib
sig = _hmac.new(row["secret"].encode(), body, hashlib.sha256).hexdigest()
headers["X-Hub-Signature-256"] = f"sha256={sig}"
```

**Header name:** `X-Hub-Signature-256` is GitHub's exact header name and format (`sha256=<lowercase hex>`). Reusing it is a reasonable choice — many existing webhook consumer libraries already know how to verify it. The alternative `X-OPM-Signature-256` is more distinctive but requires consumers to write new verification code. I recommend keeping GitHub's format; document it clearly.

**Payload to sign:** The brief signs `body` — the raw UTF-8 JSON bytes — before any HTTP transport encoding. This is the correct payload. Signing the pre-serialised bytes means the receiver can verify without re-serialising.

**Import redundancy:** `server.py` already has `import hmac` at the top. The `import hmac as _hmac` inside `_fire_webhooks` is a local alias to avoid shadowing, but since `_fire_webhooks` is a closure inside `create_server()` it already has access to the outer `hmac`. The local import is harmless but unnecessary. Not a bug; clean it up if desired.

**For implementors verifying signatures (documentation to provide):**
```python
import hmac, hashlib

def verify_opm_webhook(secret: str, body: bytes, signature_header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

---

## 5. Webhook Payload Schema

**Verdict: Top-level shape is correct. `data` field needs specifying per event. Critical gap for `task.deleted`.**

The brief specifies:
```json
{"event": "task.created", "task_id": "auth-login-ui", "timestamp": "...", "data": {<compact task>}}
```

### `data` field — recommended content per event type

| Event | `data` contents |
|---|---|
| `task.created` | Full task object: `{id, title, description, project, priority, status, assignee, tags, due_date, created_at, updated_at}` |
| `task.updated` | Same full task object (post-update state) + optional `"changed_fields": ["status", "priority"]` |
| `task.completed` | `{id, title, project, status: "done", updated_at}` — compact is fine |
| `task.deleted` | **Last known state of the task** (see below) |

### `task.deleted` — critical timing bug 🔴

The brief calls `_fire_webhooks` *after* `conn.commit()` for a delete:

```python
conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
conn.commit()
asyncio.create_task(_fire_webhooks("task.deleted", task_id, {...}))
```

Then inside `_fire_webhooks`, the project-filter logic does:
```python
task_row = conn.execute("SELECT project FROM tasks WHERE id = ?", (task_id,)).fetchone()
```

**This returns `None` because the task is already deleted.** Any project-scoped webhook for a `task.deleted` event will never fire — the project check silently skips it.

**Fix:** Fetch the task data *before* deletion and pass it into `_fire_webhooks` as the payload. The `data` dict for the delete webhook should contain the last known task state. Callers can then use this for audit trails, cache invalidation, etc.

```python
# Before DELETE:
task_snapshot = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
task_data = _row(task_snapshot) if task_snapshot else {"id": task_id}

# ... delete, commit ...
_fire_and_forget(_fire_webhooks("task.deleted", task_id, task_data))
```

And in `_fire_webhooks`, pass `task_data["project"]` directly rather than re-querying:

```python
async def _fire_webhooks(event: str, task_id: str, payload: dict) -> None:
    # payload["project"] is available for project-filter check
    task_project = payload.get("project")
    ...
    for row in rows:
        if row["project"] is not None and row["project"] != task_project:
            continue
```

---

## 6. REST Error Responses

**Verdict: Consistent `{"error": "..."}` shape is good. Recommend adding `code` for machine parsing.**

The brief uses `{"error": "message string"}` uniformly across all endpoints. This is clean and consistent. Do not adopt RFC 7807 (Problem Details) — it's overengineered for this surface area.

**Recommended addition:** an optional machine-readable `code` field so integrators can branch without string-matching:

```json
{"error": "task 'auth-ui' already exists", "code": "CONFLICT"}
{"error": "id and title are required", "code": "VALIDATION_ERROR"}
{"error": "invalid API key", "code": "UNAUTHORIZED"}
{"error": "task 'xyz' not found", "code": "NOT_FOUND"}
{"error": "database error", "code": "INTERNAL_ERROR"}
```

**Proposed code enum:**
| HTTP Status | `code` |
|---|---|
| 400 | `VALIDATION_ERROR` |
| 401 | `UNAUTHORIZED` |
| 404 | `NOT_FOUND` |
| 409 | `CONFLICT` |
| 500 | `INTERNAL_ERROR` |

If Darlene wants to keep it minimal, at a minimum distinguish `CONFLICT` (409) from `VALIDATION_ERROR` (400) — those are the two cases callers most commonly need to branch on.

---

## 7. Additional Bug: `_update_task` Activity Log Fires Before 404 Check 🔴

Not in the review scope but caught in the same pass — flagging for Darlene.

In the REST `_update_task` handler:
```python
async with _lock:
    cur = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
    _log(task_id, "updated", actor="rest-api")   # ← logged before checking rowcount
    conn.commit()
    if cur.rowcount == 0:
        return JSONResponse({"error": f"task '{task_id}' not found"}, status_code=404)
```

If the task doesn't exist, `cur.rowcount == 0`, but `_log(...)` has already been called and `conn.commit()` has already executed — leaving an orphaned activity log row for a non-existent task, and returning 404 after a committed write.

**Fix:** check `cur.rowcount` before logging and committing:

```python
async with _lock:
    cur = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
    if cur.rowcount == 0:
        conn.rollback()
        return JSONResponse({"error": f"task '{task_id}' not found"}, status_code=404)
    _log(task_id, "updated", actor="rest-api")
    conn.commit()
```

Also note: the REST `_update_task` logs a single `"updated"` activity row rather than one row per changed field (unlike the MCP `update_task`). This is inconsistent with the activity-log feature design. Darlene should either (a) align REST to also log per-field, or (b) accept the inconsistency and document it. Option (a) requires fetching `old_row` before the UPDATE, same as the MCP path.

---

## Summary for Darlene

| # | Item | Severity | Action |
|---|------|----------|--------|
| 3 | `asyncio.create_task` GC bug | 🔴 Must fix | Use `_fire_and_forget` helper with set reference |
| 5 | `task.deleted` project filter broken | 🔴 Must fix | Capture task snapshot before DELETE; pass project into `_fire_webhooks` |
| 7 | REST `_update_task` logs before 404 check | 🔴 Must fix | Check `rowcount` first; rollback if 0 |
| 2 | `GET /stats` sparse keys | 🟡 Should fix | Normalise with zeros for all valid status/priority values |
| 5 | `data` field shape undefined | 🟡 Should fix | Use full task object for all events |
| 6 | No machine-readable error `code` | 🟡 Nice to have | Add `code` field to error responses |
| 4 | `import hmac as _hmac` redundant | ⚪ Cosmetic | Remove local re-import |
| 7 | REST activity log inconsistency (per-field vs single row) | 🟡 Decide | Align or document |

The overall architecture (same port, same Starlette app, `_FixArgumentsMiddleware` placement, bearer token reuse, SSRF guards at registration time) is sound. The three 🔴 items are all in the webhooks/REST write paths and will cause data loss or incorrect behaviour in production.
