# Darlene Implementation Brief — Proactive Messaging System (Build Orders 8, 9, 10)

**From:** Elliot (Lead & Architect)  
**To:** Darlene (Implementation Lead)  
**Date:** 2026-04-02  
**Branch base:** post-webhooks (after build order 7 is merged)  
**Andrew's sign-off:** Confirmed 2026-04-02 — all 7 open questions answered  
**Security reviewer:** Dom (see §6 — review notes for Dom at end)

---

## Context & Reconciliation

Andrew approved SSE in v0.2.0. The `ConnectionTimeoutMiddleware` from the transport-stability work resolved the transport concerns that caused me to defer SSE to Phase 3 in my original design. Mobley's protocol design and my architecture design are complementary; key reconciliation decisions:

1. **SSE promoted to Build Order 8** — was Phase 3, now Phase 1. Transport is stable.
2. **Internal webhook split REJECTED** — Andrew confirmed HTTPS-only for all webhooks and subscriptions. Mobley's §4 LAN-only proposal is dropped. Reuse `_check_ssrf()` as-is.
3. **Ephemeral notifications confirmed** — no `notifications` table in v0.2.0. `GET /api/v1/notifications` is a stub. Deferred to v0.3.0.
4. **Naming collision resolved** — `GET /api/v1/events` = SSE stream; `POST /api/v1/events` = team event push (same path, different method — valid in Starlette); `GET /api/v1/team-events` = REST list of persisted team events.
5. **team.event vs notification distinction** — `POST /api/v1/events` (team event push) writes to `team_events` table and is persisted; `POST /api/v1/notifications` is ephemeral broadcast only.

**Build order must be sequential: 8 → 9 → 10**

---

## Build Order 8: SSE Infrastructure + State Query Tools

### 8.0 New Imports

Add to top-of-file imports in `server.py`:

```python
import time
from starlette.responses import JSONResponse, Response, StreamingResponse
```

(`StreamingResponse` replaces the existing import — just add it to the same line as `JSONResponse, Response`.)

---

### 8.1 New Closure Variables

Add immediately after `_lock = asyncio.Lock()` inside `create_server()`:

```python
_start_time: float = time.time()
_event_bus_clients: list[asyncio.Queue] = []
_bg_health_task: Optional[asyncio.Task] = None
_bg_sub_task: Optional[asyncio.Task] = None  # used in build order 10
```

---

### 8.2 `_publish_event()` Helper

Add alongside `_now()`, `_log()`, `_get_actor()` as a top-level inner helper in `create_server()`:

```python
def _publish_event(event_type: str, data: dict) -> None:
    """Fanout an event to all connected SSE clients. Silently drops if a client queue is full."""
    payload = {"event": event_type, "data": data, "timestamp": _now()}
    for q in list(_event_bus_clients):  # list() guards against mutation during iteration
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # Slow client — drop; client reconnects and catches up
```

---

### 8.3 `_publish_queue_stats()` Helper

Add immediately after `_publish_event()`:

```python
def _publish_queue_stats() -> None:
    """Publish a queue.stats event. No-op if no clients are connected."""
    if not _event_bus_clients:
        return
    try:
        by_status = {
            r["status"]: r["cnt"]
            for r in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
            ).fetchall()
        }
    except Exception:
        return
    _publish_event("queue.stats", {
        "pending_count": by_status.get("pending", 0),
        "in_progress_count": by_status.get("in_progress", 0),
        "blocked_count": by_status.get("blocked", 0),
        "completed_count": by_status.get("done", 0),
    })
```

---

### 8.4 `_publish_health_event()` Helper

```python
def _publish_health_event(status: str, message: Optional[str] = None) -> None:
    """Publish a server.health event to all connected SSE clients."""
    data: dict = {
        "status": status,
        "uptime_seconds": int(time.time() - _start_time),
        "active_connections": len(_event_bus_clients),
    }
    if message:
        data["message"] = message
    _publish_event("server.health", data)
```

---

### 8.5 Background Health Task

Add as inner async functions in `create_server()`:

```python
async def _health_loop() -> None:
    """Emit server.health every 30 seconds while clients are connected."""
    while True:
        await asyncio.sleep(30)
        if _event_bus_clients:
            _publish_health_event("healthy")

def _ensure_bg_health_task() -> None:
    """Start the health background task if not already running. Idempotent."""
    nonlocal _bg_health_task
    if _bg_health_task is not None and not _bg_health_task.done():
        return
    _bg_health_task = asyncio.create_task(_health_loop())
```

---

### 8.6 Hook Task CRUD Into Event Bus

For each of the four task-mutating MCP tools, add `_publish_event()` and `_publish_queue_stats()` calls **after** the existing `asyncio.create_task(_fire_webhooks(...))` call. The event bus and webhooks are independent — both fire.

**`create_task()`** — after `asyncio.create_task(_fire_webhooks(...))`:
```python
_publish_event("task.created", {
    "id": id, "title": title, "priority": priority,
    "status": "pending", "project": project,
})
_publish_queue_stats()
```

**`update_task()`** — after `asyncio.create_task(_fire_webhooks(...))`:
```python
_publish_event("task.updated", {
    "id": task_id, "updated": list(updates.keys()), "project": task_project,
})
_publish_queue_stats()
```

**`complete_task()`** — after `asyncio.create_task(_fire_webhooks(...))`:
```python
_publish_event("task.completed", {"id": task_id, "status": "done", "project": task_project})
_publish_queue_stats()
```

**`delete_task()`** — after `asyncio.create_task(_fire_webhooks(...))`:
```python
_publish_event("task.deleted", {"id": task_id, "project": task_project})
_publish_queue_stats()
```

**Also hook REST task endpoints** — The REST API task handlers in `_build_rest_router()` duplicate DB logic directly and do NOT call the MCP tool functions (pre-existing gap — they also skip `_fire_webhooks`). Add the same `_publish_event()` + `_publish_queue_stats()` calls to `tasks_endpoint` (POST) and `task_endpoint` (PATCH and DELETE) after their respective `conn.commit()` calls. The helpers are accessible from `_build_rest_router()` as they close over `create_server()` scope.

---

### 8.7 `get_server_stats()` MCP Tool

Add in the "Queries" section alongside existing query tools:

```python
@mcp.tool()
def get_server_stats() -> str:
    """Get server statistics: task counts, uptime, and active SSE connections."""
    try:
        by_status = {
            r["status"]: r["cnt"]
            for r in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
            ).fetchall()
        }
        by_project: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT project, status, COUNT(*) as cnt FROM tasks GROUP BY project, status"
        ).fetchall():
            by_project.setdefault(r["project"], {})[r["status"]] = r["cnt"]
    except sqlite3.Error:
        return "Error: database error reading server stats"
    queue_depth = sum(v for k, v in by_status.items() if k != "done")
    return json.dumps({
        "queue_depth": queue_depth,
        "by_status": by_status,
        "by_project": by_project,
        "uptime_sec": int(time.time() - _start_time),
        "active_sse_clients": len(_event_bus_clients),
    })
```

---

### 8.8 `get_project_summary()` MCP Tool

```python
@mcp.tool()
def get_project_summary(project: str) -> str:
    """Get a task summary for a specific project, including overdue count."""
    if not project or len(project) > _MAX_SHORT_FIELD:
        return f"Error: 'project' is required and must be under {_MAX_SHORT_FIELD} characters"
    try:
        by_status = {
            r["status"]: r["cnt"]
            for r in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks WHERE project = ? GROUP BY status",
                (project,),
            ).fetchall()
        }
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        overdue_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks"
            " WHERE project = ? AND due_date IS NOT NULL AND due_date < ? AND status != 'done'",
            (project, today),
        ).fetchone()
    except sqlite3.Error:
        return "Error: database error reading project summary"
    total = sum(by_status.values())
    return json.dumps({
        "project": project,
        "total": total,
        "pending": by_status.get("pending", 0),
        "in_progress": by_status.get("in_progress", 0),
        "done": by_status.get("done", 0),
        "blocked": by_status.get("blocked", 0),
        "overdue": overdue_row["cnt"] if overdue_row else 0,
    })
```

---

### 8.9 SSE Event Stream Endpoint (`GET /api/v1/events` + `POST /api/v1/events`)

This goes inside `_build_rest_router()`. A single Starlette route handles both methods on the same path.

```python
async def events_endpoint(request: Request) -> Response:
    actor, err = await _check_auth(request)
    if err:
        return err

    if request.method == "GET":
        # SSE stream — client holds this connection open
        client_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        _event_bus_clients.append(client_queue)
        _ensure_bg_health_task()

        async def event_generator():
            # Send a targeted welcome health event to just this new client
            welcome_data = {
                "status": "healthy",
                "uptime_seconds": int(time.time() - _start_time),
                "active_connections": len(_event_bus_clients),
                "message": "Connected",
            }
            yield f"event: server.health\ndata: {json.dumps(welcome_data)}\n\n"
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(client_queue.get(), timeout=15.0)
                        yield (
                            f"event: {event['event']}\n"
                            f"data: {json.dumps(event['data'])}\n\n"
                        )
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                try:
                    _event_bus_clients.remove(client_queue)
                except ValueError:
                    pass

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    elif request.method == "POST":
        # Team event push — handled in Build Order 9
        # Placeholder: returns 501 until Build Order 9 is implemented
        return JSONResponse({"error": "Not implemented yet"}, status_code=501)

    return JSONResponse({"error": "Method not allowed"}, status_code=405)
```

**Important:** When Build Order 9 is implemented, replace the `POST` 501 stub with the full team event push logic described in §9.6.

---

### 8.10 `GET /api/v1/projects/{project}/summary` REST Endpoint

```python
async def project_summary_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    project = request.path_params["project"]
    result = get_project_summary.__wrapped__(project)  # call inner function directly
    if result.startswith("Error:"):
        return JSONResponse({"error": result}, status_code=_error_status(result))
    return JSONResponse(json.loads(result))
```

> **Note on `__wrapped__`:** The MCP tool decorator wraps the function. Since the logic is the same, the simplest approach is to extract the project summary logic into a shared inner helper `_project_summary(project)` that both the MCP tool and REST endpoint call. Do that refactor when implementing — don't use `__wrapped__`.

---

### 8.11 `GET /api/v1/stats?detailed=true` Extension

Extend the existing `stats_endpoint` function inside `_build_rest_router()`:

```python
async def stats_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    detailed = request.query_params.get("detailed", "false").lower() == "true"
    try:
        by_status = {
            r["status"]: r["cnt"]
            for r in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
            ).fetchall()
        }
        by_priority = {
            r["priority"]: r["cnt"]
            for r in conn.execute(
                "SELECT priority, COUNT(*) as cnt FROM tasks WHERE status != 'done' GROUP BY priority"
            ).fetchall()
        }
        oldest = conn.execute(
            "SELECT MIN(created_at) as oldest FROM tasks WHERE status != 'done'"
        ).fetchone()
    except sqlite3.Error:
        return JSONResponse({"error": "Error: database error"}, status_code=500)

    result: dict = {
        "by_status": by_status,
        "by_priority": by_priority,
        "oldest_open": oldest["oldest"] if oldest else None,
    }

    if detailed:
        try:
            webhook_counts = conn.execute(
                "SELECT COUNT(*) as total, SUM(enabled) as enabled_count FROM webhooks"
            ).fetchone()
            activity = conn.execute(
                "SELECT"
                " MAX(CASE WHEN action='created' THEN created_at END) as last_created,"
                " MAX(CASE WHEN action='updated' THEN created_at END) as last_updated,"
                " MAX(CASE WHEN action='completed' THEN created_at END) as last_completed"
                " FROM activity_log"
            ).fetchone()
            by_project: dict[str, dict] = {}
            for r in conn.execute(
                "SELECT project, status, COUNT(*) as cnt FROM tasks GROUP BY project, status"
            ).fetchall():
                by_project.setdefault(r["project"], {})[r["status"]] = r["cnt"]
        except sqlite3.Error:
            return JSONResponse({"error": "Error: database error"}, status_code=500)

        try:
            from importlib.metadata import version as _pkg_version
            _version = _pkg_version("open-project-manager-mcp")
        except Exception:
            _version = "unknown"

        started_at = datetime.fromtimestamp(_start_time, tz=timezone.utc).isoformat()
        result["server"] = {
            "version": _version,
            "uptime_seconds": int(time.time() - _start_time),
            "started_at": started_at,
            "rest_api_enabled": True,
        }
        result["webhooks"] = {
            "total_count": webhook_counts["total"] if webhook_counts else 0,
            "enabled_count": webhook_counts["enabled_count"] if webhook_counts else 0,
        }
        result["activity"] = {
            "last_task_created": activity["last_created"] if activity else None,
            "last_task_updated": activity["last_updated"] if activity else None,
            "last_task_completed": activity["last_completed"] if activity else None,
        }
        result["connections"] = {
            "active_sse_clients": len(_event_bus_clients),
        }
        result["tasks_by_project"] = by_project

    return JSONResponse(result)
```

---

### 8.12 Router Updates (Build Order 8)

Add new routes to the `Router(routes=[...])` call at the end of `_build_rest_router()`:

```python
Route("/events", endpoint=events_endpoint, methods=["GET", "POST"]),
Route("/projects/{project:str}/summary", endpoint=project_summary_endpoint, methods=["GET"]),
```

The `/stats` route already exists — it is updated in-place (§8.11 above).

---

### 8.13 Event Type Reference (Build Order 8)

| Event Type | Trigger | Payload Keys |
|---|---|---|
| `task.created` | Task created (MCP or REST) | `id, title, priority, status, project` |
| `task.updated` | Task updated | `id, updated (list), project` |
| `task.completed` | Task marked done | `id, status` |
| `task.deleted` | Task deleted | `id, project` |
| `queue.stats` | After any task state change | `pending_count, in_progress_count, blocked_count, completed_count` |
| `server.health` | Every 30s + on new SSE connection | `status, uptime_seconds, active_connections, message?` |

---

## Build Order 9: Team Inbound + Notifications

### 9.1 Schema

Append to `_SCHEMA` in `server.py`:

```sql
CREATE TABLE IF NOT EXISTS team_status (
    squad      TEXT    PRIMARY KEY,
    status     TEXT    NOT NULL,      -- 'online' | 'offline' | 'busy' | 'degraded'
    message    TEXT,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS team_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    squad      TEXT    NOT NULL,
    event_type TEXT    NOT NULL,      -- e.g. 'milestone.completed' | 'error' | 'status_change'
    data       TEXT,                  -- JSON payload (nullable)
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS team_events_squad_idx   ON team_events(squad);
CREATE INDEX IF NOT EXISTS team_events_created_idx ON team_events(created_at DESC);
```

> **Retention note:** `created_at` index is pre-positioned for 30-day pruning in v0.3.0. No pruning job in v0.2.0.

---

### 9.2 New Constant

Add at module level alongside `VALID_WEBHOOK_EVENTS`:

```python
VALID_TEAM_STATUSES = {"online", "offline", "busy", "degraded"}
VALID_NOTIFICATION_TYPES = {"squad.status", "squad.alert", "squad.heartbeat"}
```

---

### 9.3 `set_team_status()` MCP Tool

```python
@mcp.tool()
async def set_team_status(status: str, message: Optional[str] = None) -> str:
    """Set your team's operational status. Actor is derived from your auth token.
    status: online | offline | busy | degraded"""
    if status not in VALID_TEAM_STATUSES:
        return f"Error: invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_TEAM_STATUSES))}"
    squad = _get_actor()
    if squad == "system":
        return "Error: cannot set team status without an authenticated actor"
    if message and len(message) > _MAX_SHORT_FIELD:
        return f"Error: 'message' exceeds maximum length of {_MAX_SHORT_FIELD} characters"
    now = _now()
    async with _lock:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO team_status (squad, status, message, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (squad, status, message, now),
            )
            conn.commit()
        except sqlite3.Error:
            return "Error: database error setting team status"
    _publish_event("team.status_changed", {
        "squad": squad, "status": status, "message": message, "updated_at": now,
    })
    return json.dumps({"squad": squad, "status": status, "updated_at": now})
```

---

### 9.4 `get_team_status()` MCP Tool

```python
@mcp.tool()
def get_team_status(squad: Optional[str] = None) -> str:
    """Get team status. If squad is omitted, returns all teams' statuses.
    Any authenticated team can see all teams (cross-team visibility)."""
    try:
        if squad:
            row = conn.execute(
                "SELECT squad, status, message, updated_at FROM team_status WHERE squad = ?",
                (squad,),
            ).fetchone()
            if row is None:
                return json.dumps({"squad": squad, "status": None, "message": None})
            return json.dumps(dict(row))
        else:
            rows = conn.execute(
                "SELECT squad, status, message, updated_at FROM team_status ORDER BY squad"
            ).fetchall()
            return json.dumps({"teams": [dict(r) for r in rows]})
    except sqlite3.Error:
        return "Error: database error reading team status"
```

---

### 9.5 `post_team_event()` MCP Tool

```python
@mcp.tool()
async def post_team_event(event_type: str, data: Optional[dict] = None) -> str:
    """Post a team event to OPM. Actor from auth token = squad.
    event_type: any string, e.g. milestone.completed | error | custom"""
    squad = _get_actor()
    if squad == "system":
        return "Error: cannot post team event without an authenticated actor"
    if not event_type or len(event_type) > _MAX_SHORT_FIELD:
        return f"Error: 'event_type' is required and must be under {_MAX_SHORT_FIELD} characters"
    data_json = json.dumps(data) if data is not None else None
    now = _now()
    async with _lock:
        try:
            cur = conn.execute(
                "INSERT INTO team_events (squad, event_type, data, created_at)"
                " VALUES (?, ?, ?, ?)",
                (squad, event_type, data_json, now),
            )
            conn.commit()
            event_id = cur.lastrowid
        except sqlite3.Error:
            return "Error: database error posting team event"
    _publish_event("team.event", {
        "id": event_id, "squad": squad, "event_type": event_type,
        "data": data, "created_at": now,
    })
    return json.dumps({"id": event_id, "squad": squad, "event_type": event_type, "created_at": now})
```

---

### 9.6 `get_team_events()` MCP Tool

```python
@mcp.tool()
def get_team_events(
    squad: Optional[str] = None,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List team events, newest first. Any authenticated team can see all teams' events."""
    limit = max(1, min(limit, _MAX_LIMIT))
    conditions: list[str] = []
    params: list = []
    if squad:
        conditions.append("squad = ?")
        params.append(squad)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)
    if since:
        conditions.append("created_at > ?")
        params.append(since)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    try:
        rows = conn.execute(
            f"SELECT id, squad, event_type, data, created_at"
            f" FROM team_events {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    except sqlite3.Error:
        return "Error: database error reading team events"
    events = []
    for r in rows:
        e = dict(r)
        if e.get("data"):
            try:
                e["data"] = json.loads(e["data"])
            except Exception:
                pass
        events.append(e)
    return json.dumps({"events": events, "count": len(events)})
```

---

### 9.7 REST Endpoints for Build Order 9

All of these go inside `_build_rest_router()`.

#### `POST /api/v1/notifications` and `GET /api/v1/notifications`

```python
async def notifications_endpoint(request: Request) -> Response:
    actor, err = await _check_auth(request)
    if err:
        return err
    if request.method == "POST":
        body, body_err = await _read_json_body(request)
        if body_err:
            return body_err
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        message_type = body.get("message_type")
        squad = body.get("squad")
        data = body.get("data")
        if message_type not in VALID_NOTIFICATION_TYPES:
            return JSONResponse(
                {"error": f"Invalid message_type. Must be one of: {', '.join(sorted(VALID_NOTIFICATION_TYPES))}"},
                status_code=400,
            )
        if not isinstance(squad, str) or not squad:
            return JSONResponse({"error": "'squad' is required and must be a non-empty string"}, status_code=400)
        if len(squad) > _MAX_SHORT_FIELD:
            return JSONResponse({"error": f"'squad' exceeds maximum length of {_MAX_SHORT_FIELD} characters"}, status_code=400)
        notification_id = secrets.token_urlsafe(16)
        received_at = _now()
        _publish_event("notification.received", {
            "notification_id": notification_id,
            "squad": squad,
            "message_type": message_type,
            "data": data,
            "received_at": received_at,
        })
        return JSONResponse({"notification_id": notification_id, "received_at": received_at}, status_code=201)
    elif request.method == "GET":
        # Stub — notifications table deferred to v0.3.0
        return JSONResponse({"notifications": [], "count": 0})
    return JSONResponse({"error": "Method not allowed"}, status_code=405)
```

#### `PUT /api/v1/status` and `GET /api/v1/status`

```python
async def status_endpoint(request: Request) -> Response:
    actor, err = await _check_auth(request)
    if err:
        return err
    if request.method == "PUT":
        body, body_err = await _read_json_body(request)
        if body_err:
            return body_err
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        status = body.get("status")
        message = body.get("message")
        if status not in VALID_TEAM_STATUSES:
            return JSONResponse(
                {"error": f"Invalid status. Must be one of: {', '.join(sorted(VALID_TEAM_STATUSES))}"},
                status_code=400,
            )
        squad = actor  # squad identity comes from the auth token
        now = _now()
        async with _lock:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO team_status (squad, status, message, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (squad, status, message, now),
                )
                conn.commit()
            except sqlite3.Error:
                return JSONResponse({"error": "Error: database error"}, status_code=500)
        _publish_event("team.status_changed", {
            "squad": squad, "status": status, "message": message, "updated_at": now,
        })
        return JSONResponse({"squad": squad, "status": status, "updated_at": now})
    elif request.method == "GET":
        try:
            rows = conn.execute(
                "SELECT squad, status, message, updated_at FROM team_status ORDER BY squad"
            ).fetchall()
        except sqlite3.Error:
            return JSONResponse({"error": "Error: database error"}, status_code=500)
        return JSONResponse({"teams": [dict(r) for r in rows]})
    return JSONResponse({"error": "Method not allowed"}, status_code=405)
```

#### `GET /api/v1/status/{squad}`

```python
async def status_squad_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    squad = request.path_params["squad"]
    try:
        row = conn.execute(
            "SELECT squad, status, message, updated_at FROM team_status WHERE squad = ?",
            (squad,),
        ).fetchone()
    except sqlite3.Error:
        return JSONResponse({"error": "Error: database error"}, status_code=500)
    if row is None:
        return JSONResponse({"error": f"Squad '{squad}' not found"}, status_code=404)
    return JSONResponse(dict(row))
```

#### `POST /api/v1/events` (team event push — fills in the §8.9 stub)

Replace the 501 stub in `events_endpoint` with:

```python
elif request.method == "POST":
    body, body_err = await _read_json_body(request)
    if body_err:
        return body_err
    if not isinstance(body, dict):
        return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
    event_type = body.get("event_type")
    data = body.get("data")
    if not isinstance(event_type, str) or not event_type:
        return JSONResponse({"error": "'event_type' is required and must be a non-empty string"}, status_code=400)
    if len(event_type) > _MAX_SHORT_FIELD:
        return JSONResponse({"error": f"'event_type' exceeds maximum length of {_MAX_SHORT_FIELD} characters"}, status_code=400)
    squad = actor
    now = _now()
    async with _lock:
        try:
            cur = conn.execute(
                "INSERT INTO team_events (squad, event_type, data, created_at) VALUES (?, ?, ?, ?)",
                (squad, event_type, json.dumps(data) if data is not None else None, now),
            )
            conn.commit()
            event_id = cur.lastrowid
        except sqlite3.Error:
            return JSONResponse({"error": "Error: database error"}, status_code=500)
    _publish_event("team.event", {
        "id": event_id, "squad": squad, "event_type": event_type,
        "data": data, "created_at": now,
    })
    return JSONResponse(
        {"id": event_id, "squad": squad, "event_type": event_type, "created_at": now},
        status_code=201,
    )
```

#### `GET /api/v1/team-events` (REST list of persisted team events)

```python
async def team_events_endpoint(request: Request) -> JSONResponse:
    actor, err = await _check_auth(request)
    if err:
        return err
    p = request.query_params
    squad_filter = p.get("squad")
    event_type_filter = p.get("event_type")
    since_filter = p.get("since")
    try:
        limit = int(p.get("limit", 50))
    except ValueError:
        return JSONResponse({"error": "Error: invalid limit"}, status_code=400)
    limit = max(1, min(limit, _MAX_LIMIT))
    conditions: list[str] = []
    params: list = []
    if squad_filter:
        conditions.append("squad = ?")
        params.append(squad_filter)
    if event_type_filter:
        conditions.append("event_type = ?")
        params.append(event_type_filter)
    if since_filter:
        conditions.append("created_at > ?")
        params.append(since_filter)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    try:
        rows = conn.execute(
            f"SELECT id, squad, event_type, data, created_at"
            f" FROM team_events {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    except sqlite3.Error:
        return JSONResponse({"error": "Error: database error"}, status_code=500)
    events = []
    for r in rows:
        e = dict(r)
        if e.get("data"):
            try:
                e["data"] = json.loads(e["data"])
            except Exception:
                pass
        events.append(e)
    return JSONResponse({"events": events, "count": len(events)})
```

---

### 9.8 Router Updates (Build Order 9)

Add to the `Router(routes=[...])`:

```python
Route("/notifications", endpoint=notifications_endpoint, methods=["GET", "POST"]),
Route("/status", endpoint=status_endpoint, methods=["GET", "PUT"]),
Route("/status/{squad:str}", endpoint=status_squad_endpoint, methods=["GET"]),
Route("/team-events", endpoint=team_events_endpoint, methods=["GET"]),
```

And update the `/events` route to handle both GET (SSE) and POST (team push) — if it wasn't already declared with both methods in Build Order 8:

```python
Route("/events", endpoint=events_endpoint, methods=["GET", "POST"]),
```

---

### 9.9 Event Type Reference (Build Order 9 additions)

| Event Type | Trigger | Payload Keys |
|---|---|---|
| `notification.received` | `POST /api/v1/notifications` | `notification_id, squad, message_type, data, received_at` |
| `team.status_changed` | `PUT /api/v1/status` or `set_team_status` MCP tool | `squad, status, message, updated_at` |
| `team.event` | `POST /api/v1/events` or `post_team_event` MCP tool | `id, squad, event_type, data, created_at` |

---

## Build Order 10: Outbound Event Subscriptions

### 10.1 Schema

Append to `_SCHEMA` (use Elliot's DDL from the original design):

```sql
CREATE TABLE IF NOT EXISTS event_subscriptions (
    id             TEXT    PRIMARY KEY,
    subscriber     TEXT    NOT NULL,       -- squad/team identifier
    url            TEXT    NOT NULL,       -- HTTPS endpoint (SSRF rules enforced at registration)
    event_type     TEXT    NOT NULL,       -- 'server.stats' | 'server.health' | 'project.summary'
    project        TEXT,                   -- NULL = N/A; used for project.summary scoping
    interval_sec   INTEGER,               -- seconds between fires; NULL = on-change (not used in v0.2.0)
    enabled        INTEGER NOT NULL DEFAULT 1,
    last_fired_at  TEXT,                   -- ISO timestamp of last successful delivery
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS event_sub_type_idx ON event_subscriptions(event_type);
```

---

### 10.2 New Constants

```python
VALID_SUBSCRIPTION_EVENTS = {"server.stats", "server.health", "project.summary"}
_SUB_MIN_INTERVAL = 60       # seconds
_SUB_MAX_INTERVAL = 86400    # seconds
```

---

### 10.3 `subscribe_events()` MCP Tool

```python
@mcp.tool()
async def subscribe_events(
    id: str,
    subscriber: str,
    url: str,
    event_type: str,
    project: Optional[str] = None,
    interval_sec: Optional[int] = None,
) -> str:
    """Subscribe to periodic server state events delivered to an HTTPS endpoint.
    event_type: server.stats | server.health | project.summary
    interval_sec: 60–86400; required for server.stats and project.summary"""
    try:
        import httpx  # noqa: F401
    except ImportError:
        return (
            "Error: httpx is required for event subscriptions. "
            "Install with: pip install 'open-project-manager-mcp[webhooks]'"
        )
    if len(id) > _MAX_SHORT_FIELD:
        return f"Error: 'id' exceeds maximum length of {_MAX_SHORT_FIELD} characters"
    if len(subscriber) > _MAX_SHORT_FIELD:
        return f"Error: 'subscriber' exceeds maximum length of {_MAX_SHORT_FIELD} characters"
    if event_type not in VALID_SUBSCRIPTION_EVENTS:
        return f"Error: invalid event_type '{event_type}'. Must be one of: {', '.join(sorted(VALID_SUBSCRIPTION_EVENTS))}"
    if event_type == "project.summary" and not project:
        return "Error: 'project' is required for event_type 'project.summary'"
    if interval_sec is not None:
        if interval_sec < _SUB_MIN_INTERVAL:
            return f"Error: interval_sec must be at least {_SUB_MIN_INTERVAL} seconds"
        if interval_sec > _SUB_MAX_INTERVAL:
            return f"Error: interval_sec must be at most {_SUB_MAX_INTERVAL} seconds"
    ssrf_err = await _check_ssrf(url)
    if ssrf_err:
        return ssrf_err
    async with _lock:
        try:
            conn.execute(
                "INSERT INTO event_subscriptions"
                " (id, subscriber, url, event_type, project, interval_sec, enabled, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (id, subscriber, url, event_type, project, interval_sec, _now()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return f"Error: subscription '{id}' already exists"
        except sqlite3.Error:
            return "Error: database error registering subscription"
    _ensure_bg_sub_task()
    return json.dumps({
        "id": id, "subscriber": subscriber, "event_type": event_type,
        "project": project, "interval_sec": interval_sec,
    })
```

---

### 10.4 `list_subscriptions()` MCP Tool

```python
@mcp.tool()
def list_subscriptions(subscriber: Optional[str] = None) -> str:
    """List event subscriptions. Filter by subscriber name if provided."""
    try:
        if subscriber:
            rows = conn.execute(
                "SELECT id, subscriber, url, event_type, project, interval_sec, enabled, last_fired_at, created_at"
                " FROM event_subscriptions WHERE subscriber = ? ORDER BY created_at",
                (subscriber,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, subscriber, url, event_type, project, interval_sec, enabled, last_fired_at, created_at"
                " FROM event_subscriptions ORDER BY created_at"
            ).fetchall()
    except sqlite3.Error:
        return "Error: database error listing subscriptions"
    return json.dumps({"subscriptions": [dict(r) for r in rows]})
```

---

### 10.5 `unsubscribe_events()` MCP Tool

```python
@mcp.tool()
async def unsubscribe_events(id: str, human_approval: bool = False) -> str:
    """Delete an event subscription. Requires human_approval=True."""
    if not human_approval:
        return "Error: human_approval=True is required to delete a subscription"
    async with _lock:
        cur = conn.execute("DELETE FROM event_subscriptions WHERE id = ?", (id,))
        conn.commit()
        if cur.rowcount == 0:
            return f"Error: subscription '{id}' not found"
    return json.dumps({"id": id, "deleted": True})
```

---

### 10.6 `_fire_event_subscriptions()` Helper

Model after `_fire_webhooks()`. Put it immediately after the `_fire_webhooks` function:

```python
async def _fire_event_subscriptions(event_type: str, payload: dict) -> None:
    """Deliver an event to all enabled subscriptions of the given type. Fire-and-forget."""
    try:
        import httpx
    except ImportError:
        return
    try:
        rows = conn.execute(
            "SELECT id, url FROM event_subscriptions WHERE enabled = 1 AND event_type = ?",
            (event_type,),
        ).fetchall()
    except Exception:
        return
    if not rows:
        return
    envelope = {
        "event": event_type,
        "timestamp": _now(),
        "data": payload,
    }
    payload_bytes = json.dumps(envelope).encode()
    headers = {"Content-Type": "application/json"}
    fired_ids: list[str] = []
    for row in rows:
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=True) as client:
                await client.post(row["url"], content=payload_bytes, headers=headers)
            fired_ids.append(row["id"])
        except Exception:
            pass  # fire-and-forget; no retries in v0.2.0
    if fired_ids:
        now = _now()
        try:
            for sub_id in fired_ids:
                conn.execute(
                    "UPDATE event_subscriptions SET last_fired_at = ? WHERE id = ?",
                    (now, sub_id),
                )
            conn.commit()
        except Exception:
            pass
```

---

### 10.7 Background Subscription Loop

The periodic firing loop. Add as inner async functions alongside `_health_loop`:

```python
async def _subscriptions_loop() -> None:
    """Check every 30s for due interval subscriptions and fire them."""
    while True:
        await asyncio.sleep(30)
        try:
            # Find subscriptions where interval is set AND they're overdue
            rows = conn.execute(
                "SELECT id, subscriber, url, event_type, project, interval_sec"
                " FROM event_subscriptions"
                " WHERE enabled = 1 AND interval_sec IS NOT NULL"
                " AND (last_fired_at IS NULL"
                "      OR datetime(last_fired_at, '+' || interval_sec || ' seconds')"
                "         <= datetime('now'))"
            ).fetchall()
        except Exception:
            continue
        for row in rows:
            event_type = row["event_type"]
            try:
                if event_type == "server.stats":
                    by_status = {
                        r["status"]: r["cnt"]
                        for r in conn.execute(
                            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
                        ).fetchall()
                    }
                    payload = {
                        "queue_depth": sum(v for k, v in by_status.items() if k != "done"),
                        "by_status": by_status,
                        "uptime_sec": int(time.time() - _start_time),
                    }
                elif event_type == "project.summary":
                    project = row["project"] or ""
                    by_status = {
                        r["status"]: r["cnt"]
                        for r in conn.execute(
                            "SELECT status, COUNT(*) as cnt FROM tasks WHERE project = ? GROUP BY status",
                            (project,),
                        ).fetchall()
                    }
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    overdue_row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM tasks"
                        " WHERE project = ? AND due_date IS NOT NULL AND due_date < ? AND status != 'done'",
                        (project, today),
                    ).fetchone()
                    payload = {
                        "project": project,
                        "total": sum(by_status.values()),
                        "pending": by_status.get("pending", 0),
                        "in_progress": by_status.get("in_progress", 0),
                        "done": by_status.get("done", 0),
                        "blocked": by_status.get("blocked", 0),
                        "overdue": overdue_row["cnt"] if overdue_row else 0,
                    }
                elif event_type == "server.health":
                    payload = {
                        "status": "healthy",
                        "uptime_seconds": int(time.time() - _start_time),
                        "active_connections": len(_event_bus_clients),
                    }
                else:
                    continue
                asyncio.create_task(_fire_event_subscriptions(event_type, payload))
            except Exception:
                pass

def _ensure_bg_sub_task() -> None:
    """Start the subscription background task if not already running. Idempotent."""
    nonlocal _bg_sub_task
    if _bg_sub_task is not None and not _bg_sub_task.done():
        return
    _bg_sub_task = asyncio.create_task(_subscriptions_loop())
```

Also start `_ensure_bg_sub_task()` on server startup — call it lazily from `subscribe_events()` and from the SSE endpoint on first connection (already done in §8.9, add `_ensure_bg_sub_task()` there too).

---

### 10.8 REST Endpoints for Build Order 10

#### `POST /api/v1/subscriptions`

```python
async def subscriptions_endpoint(request: Request) -> Response:
    actor, err = await _check_auth(request)
    if err:
        return err
    if request.method == "POST":
        body, body_err = await _read_json_body(request)
        if body_err:
            return body_err
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        # Delegate to the MCP tool logic — extract fields and validate
        sub_id = body.get("id", "")
        subscriber = body.get("subscriber", "")
        url = body.get("url", "")
        event_type = body.get("event_type", "")
        project = body.get("project")
        interval_sec = body.get("interval_sec")
        if not sub_id:
            return JSONResponse({"error": "'id' is required"}, status_code=400)
        # Re-use validation from subscribe_events — call the inner helper directly
        # (refactor: extract shared validation into _validate_subscription() helper)
        result = await subscribe_events.__wrapped__(sub_id, subscriber, url, event_type, project, interval_sec)
        if result.startswith("Error:"):
            return JSONResponse({"error": result}, status_code=_error_status(result))
        return JSONResponse(json.loads(result), status_code=201)
    elif request.method == "GET":
        subscriber_filter = request.query_params.get("subscriber")
        result = list_subscriptions.__wrapped__(subscriber_filter)
        if result.startswith("Error:"):
            return JSONResponse({"error": result}, status_code=500)
        return JSONResponse(json.loads(result))
    return JSONResponse({"error": "Method not allowed"}, status_code=405)
```

> **Refactor note:** The `__wrapped__` approach is fragile. Extract subscription validation and persistence into a private `_create_subscription()` helper function that both the MCP tool and REST endpoint delegate to. Same pattern as `_project_summary()` mentioned in §8.10.

#### `DELETE /api/v1/subscriptions/{id}`

```python
async def subscription_endpoint(request: Request) -> Response:
    actor, err = await _check_auth(request)
    if err:
        return err
    sub_id = request.path_params["id"]
    if request.method == "DELETE":
        async with _lock:
            cur = conn.execute(
                "DELETE FROM event_subscriptions WHERE id = ?", (sub_id,)
            )
            conn.commit()
            if cur.rowcount == 0:
                return JSONResponse({"error": f"Subscription '{sub_id}' not found"}, status_code=404)
        return Response(status_code=204)
    return JSONResponse({"error": "Method not allowed"}, status_code=405)
```

> **Note:** The REST DELETE for subscriptions does NOT require `human_approval` (that's MCP-only UX). REST API callers are responsible for their own confirmation flow.

---

### 10.9 Router Updates (Build Order 10)

```python
Route("/subscriptions", endpoint=subscriptions_endpoint, methods=["GET", "POST"]),
Route("/subscriptions/{id:str}", endpoint=subscription_endpoint, methods=["DELETE"]),
```

---

## Schema DDL — All New Tables

Append all of this to `_SCHEMA` (in the order implemented):

```sql
-- Build Order 9: Team coordination tables
CREATE TABLE IF NOT EXISTS team_status (
    squad      TEXT    PRIMARY KEY,
    status     TEXT    NOT NULL,
    message    TEXT,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS team_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    squad      TEXT    NOT NULL,
    event_type TEXT    NOT NULL,
    data       TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS team_events_squad_idx   ON team_events(squad);
CREATE INDEX IF NOT EXISTS team_events_created_idx ON team_events(created_at DESC);

-- Build Order 10: Outbound event subscriptions
CREATE TABLE IF NOT EXISTS event_subscriptions (
    id             TEXT    PRIMARY KEY,
    subscriber     TEXT    NOT NULL,
    url            TEXT    NOT NULL,
    event_type     TEXT    NOT NULL,
    project        TEXT,
    interval_sec   INTEGER,
    enabled        INTEGER NOT NULL DEFAULT 1,
    last_fired_at  TEXT,
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS event_sub_type_idx ON event_subscriptions(event_type);
```

---

## Complete Endpoint Summary

| Method | Path | Auth | Description | Build Order |
|--------|------|------|-------------|-------------|
| GET | `/api/v1/events` | Bearer | SSE stream (all event types) | 8 |
| POST | `/api/v1/events` | Bearer | Team pushes an event (stored in team_events) | 9 |
| GET | `/api/v1/stats` | Bearer | Existing stats (unchanged) | existing |
| GET | `/api/v1/stats?detailed=true` | Bearer | Extended stats snapshot | 8 |
| GET | `/api/v1/projects/{project}/summary` | Bearer | Project task summary | 8 |
| POST | `/api/v1/notifications` | Bearer | Ephemeral broadcast to SSE clients | 9 |
| GET | `/api/v1/notifications` | Bearer | Stub (empty array) | 9 |
| PUT | `/api/v1/status` | Bearer | Set own team status | 9 |
| GET | `/api/v1/status` | Bearer | All teams' statuses | 9 |
| GET | `/api/v1/status/{squad}` | Bearer | Specific team status | 9 |
| GET | `/api/v1/team-events` | Bearer | List stored team events | 9 |
| POST | `/api/v1/subscriptions` | Bearer | Subscribe to periodic events | 10 |
| GET | `/api/v1/subscriptions` | Bearer | List subscriptions | 10 |
| DELETE | `/api/v1/subscriptions/{id}` | Bearer | Unsubscribe | 10 |

---

## Complete MCP Tool Summary

| Tool | Signature | Build Order |
|------|-----------|-------------|
| `get_server_stats` | `() → str` | 8 |
| `get_project_summary` | `(project: str) → str` | 8 |
| `set_team_status` | `(status: str, message?: str) → str` | 9 |
| `get_team_status` | `(squad?: str) → str` | 9 |
| `post_team_event` | `(event_type: str, data?: dict) → str` | 9 |
| `get_team_events` | `(squad?, event_type?, since?, limit=50) → str` | 9 |
| `subscribe_events` | `(id, subscriber, url, event_type, project?, interval_sec?) → str` | 10 |
| `list_subscriptions` | `(subscriber?: str) → str` | 10 |
| `unsubscribe_events` | `(id: str, human_approval: bool = False) → str` | 10 |

---

## Security Notes for Dom

**Dom: please review these specific points before Build Order 9 is merged to main.**

### D1 — `POST /api/v1/notifications` payload validation

The `data` field is an arbitrary JSON object from the requester. It is stored ephemerally and broadcast to all SSE clients as-is. Risks:

- **XSS via SSE clients:** SSE clients that render `data` in a browser UI without escaping could be vulnerable. Confirm that all known consumers treat SSE event data as opaque JSON (not HTML). If any browser-based consumer exists, the `data` field must be sanitized server-side before broadcast.
- **Payload size:** There is no explicit size limit on the `data` field beyond the 1 MiB body cap (`_MAX_REST_BODY`). Consider whether a tighter cap on `data` specifically is warranted (e.g., 64 KiB).
- **squad field:** Validated as `str`, non-empty, under `_MAX_SHORT_FIELD`. The `squad` in the notification body is caller-supplied and NOT verified against the auth token — a token for squad "alpha" can claim to send a notification from squad "beta". This is intentional for v0.2.0 (notification identity is informational), but confirm this is acceptable.

### D2 — `POST /api/v1/events` (team event push)

- The `data` field follows the same risk profile as D1. Broadcast to SSE clients.
- `event_type` is a free-form string (no enumeration). Log injection risk if `event_type` is ever written to structured logs without sanitization. Confirm logging approach.

### D3 — `PUT /api/v1/status`

- The `message` field is optional, free-form, bounded only by `_MAX_SHORT_FIELD` (500 chars). No HTML escaping.
- `squad` identity is taken from the auth token (correct). No spoofing risk here.

### D4 — Rate limiting (deferred)

Andrew deferred rate limiting to v0.3.0. For v0.2.0, authenticated tenants are trusted. Dom: confirm this is acceptable given the authentication model (OPM_TENANT_KEYS or DB-registered tokens).

### D5 — Subscription SSRF

`subscribe_events` uses `_check_ssrf()` exactly as `register_webhook` does — HTTPS-only, RFC1918/loopback/link-local blocked. Andrew confirmed HTTPS-only. No changes needed here; noting for completeness.

---

## Implementation Checklist

### Build Order 8
- [ ] Add `time` import; add `StreamingResponse` to starlette.responses import
- [ ] Add `_start_time`, `_event_bus_clients`, `_bg_health_task`, `_bg_sub_task` closure variables
- [ ] Add `_publish_event()`, `_publish_queue_stats()`, `_publish_health_event()` helpers
- [ ] Add `_health_loop()` and `_ensure_bg_health_task()` background task functions
- [ ] Hook `_publish_event()` + `_publish_queue_stats()` into all 4 MCP task tools (create/update/complete/delete)
- [ ] Hook same into REST task endpoints (tasks_endpoint POST, task_endpoint PATCH + DELETE)
- [ ] Add `get_server_stats()` MCP tool
- [ ] Add `get_project_summary()` MCP tool — refactor into shared `_project_summary()` helper
- [ ] Add `events_endpoint` to `_build_rest_router()` (GET=SSE, POST=501 stub)
- [ ] Add `project_summary_endpoint` to `_build_rest_router()` — reuse `_project_summary()`
- [ ] Extend `stats_endpoint` with `?detailed=true` branch
- [ ] Update Router with new routes

### Build Order 9
- [ ] Append `team_status` + `team_events` DDL to `_SCHEMA`
- [ ] Add `VALID_TEAM_STATUSES` and `VALID_NOTIFICATION_TYPES` constants
- [ ] Add `set_team_status()`, `get_team_status()`, `post_team_event()`, `get_team_events()` MCP tools
- [ ] Add `notifications_endpoint` to REST router (POST + GET stub)
- [ ] Add `status_endpoint` to REST router (PUT + GET)
- [ ] Add `status_squad_endpoint` to REST router
- [ ] Replace `events_endpoint` POST 501 stub with full team event push logic
- [ ] Add `team_events_endpoint` to REST router (GET list)
- [ ] Update Router with new routes

### Build Order 10
- [ ] Append `event_subscriptions` DDL to `_SCHEMA`
- [ ] Add `VALID_SUBSCRIPTION_EVENTS`, `_SUB_MIN_INTERVAL`, `_SUB_MAX_INTERVAL` constants
- [ ] Add `subscribe_events()`, `list_subscriptions()`, `unsubscribe_events()` MCP tools
- [ ] Add `_fire_event_subscriptions()` helper (after `_fire_webhooks`)
- [ ] Add `_subscriptions_loop()` and `_ensure_bg_sub_task()` functions
- [ ] Call `_ensure_bg_sub_task()` from SSE endpoint on connect
- [ ] Add `subscriptions_endpoint` + `subscription_endpoint` to REST router
- [ ] Update Router with new routes

---

*Elliot sign-off: This brief reflects Andrew's confirmed decisions, Mobley's protocol design, and my architecture design fully reconciled. All schema DDL is final — no changes without my approval.*
