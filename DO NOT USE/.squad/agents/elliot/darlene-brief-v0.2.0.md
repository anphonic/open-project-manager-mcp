# Darlene — Implementation Brief v0.2.0

**From:** Elliot  
**To:** Darlene  
**Scope:** 7 features for open-project-manager-mcp v0.2.0  
**Working files:** `src/open_project_manager_mcp/server.py`, `src/open_project_manager_mcp/__main__.py`, `pyproject.toml`

Read this top-to-bottom. Each section is complete. Do NOT add features, change signatures, or deviate from patterns without checking back.

---

## Existing code mental model

- `server.py` exports `create_server(db_path, tenant_keys, server_url, transport_security) -> FastMCP`
- Everything is a closure inside `create_server()`. `conn` and `_lock` are shared state. No module globals.
- Tools registered with `@mcp.tool()`. Type annotations drive MCP schema.
- `_now()` → ISO 8601 UTC string. `_row(row)` → dict with tags decoded from JSON.
- `_VALID_UPDATE_COLUMNS` frozenset guards the f-string in `update_task()`.
- `__main__.py` builds Starlette app, applies `_FixArgumentsMiddleware`, runs uvicorn.
- Auth: `ApiKeyVerifier` with `hmac.compare_digest`. `tenant_keys` is `dict[str, str]` (flat: `{tenant_id: api_key}`).

---

## Build order

**Implement in this exact order.** Each feature is independently testable before moving to the next.

1. `due-dates`
2. `full-text-search`
3. `bulk-operations`
4. `activity-log`
5. `export-import`
6. `rest-api`
7. `webhooks`

---

## 1. due-dates

### Schema migration

Add to `create_server()`, after `conn.executescript(_SCHEMA)`:
```python
try:
    conn.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass  # column already exists
```

Also add `due_date TEXT` to the `CREATE TABLE tasks` DDL in `_SCHEMA` so fresh databases get it directly.

### Validation helper

Add inner function:
```python
def _validate_due_date(value: str) -> bool:
    """Return True if value is a valid ISO 8601 date or datetime string."""
    from datetime import date
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            datetime.strptime(value.rstrip("Z"), fmt.rstrip("%z")) if fmt.endswith("%z") else datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False
```
Actually — simpler approach: attempt `datetime.fromisoformat(value.replace("Z", "+00:00"))`. That covers all cases in Python 3.7+. Return True on success, False on ValueError.

### `create_task()` update

Add param: `due_date: Optional[str] = None`

In validation block add:
```python
if due_date is not None and not _validate_due_date(due_date):
    return "Error: 'due_date' must be a valid ISO 8601 date or datetime string"
```

Add `due_date` to INSERT statement and values tuple.

### `update_task()` update

Add param: `due_date: Optional[str] = None`

In validation block add:
```python
if due_date is not None and not _validate_due_date(due_date):
    return "Error: 'due_date' must be a valid ISO 8601 date or datetime string"
```

Add `"due_date"` to `_VALID_UPDATE_COLUMNS` frozenset.

In the updates dict block add:
```python
if due_date is not None:
    updates["due_date"] = due_date
```

### New tool: `list_overdue_tasks`

```python
@mcp.tool()
def list_overdue_tasks(
    project: Optional[str] = None,
    assignee: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List tasks whose due_date is in the past and are not done."""
    limit = max(1, min(limit, _MAX_LIMIT))
    now = _now()
    conditions = ["due_date IS NOT NULL", "due_date < ?", "status != 'done'"]
    params: list[object] = [now]
    if project:
        conditions.append("project = ?")
        params.append(project)
    if assignee:
        conditions.append("assignee = ?")
        params.append(assignee)
    where = "WHERE " + " AND ".join(conditions)
    priority_case = _PRIORITY_CASE
    try:
        rows = conn.execute(
            f"SELECT id, title, priority, status, due_date FROM tasks"
            f" {where} ORDER BY {priority_case}, due_date LIMIT ? OFFSET 0",
            params + [limit + 1],
        ).fetchall()
    except sqlite3.Error:
        return "Error: database error listing overdue tasks"
    has_more = len(rows) > limit
    return json.dumps({"tasks": [dict(r) for r in rows[:limit]], "has_more": has_more})
```

### New tool: `list_due_soon_tasks`

```python
@mcp.tool()
def list_due_soon_tasks(
    days: int = 7,
    project: Optional[str] = None,
    assignee: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List tasks due within the next N days (1–365)."""
    days = max(1, min(days, 365))
    limit = max(1, min(limit, _MAX_LIMIT))
    from datetime import timedelta
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    horizon = (now_dt + timedelta(days=days)).isoformat()
    conditions = ["due_date IS NOT NULL", "due_date >= ?", "due_date <= ?", "status != 'done'"]
    params: list[object] = [now, horizon]
    if project:
        conditions.append("project = ?")
        params.append(project)
    if assignee:
        conditions.append("assignee = ?")
        params.append(assignee)
    where = "WHERE " + " AND ".join(conditions)
    priority_case = _PRIORITY_CASE
    try:
        rows = conn.execute(
            f"SELECT id, title, priority, status, due_date FROM tasks"
            f" {where} ORDER BY {priority_case}, due_date LIMIT ?",
            params + [limit + 1],
        ).fetchall()
    except sqlite3.Error:
        return "Error: database error listing due-soon tasks"
    has_more = len(rows) > limit
    return json.dumps({"tasks": [dict(r) for r in rows[:limit]], "has_more": has_more})
```

---

## 2. full-text-search

### Schema additions

Append to `_SCHEMA` (after existing table definitions):
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

### Startup FTS availability check

In `create_server()`, after `conn.executescript(_SCHEMA)`:
```python
_fts_available = False
try:
    conn.execute("INSERT INTO tasks_fts(tasks_fts) VALUES('rebuild')")
    conn.commit()
    _fts_available = True
except sqlite3.OperationalError:
    # FTS5 not compiled in this SQLite build — search_tasks will return an error
    pass
```

### New tool: `search_tasks`

```python
@mcp.tool()
def search_tasks(
    query: str,
    project: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Full-text search across task title, description, and tags."""
    if not _fts_available:
        return "Error: full-text search is not available (SQLite FTS5 not compiled in this build)"
    if not query or not query.strip():
        return "Error: query must not be empty"
    if len(query) > _MAX_SHORT_FIELD:
        return f"Error: query exceeds maximum length of {_MAX_SHORT_FIELD}"
    limit = max(1, min(limit, _MAX_LIMIT))
    conditions: list[str] = ["tasks_fts MATCH ?"]
    params: list[object] = [query]
    if project:
        conditions.append("t.project = ?")
        params.append(project)
    if status:
        conditions.append("t.status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(conditions)
    try:
        rows = conn.execute(
            f"SELECT t.id, t.title, t.priority, t.status, t.assignee"
            f" FROM tasks t JOIN tasks_fts f ON t.rowid = f.rowid"
            f" {where} ORDER BY rank LIMIT ?",
            params + [limit + 1],
        ).fetchall()
    except sqlite3.OperationalError as e:
        return f"Error: search query failed — {e}"
    except sqlite3.Error:
        return "Error: database error during search"
    has_more = len(rows) > limit
    return json.dumps({"tasks": [dict(r) for r in rows[:limit]], "has_more": has_more})
```

---

## 3. bulk-operations

### Constants

Add near the top of `create_server()`:
```python
_BULK_MAX = 50
```

### Shared validation helpers

Extract the validation logic from `create_task()` and `update_task()` into inner functions. These replace the inline validation in both single and bulk tools:

```python
def _validate_create_params(p: dict) -> Optional[str]:
    """Returns error string or None."""
    for field, max_len in [("id", _MAX_SHORT_FIELD), ("title", _MAX_SHORT_FIELD),
                            ("project", _MAX_SHORT_FIELD), ("assignee", _MAX_SHORT_FIELD)]:
        val = p.get(field)
        if val is not None and len(val) > max_len:
            return f"'{field}' exceeds maximum length of {max_len}"
    desc = p.get("description")
    if desc is not None and len(desc) > _MAX_DESCRIPTION:
        return f"'description' exceeds maximum length of {_MAX_DESCRIPTION}"
    pri = p.get("priority", "medium")
    if pri not in VALID_PRIORITIES:
        return f"invalid priority '{pri}'"
    due = p.get("due_date")
    if due is not None and not _validate_due_date(due):
        return "'due_date' must be a valid ISO 8601 date or datetime string"
    if not p.get("id"):
        return "'id' is required"
    if not p.get("title"):
        return "'title' is required"
    return None
```

```python
def _validate_update_params(p: dict) -> Optional[str]:
    """Returns error string or None."""
    for field, max_len in [("title", _MAX_SHORT_FIELD), ("project", _MAX_SHORT_FIELD),
                            ("assignee", _MAX_SHORT_FIELD)]:
        val = p.get(field)
        if val is not None and len(val) > max_len:
            return f"'{field}' exceeds maximum length of {max_len}"
    desc = p.get("description")
    if desc is not None and len(desc) > _MAX_DESCRIPTION:
        return f"'description' exceeds maximum length of {_MAX_DESCRIPTION}"
    pri = p.get("priority")
    if pri is not None and pri not in VALID_PRIORITIES:
        return f"invalid priority '{pri}'"
    status = p.get("status")
    if status is not None and status not in VALID_STATUSES:
        return f"invalid status '{status}'"
    due = p.get("due_date")
    if due is not None and not _validate_due_date(due):
        return "'due_date' must be a valid ISO 8601 date or datetime string"
    return None
```

Update `create_task()` and `update_task()` to call these helpers rather than repeating validation inline.

### New tool: `create_tasks`

```python
@mcp.tool()
async def create_tasks(tasks: list[dict]) -> str:
    """Bulk create tasks in a single transaction. Max 50 items."""
    if len(tasks) > _BULK_MAX:
        return f"Error: maximum {_BULK_MAX} tasks per bulk call"
    created, errors = [], []
    now = _now()
    async with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for p in tasks:
                err = _validate_create_params(p)
                if err:
                    errors.append({"id": p.get("id", "?"), "error": err})
                    continue
                task_id = p["id"]
                tags_json = json.dumps(p["tags"]) if p.get("tags") else None
                try:
                    conn.execute(
                        "INSERT INTO tasks (id, title, description, project, priority, status,"
                        " assignee, tags, due_date, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                        (task_id, p["title"], p.get("description"),
                         p.get("project", "default"), p.get("priority", "medium"),
                         p.get("assignee"), tags_json, p.get("due_date"), now, now),
                    )
                    created.append(task_id)
                except sqlite3.IntegrityError:
                    errors.append({"id": task_id, "error": "already exists"})
            conn.commit()
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            return "Error: database error during bulk create"
    return json.dumps({"created": created, "errors": errors})
```

### New tool: `update_tasks`

```python
@mcp.tool()
async def update_tasks(updates: list[dict]) -> str:
    """Bulk update tasks in a single transaction. Each item needs task_id. Max 50 items."""
    if len(updates) > _BULK_MAX:
        return f"Error: maximum {_BULK_MAX} updates per bulk call"
    updated, errors = [], []
    now = _now()
    async with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for p in updates:
                task_id = p.get("task_id")
                if not task_id:
                    errors.append({"id": "?", "error": "task_id is required"})
                    continue
                err = _validate_update_params(p)
                if err:
                    errors.append({"id": task_id, "error": err})
                    continue
                field_map: dict[str, object] = {}
                for field in ("title", "description", "priority", "project", "status",
                               "assignee", "due_date"):
                    if field in p:
                        field_map[field] = p[field]
                if "tags" in p:
                    field_map["tags"] = json.dumps(p["tags"])
                if not field_map:
                    errors.append({"id": task_id, "error": "no fields to update"})
                    continue
                field_map["updated_at"] = now
                set_clause = ", ".join(f"{k} = ?" for k in field_map)
                values = list(field_map.values()) + [task_id]
                cur = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
                if cur.rowcount == 0:
                    errors.append({"id": task_id, "error": "not found"})
                else:
                    updated.append(task_id)
            conn.commit()
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            return "Error: database error during bulk update"
    return json.dumps({"updated": updated, "errors": errors})
```

### New tool: `complete_tasks`

```python
@mcp.tool()
async def complete_tasks(ids: list[str]) -> str:
    """Mark multiple tasks as done in a single transaction. Max 50 items."""
    if len(ids) > _BULK_MAX:
        return f"Error: maximum {_BULK_MAX} ids per bulk call"
    completed, not_found = [], []
    now = _now()
    async with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for task_id in ids:
                cur = conn.execute(
                    "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
                    (now, task_id),
                )
                if cur.rowcount == 0:
                    not_found.append(task_id)
                else:
                    completed.append(task_id)
            conn.commit()
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            return "Error: database error during bulk complete"
    return json.dumps({"completed": completed, "not_found": not_found})
```

---

## 4. activity-log

### Schema DDL

Add to `_SCHEMA`:
```sql
CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT    NOT NULL,
    action     TEXT    NOT NULL,
    field      TEXT,
    old_value  TEXT,
    new_value  TEXT,
    actor      TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_log_task_idx    ON activity_log(task_id);
CREATE INDEX IF NOT EXISTS activity_log_created_idx ON activity_log(created_at DESC);
```

Valid `action` values: `'created'`, `'updated'`, `'completed'`, `'deleted'`, `'dep_added'`, `'dep_removed'`.

### Logging helper

Add inside `create_server()`:
```python
def _log(task_id: str, action: str, field: Optional[str] = None,
         old_value: Optional[str] = None, new_value: Optional[str] = None,
         actor: str = "system") -> None:
    """Insert one activity_log row. Caller must commit."""
    conn.execute(
        "INSERT INTO activity_log (task_id, action, field, old_value, new_value, actor, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, action, field, old_value, new_value, actor, _now()),
    )
```

### Actor resolution

Add helper:
```python
def _actor() -> str:
    try:
        ctx = mcp.get_context()
        return getattr(getattr(ctx, "auth", None), "client_id", None) or "system"
    except Exception:
        return "system"
```

### Hooking into existing write tools

**`create_task()`** — after `conn.execute(INSERT ...)`, before `conn.commit()`:
```python
_log(id, "created", actor=_actor())
```

**`update_task()`** — requires capturing old values before the UPDATE:
```python
# Before UPDATE:
old_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
# After UPDATE (before commit), for each changed field:
if old_row:
    actor = _actor()
    for field_name in (set(updates.keys()) - {"updated_at"}):
        _log(task_id, "updated", field=field_name,
             old_value=str(old_row[field_name]) if old_row[field_name] is not None else None,
             new_value=str(updates[field_name]),
             actor=actor)
```

**`complete_task()`** — after UPDATE, before commit:
```python
_log(task_id, "completed", actor=_actor())
```

**`delete_task()`** — after DELETE, before commit:
```python
_log(task_id, "deleted", actor=_actor())
```

**`add_dependency()`** — after INSERT:
```python
_log(task_id, "dep_added", new_value=depends_on_id, actor=_actor())
```

**`remove_dependency()`** — after DELETE:
```python
_log(task_id, "dep_removed", old_value=depends_on_id, actor=_actor())
```

**Bulk tools** — log per-item inside the loop, before `conn.commit()`.

### New tool: `get_task_activity`

```python
@mcp.tool()
def get_task_activity(task_id: str, limit: int = 50) -> str:
    """Return recent activity for a task, newest first."""
    limit = max(1, min(limit, 200))
    if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
        return f"Error: task '{task_id}' not found"
    try:
        rows = conn.execute(
            "SELECT id, action, field, old_value, new_value, actor, created_at"
            " FROM activity_log WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    except sqlite3.Error:
        return "Error: database error reading activity log"
    return json.dumps({"activity": [dict(r) for r in rows], "count": len(rows)})
```

---

## 5. export-import

### New tool: `export_all_tasks`

```python
@mcp.tool()
def export_all_tasks(project: Optional[str] = None) -> str:
    """Export all tasks and dependency edges as a JSON snapshot."""
    try:
        params: list[object] = []
        where = ""
        if project:
            where = "WHERE project = ?"
            params.append(project)
        task_rows = conn.execute(f"SELECT * FROM tasks {where}", params).fetchall()
        task_ids = {r["id"] for r in task_rows}
        tasks_out = []
        for r in task_rows:
            t = _row(r)  # decodes tags JSON
            tasks_out.append(t)

        dep_rows = conn.execute("SELECT task_id, depends_on FROM task_deps").fetchall()
        deps_out = [
            {"task_id": r["task_id"], "depends_on": r["depends_on"]}
            for r in dep_rows
            if r["task_id"] in task_ids and r["depends_on"] in task_ids
        ]
    except sqlite3.Error:
        return "Error: database error during export"
    return json.dumps({
        "version": "1.0",
        "exported_at": _now(),
        "tasks": tasks_out,
        "deps": deps_out,
    })
```

### New tool: `import_tasks`

```python
_IMPORT_MAX_BYTES = 5_000_000

@mcp.tool()
async def import_tasks(data: str, merge: bool = False) -> str:
    """Import tasks from a JSON snapshot produced by export_all_tasks().
    merge=False aborts on duplicate IDs; merge=True skips them."""
    if len(data) > _IMPORT_MAX_BYTES:
        return f"Error: data exceeds maximum size of {_IMPORT_MAX_BYTES} bytes"
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON — {e}"

    if not isinstance(payload.get("tasks"), list):
        return "Error: payload must contain a 'tasks' list"

    tasks = payload["tasks"]
    deps = payload.get("deps", [])
    errors: list[str] = []
    imported = 0
    skipped = 0
    now = _now()

    if not merge:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM tasks WHERE id IN (%s)" % ",".join("?" * len(tasks)),
                [t.get("id") for t in tasks],
            ).fetchall()
        } if tasks else set()
        conflicts = [t.get("id") for t in tasks if t.get("id") in existing]
        if conflicts:
            return json.dumps({"error": "conflict", "duplicate_ids": conflicts})

    async with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for t in tasks:
                err = _validate_create_params(t)
                if err:
                    errors.append(f"{t.get('id', '?')}: {err}")
                    continue
                tags_json = json.dumps(t["tags"]) if isinstance(t.get("tags"), list) else t.get("tags")
                insert_or = "INSERT OR IGNORE" if merge else "INSERT"
                try:
                    cur = conn.execute(
                        f"{insert_or} INTO tasks"
                        " (id, title, description, project, priority, status, assignee,"
                        "  tags, due_date, sort_order, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (t["id"], t["title"], t.get("description"),
                         t.get("project", "default"), t.get("priority", "medium"),
                         t.get("status", "pending"), t.get("assignee"),
                         tags_json, t.get("due_date"), t.get("sort_order"),
                         t.get("created_at", now), t.get("updated_at", now)),
                    )
                    if cur.rowcount == 0:
                        skipped += 1
                    else:
                        imported += 1
                except sqlite3.IntegrityError as e:
                    errors.append(f"{t['id']}: {e}")

            for d in deps:
                tid = d.get("task_id")
                dep = d.get("depends_on")
                if tid and dep:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                            (tid, dep),
                        )
                    except sqlite3.Error:
                        pass  # dep edges are best-effort on import
            conn.commit()
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            return "Error: database error during import"

    return json.dumps({"imported": imported, "skipped": skipped, "errors": errors})
```

---

## 6. rest-api

### CLI flag

In `__main__.py` `argparse` block, add:
```python
parser.add_argument(
    "--rest-api",
    action="store_true",
    dest="rest_api",
    help="Mount REST API endpoints at /api/v1 (requires --http or --sse)",
)
```

### `create_server()` signature change

Add param: `enable_rest: bool = False`

When `True`, call `_build_rest_router(conn, _lock, tenant_keys)` and attach: `mcp._rest_router = router`. When `False`, `mcp._rest_router = None`.

### REST router factory

Add `_build_rest_router` as an inner function of `create_server()`:

```python
def _build_rest_router(flat_tenant_keys: Optional[dict[str, str]]):
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route, Router

    async def _auth(request: Request) -> Optional[Response]:
        if not flat_tenant_keys:
            return None  # unauthenticated server — allow all
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return Response(
                content=json.dumps({"error": "missing or malformed Authorization header"}),
                status_code=401, media_type="application/json",
            )
        token = auth_header[len("Bearer "):]
        for api_key in flat_tenant_keys.values():
            if hmac.compare_digest(token, api_key):
                return None
        return Response(
            content=json.dumps({"error": "invalid API key"}),
            status_code=401, media_type="application/json",
        )

    async def _list_tasks(request: Request):
        if (err := await _auth(request)):
            return err
        q = request.query_params
        project = q.get("project")
        assignee = q.get("assignee")
        status = q.get("status")
        priority = q.get("priority")
        try:
            limit = max(1, min(int(q.get("limit", 20)), _MAX_LIMIT))
            offset = max(0, int(q.get("offset", 0)))
        except ValueError:
            return JSONResponse({"error": "limit and offset must be integers"}, status_code=400)
        # delegate to existing list_tasks logic (inline — shares conn/params pattern)
        conditions, params = [], []
        if project: conditions.append("project = ?"); params.append(project)
        if assignee: conditions.append("assignee = ?"); params.append(assignee)
        if status: conditions.append("status = ?"); params.append(status)
        if priority: conditions.append("priority = ?"); params.append(priority)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT id, title, priority, status, assignee FROM tasks"
            f" {where} ORDER BY {_PRIORITY_CASE}, created_at LIMIT ? OFFSET ?",
            params + [limit + 1, offset],
        ).fetchall()
        has_more = len(rows) > limit
        return JSONResponse({"tasks": [dict(r) for r in rows[:limit]], "has_more": has_more, "offset": offset})

    async def _create_task(request: Request):
        if (err := await _auth(request)):
            return err
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        # Validate required fields
        task_id = body.get("id")
        title = body.get("title")
        if not task_id or not title:
            return JSONResponse({"error": "id and title are required"}, status_code=400)
        err_str = _validate_create_params(body)
        if err_str:
            return JSONResponse({"error": err_str}, status_code=400)
        now = _now()
        tags_json = json.dumps(body["tags"]) if body.get("tags") else None
        async with _lock:
            try:
                conn.execute(
                    "INSERT INTO tasks (id, title, description, project, priority, status,"
                    " assignee, tags, due_date, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                    (task_id, title, body.get("description"), body.get("project", "default"),
                     body.get("priority", "medium"), body.get("assignee"), tags_json,
                     body.get("due_date"), now, now),
                )
                _log(task_id, "created", actor="rest-api")
                conn.commit()
            except sqlite3.IntegrityError:
                return JSONResponse({"error": f"task '{task_id}' already exists"}, status_code=409)
            except sqlite3.Error:
                return JSONResponse({"error": "database error"}, status_code=500)
        return JSONResponse({"id": task_id, "status": "pending"}, status_code=201)

    async def _get_task(request: Request):
        if (err := await _auth(request)):
            return err
        task_id = request.path_params["id"]
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return JSONResponse({"error": f"task '{task_id}' not found"}, status_code=404)
        task = _row(row)
        task["depends_on"] = [
            r[0] for r in conn.execute(
                "SELECT depends_on FROM task_deps WHERE task_id = ?", (task_id,)
            ).fetchall()
        ]
        return JSONResponse(task)

    async def _update_task(request: Request):
        if (err := await _auth(request)):
            return err
        task_id = request.path_params["id"]
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        err_str = _validate_update_params(body)
        if err_str:
            return JSONResponse({"error": err_str}, status_code=400)
        updates: dict[str, object] = {}
        for field in ("title", "description", "priority", "project", "status", "assignee", "due_date"):
            if field in body:
                updates[field] = body[field]
        if "tags" in body:
            updates["tags"] = json.dumps(body["tags"])
        if not updates:
            return JSONResponse({"error": "no fields to update"}, status_code=400)
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        async with _lock:
            cur = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
            _log(task_id, "updated", actor="rest-api")
            conn.commit()
            if cur.rowcount == 0:
                return JSONResponse({"error": f"task '{task_id}' not found"}, status_code=404)
        return JSONResponse({"id": task_id, "updated": list(updates.keys())})

    async def _delete_task(request: Request):
        if (err := await _auth(request)):
            return err
        task_id = request.path_params["id"]
        confirm = request.query_params.get("confirm", "").lower()
        if confirm != "true":
            return JSONResponse({"error": "add ?confirm=true to confirm deletion"}, status_code=400)
        async with _lock:
            conn.execute("DELETE FROM task_deps WHERE task_id = ? OR depends_on = ?", (task_id, task_id))
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            if cur.rowcount == 0:
                return JSONResponse({"error": f"task '{task_id}' not found"}, status_code=404)
            _log(task_id, "deleted", actor="rest-api")
            conn.commit()
        return JSONResponse({"id": task_id, "deleted": True})

    async def _list_projects(request: Request):
        if (err := await _auth(request)):
            return err
        rows = conn.execute(
            "SELECT project, COUNT(*) as total,"
            " SUM(CASE WHEN status != 'done' THEN 1 ELSE 0 END) as open"
            " FROM tasks GROUP BY project ORDER BY project"
        ).fetchall()
        return JSONResponse({"projects": [{"project": r["project"], "open": r["open"], "total": r["total"]} for r in rows]})

    async def _get_stats(request: Request):
        if (err := await _auth(request)):
            return err
        by_status = {r["status"]: r["cnt"] for r in conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ).fetchall()}
        by_priority = {r["priority"]: r["cnt"] for r in conn.execute(
            "SELECT priority, COUNT(*) as cnt FROM tasks WHERE status != 'done' GROUP BY priority"
        ).fetchall()}
        oldest = conn.execute("SELECT MIN(created_at) as oldest FROM tasks WHERE status != 'done'").fetchone()
        return JSONResponse({
            "by_status": by_status,
            "by_priority": by_priority,
            "oldest_open": oldest["oldest"] if oldest else None,
        })

    return Router(routes=[
        Route("/tasks",        _list_tasks,    methods=["GET"]),
        Route("/tasks",        _create_task,   methods=["POST"]),
        Route("/tasks/{id}",   _get_task,      methods=["GET"]),
        Route("/tasks/{id}",   _update_task,   methods=["PATCH"]),
        Route("/tasks/{id}",   _delete_task,   methods=["DELETE"]),
        Route("/projects",     _list_projects, methods=["GET"]),
        Route("/stats",        _get_stats,     methods=["GET"]),
    ])
```

> **Note on Route deduplication:** Starlette allows multiple `Route` entries for the same path with different methods — that's correct here. If your version of Starlette doesn't merge them correctly, use a single route per path and dispatch on `request.method` inside the handler.

### `__main__.py` wiring

In `create_server(...)` call, pass `enable_rest=args.rest_api`.

In the Starlette app construction (HTTP branch):
```python
from starlette.routing import Mount, Route

routes = []
if args.rest_api and getattr(mcp, "_rest_router", None):
    routes.append(Mount("/api/v1", mcp._rest_router))
routes.append(Mount("/", mcp_asgi))

app = Starlette(routes=routes, lifespan=_make_lifespan(mcp_asgi))
app = _FixArgumentsMiddleware(app)
```

Add startup notice when REST is enabled:
```python
if args.rest_api:
    print(f"  REST API:         http://{host}:{port}/api/v1", file=sys.stderr)
```

---

## 7. webhooks

### New dependency

`pyproject.toml` — add optional extras:
```toml
[project.optional-dependencies]
http = ["uvicorn>=0.20,<1.0", "starlette>=0.27,<1.0"]
webhooks = ["httpx>=0.24,<1.0"]
```
(Combine if you prefer: `all = ["uvicorn>=0.20,<1.0", "starlette>=0.27,<1.0", "httpx>=0.24,<1.0"]`)

### Schema DDL

Add to `_SCHEMA`:
```sql
CREATE TABLE IF NOT EXISTS webhooks (
    id         TEXT    PRIMARY KEY,
    url        TEXT    NOT NULL,
    project    TEXT,
    events     TEXT    NOT NULL,
    secret     TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL
);
```

### Constants

```python
_VALID_WEBHOOK_EVENTS = frozenset({"task.created", "task.updated", "task.completed", "task.deleted"})
```

### SSRF guard

```python
import ipaddress, socket

def _is_safe_webhook_url(url: str) -> tuple[bool, str]:
    """Returns (is_safe, error_message). Blocks non-HTTPS and RFC1918/loopback targets."""
    if not url.startswith("https://"):
        return False, "webhook URL must use https://"
    try:
        from urllib.parse import urlparse
        hostname = urlparse(url).hostname
        if not hostname:
            return False, "could not parse hostname from URL"
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"webhook URL resolves to a private/reserved address ({ip})"
    except socket.gaierror:
        return False, "could not resolve webhook hostname"
    except Exception as e:
        return False, f"URL validation error: {e}"
    return True, ""
```

### Delivery function

```python
async def _fire_webhooks(event: str, task_id: str, payload: dict) -> None:
    try:
        import httpx
    except ImportError:
        return  # httpx not installed — silently skip

    rows = conn.execute(
        "SELECT url, project, secret FROM webhooks WHERE enabled = 1 AND events LIKE ?",
        (f'%"{event}"%',),
    ).fetchall()

    body = json.dumps({"event": event, "task_id": task_id,
                       "timestamp": _now(), "data": payload}).encode()

    async with httpx.AsyncClient(timeout=5.0) as client:
        for row in rows:
            if row["project"] is not None:
                # Check if task belongs to this webhook's project
                task_row = conn.execute("SELECT project FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if not task_row or task_row["project"] != row["project"]:
                    continue
            headers = {"Content-Type": "application/json"}
            if row["secret"]:
                import hmac as _hmac, hashlib
                sig = _hmac.new(row["secret"].encode(), body, hashlib.sha256).hexdigest()
                headers["X-Hub-Signature-256"] = f"sha256={sig}"
            try:
                await client.post(row["url"], content=body, headers=headers)
            except Exception:
                pass  # fire-and-forget — no retry in v0.2.0
```

Call after each write tool completes (outside the `async with _lock` block, after `conn.commit()`):
```python
asyncio.create_task(_fire_webhooks("task.created", id, {"id": id, "title": title, ...}))
```

### New MCP tools

```python
@mcp.tool()
async def register_webhook(
    id: str,
    url: str,
    events: list[str],
    project: Optional[str] = None,
    secret: Optional[str] = None,
) -> str:
    """Register a webhook URL to receive task event notifications."""
    try:
        import httpx  # noqa: F401
    except ImportError:
        return ("Error: webhooks require httpx. "
                "Install with: pip install 'open-project-manager-mcp[webhooks]'")
    if len(id) > _MAX_SHORT_FIELD:
        return f"Error: 'id' exceeds maximum length of {_MAX_SHORT_FIELD}"
    invalid_events = set(events) - _VALID_WEBHOOK_EVENTS
    if invalid_events:
        return f"Error: invalid event(s): {', '.join(sorted(invalid_events))}. Valid: {', '.join(sorted(_VALID_WEBHOOK_EVENTS))}"
    if not events:
        return "Error: events list must not be empty"
    safe, err_msg = _is_safe_webhook_url(url)
    if not safe:
        return f"Error: {err_msg}"
    now = _now()
    async with _lock:
        try:
            conn.execute(
                "INSERT INTO webhooks (id, url, project, events, secret, enabled, created_at)"
                " VALUES (?, ?, ?, ?, ?, 1, ?)",
                (id, url, project, json.dumps(events), secret, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return f"Error: webhook '{id}' already exists"
        except sqlite3.Error:
            return "Error: database error registering webhook"
    return json.dumps({"id": id, "url": url, "events": events, "project": project})


@mcp.tool()
def list_webhooks(project: Optional[str] = None) -> str:
    """List registered webhooks. Does not return secrets."""
    conditions: list[str] = []
    params: list[object] = []
    if project is not None:
        conditions.append("(project = ? OR project IS NULL)")
        params.append(project)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT id, url, project, events, enabled FROM webhooks {where} ORDER BY id",
        params,
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["events"] = json.loads(d["events"])
        result.append(d)
    return json.dumps({"webhooks": result})


@mcp.tool()
async def delete_webhook(id: str, human_approval: bool = False) -> str:
    """Delete a registered webhook. Requires human_approval=True."""
    if not human_approval:
        return "Error: human_approval=True is required to delete a webhook"
    async with _lock:
        cur = conn.execute("DELETE FROM webhooks WHERE id = ?", (id,))
        conn.commit()
        if cur.rowcount == 0:
            return f"Error: webhook '{id}' not found"
    return json.dumps({"id": id, "deleted": True})
```

---

## Testing checklist

After implementing each feature, run `pytest tests/` to confirm no regressions. Add tests for each new tool following the existing pattern in `tests/test_tools.py`:

```python
result = json.loads(_sync_wrap(server._tool_manager._tools["tool_name"].fn)(...))
```

Cover: happy path, invalid params, boundary values (limit=0, limit=501, bulk >50), missing required fields, FTS unavailable fallback, webhook SSRF rejection.

---

## Files to modify

| File | Changes |
|------|---------|
| `server.py` | Schema DDL, migration block, `_VALID_UPDATE_COLUMNS`, new tools, `_log`, `_actor`, `_build_rest_router`, `enable_rest` param |
| `__main__.py` | `--rest-api` CLI flag, REST router mounting, startup print |
| `pyproject.toml` | `[webhooks]` optional extra, `timedelta` import note |

`due_date` requires `from datetime import timedelta` import — add to existing datetime import line.

---

*Brief written by Elliot. Build in order 1→7. No deviations without sign-off.*
