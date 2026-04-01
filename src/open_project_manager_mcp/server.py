"""SQLite-backed project management MCP server."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.authentication import AuthenticationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Router

VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}
VALID_WEBHOOK_EVENTS = {"task.created", "task.updated", "task.completed", "task.deleted"}

_VALID_UPDATE_COLUMNS = frozenset(
    {"title", "description", "priority", "project", "status", "assignee", "tags", "due_date", "updated_at"}
)

_MAX_LIMIT = 500
_BULK_MAX = 50
_MAX_SHORT_FIELD = 500
_MAX_DESCRIPTION = 50_000
_MAX_IMPORT_SIZE = 5_000_000
_MAX_REST_BODY = 1_048_576  # 1 MiB — cap REST API request bodies to prevent OOM DoS
_MAX_TAG_LENGTH = 100
_MAX_TAGS_COUNT = 50

_PRIORITY_CASE = (
    "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
    "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"
)

_SSRF_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),   # IPv6 unique-local (RFC 4193) — equivalent to RFC1918
]


class ApiKeyVerifier(TokenVerifier):
    """Validates Bearer API keys and injects tenant_id into token claims."""

    def __init__(self, tenant_keys: dict[str, str]):
        self._tenants: list[tuple[str, str]] = list(tenant_keys.items())

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            matched_tenant = None
            for tenant_id, api_key in self._tenants:
                if hmac.compare_digest(token, api_key):
                    matched_tenant = tenant_id
                    break
            if not matched_tenant:
                raise AuthenticationError("Invalid API key")
            return AccessToken(
                token=token,
                client_id=matched_tenant,
                scopes=["api"],
            )
        except AuthenticationError:
            raise
        except Exception:
            raise AuthenticationError("Authentication failed") from None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    project     TEXT NOT NULL DEFAULT 'default',
    priority    TEXT NOT NULL DEFAULT 'medium',
    status      TEXT NOT NULL DEFAULT 'pending',
    assignee    TEXT,
    tags        TEXT,
    sort_order  INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_deps (
    task_id     TEXT NOT NULL,
    depends_on  TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on),
    FOREIGN KEY (task_id)    REFERENCES tasks(id),
    FOREIGN KEY (depends_on) REFERENCES tasks(id)
);

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

CREATE TABLE IF NOT EXISTS webhooks (
    id         TEXT    PRIMARY KEY,
    url        TEXT    NOT NULL,
    project    TEXT,
    events     TEXT    NOT NULL,
    secret     TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL
);
"""

_FTS_SCHEMA = """
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
"""


def create_server(
    db_path: str,
    tenant_keys: Optional[dict[str, str]] = None,
    server_url: str = "http://localhost:8765",
    transport_security: Optional[TransportSecuritySettings] = None,
    enable_rest: bool = False,
) -> FastMCP:
    """Create and return the project manager MCP server backed by SQLite at db_path."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    # due_date column migration — idempotent
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # FTS5 setup — graceful degradation if not compiled in
    _fts_available = False
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.execute("INSERT INTO tasks_fts(tasks_fts) VALUES('rebuild')")
        conn.commit()
        _fts_available = True
    except Exception:
        _fts_available = False

    _lock = asyncio.Lock()

    auth_settings = None
    token_verifier = None
    if tenant_keys:
        token_verifier = ApiKeyVerifier(tenant_keys)
        auth_settings = AuthSettings(
            issuer_url=server_url,
            resource_server_url=server_url,
        )

    mcp = FastMCP(
        "open-project-manager-mcp",
        token_verifier=token_verifier,
        auth=auth_settings,
        transport_security=transport_security,
    )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row(row) -> dict:
        d = dict(row)
        if d.get("tags"):
            d["tags"] = json.loads(d["tags"])
        return d

    def _log(task_id, action, field=None, old_value=None, new_value=None, actor="system"):
        """Insert one activity_log row — caller is responsible for committing."""
        conn.execute(
            "INSERT INTO activity_log (task_id, action, field, old_value, new_value, actor, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                action,
                field,
                str(old_value) if old_value is not None else None,
                str(new_value) if new_value is not None else None,
                actor,
                _now(),
            ),
        )

    def _get_actor() -> str:
        try:
            ctx = mcp.get_context()
            return getattr(getattr(ctx, "auth", None), "client_id", None) or "system"
        except Exception:
            return "system"

    def _parse_due_date(due_date: str) -> Optional[str]:
        """Return error string if invalid, None if valid ISO 8601."""
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
            return None
        except ValueError:
            pass
        try:
            datetime.fromisoformat(due_date)
            return None
        except ValueError:
            return (
                f"Error: invalid due_date '{due_date}'."
                " Must be YYYY-MM-DD or ISO 8601 datetime"
            )

    def _validate_create_params(
        id: str,
        title: str,
        description: Optional[str],
        priority: str,
        project: str,
        assignee: Optional[str],
        due_date: Optional[str],
    ) -> Optional[str]:
        for field, val, max_len in [
            ("id", id, _MAX_SHORT_FIELD),
            ("title", title, _MAX_SHORT_FIELD),
            ("project", project, _MAX_SHORT_FIELD),
            ("assignee", assignee, _MAX_SHORT_FIELD),
            ("description", description, _MAX_DESCRIPTION),
        ]:
            if val is not None and len(val) > max_len:
                return f"Error: '{field}' exceeds maximum length of {max_len} characters"
        if priority not in VALID_PRIORITIES:
            return f"Error: invalid priority '{priority}'. Must be one of: {', '.join(sorted(VALID_PRIORITIES))}"
        if due_date is not None:
            err = _parse_due_date(due_date)
            if err:
                return err
        return None

    def _validate_tags(tags: Optional[list]) -> Optional[str]:
        """Return error string if tags are invalid, None if valid."""
        if tags is None:
            return None
        if len(tags) > _MAX_TAGS_COUNT:
            return f"Error: too many tags (max {_MAX_TAGS_COUNT}, got {len(tags)})"
        for i, tag in enumerate(tags):
            if not isinstance(tag, str):
                return f"Error: tag at index {i} must be a string"
            if len(tag) > _MAX_TAG_LENGTH:
                return f"Error: tag at index {i} exceeds maximum length of {_MAX_TAG_LENGTH} characters"
        return None

    def _validate_update_params(
        title: Optional[str],
        description: Optional[str],
        priority: Optional[str],
        project: Optional[str],
        status: Optional[str],
        assignee: Optional[str],
        due_date: Optional[str],
    ) -> Optional[str]:
        for field, val, max_len in [
            ("title", title, _MAX_SHORT_FIELD),
            ("project", project, _MAX_SHORT_FIELD),
            ("assignee", assignee, _MAX_SHORT_FIELD),
            ("description", description, _MAX_DESCRIPTION),
        ]:
            if val is not None and len(val) > max_len:
                return f"Error: '{field}' exceeds maximum length of {max_len} characters"
        if priority is not None and priority not in VALID_PRIORITIES:
            return f"Error: invalid priority '{priority}'. Must be one of: {', '.join(sorted(VALID_PRIORITIES))}"
        if status is not None and status not in VALID_STATUSES:
            return f"Error: invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
        if due_date is not None:
            err = _parse_due_date(due_date)
            if err:
                return err
        return None

    async def _check_ssrf(url: str) -> Optional[str]:
        """Return error string if URL is SSRF-risky or invalid, None if safe."""
        if not url.startswith("https://"):
            return "Error: webhook URL must use HTTPS"
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return "Error: invalid webhook URL — cannot extract hostname"
        try:
            loop = asyncio.get_event_loop()
            addrinfos = await loop.run_in_executor(None, socket.getaddrinfo, hostname, 443)
        except socket.gaierror as exc:
            return f"Error: cannot resolve hostname '{hostname}': {exc}"
        for _, _, _, _, sockaddr in addrinfos:
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            # Check the resolved IP — and also the underlying IPv4 form for any
            # IPv4-mapped IPv6 address (e.g. ::ffff:10.0.0.1) so RFC1918 blocks
            # cannot be bypassed by requesting the IPv6-mapped variant.
            ips_to_check = [ip]
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ips_to_check.append(ip.ipv4_mapped)
            for check_ip in ips_to_check:
                for net in _SSRF_BLOCKED_NETWORKS:
                    if check_ip in net:
                        return f"Error: webhook URL resolves to a blocked address ({ip})"
        return None

    # ------------------------------------------------------------------
    # Fire-and-forget webhook delivery
    # ------------------------------------------------------------------

    async def _fire_webhooks(event: str, task_id: str, project: Optional[str], data: dict) -> None:
        try:
            import httpx
        except ImportError:
            return
        try:
            rows = conn.execute(
                "SELECT id, url, secret, events FROM webhooks"
                " WHERE enabled=1 AND (project IS NULL OR project=?)",
                (project,),
            ).fetchall()
        except Exception:
            return
        for row in rows:
            try:
                events_list = json.loads(row["events"])
            except Exception:
                continue
            if event not in events_list:
                continue
            payload = {
                "event": event,
                "task_id": task_id,
                "timestamp": _now(),
                "data": data,
            }
            payload_bytes = json.dumps(payload).encode()
            headers = {"Content-Type": "application/json"}
            if row["secret"]:
                sig = hmac.new(
                    row["secret"].encode(), payload_bytes, hashlib.sha256
                ).hexdigest()
                headers["X-Hub-Signature-256"] = f"sha256={sig}"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(row["url"], content=payload_bytes, headers=headers)
            except Exception:
                pass  # fire-and-forget; no retries in v0.2.0

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    @mcp.tool()
    async def create_task(
        id: str,
        title: str,
        description: Optional[str] = None,
        priority: str = "medium",
        project: str = "default",
        tags: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        due_date: Optional[str] = None,
    ) -> str:
        """Create a new task. id is a caller-supplied slug (e.g. 'auth-login-ui')."""
        err = _validate_create_params(id, title, description, priority, project, assignee, due_date)
        if err:
            return err
        tags_err = _validate_tags(tags)
        if tags_err:
            return tags_err
        actor = _get_actor()
        now = _now()
        tags_json = json.dumps(tags) if tags else None
        async with _lock:
            try:
                conn.execute(
                    "INSERT INTO tasks"
                    " (id, title, description, project, priority, status, assignee, tags, due_date, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                    (id, title, description, project, priority, assignee, tags_json, due_date, now, now),
                )
                _log(id, "created", actor=actor)
                conn.commit()
            except sqlite3.IntegrityError:
                return f"Error: task '{id}' already exists"
            except sqlite3.Error:
                return "Error: database error creating task"
        asyncio.create_task(
            _fire_webhooks(
                "task.created",
                id,
                project,
                {"id": id, "title": title, "priority": priority, "status": "pending", "project": project},
            )
        )
        return json.dumps({"id": id, "status": "pending", "priority": priority, "project": project})

    @mcp.tool()
    async def update_task(
        task_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        project: Optional[str] = None,
        status: Optional[str] = None,
        assignee: Optional[str] = None,
        tags: Optional[list[str]] = None,
        due_date: Optional[str] = None,
    ) -> str:
        """Update fields on an existing task. Only provided fields are changed."""
        err = _validate_update_params(title, description, priority, project, status, assignee, due_date)
        if err:
            return err
        updates: dict[str, object] = {}
        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if priority is not None:
            updates["priority"] = priority
        if project is not None:
            updates["project"] = project
        if status is not None:
            updates["status"] = status
        if assignee is not None:
            updates["assignee"] = assignee
        if tags is not None:
            tags_err = _validate_tags(tags)
            if tags_err:
                return tags_err
            updates["tags"] = json.dumps(tags)
        if due_date is not None:
            updates["due_date"] = due_date
        if not updates:
            return "Error: no fields to update"
        unknown = set(updates) - _VALID_UPDATE_COLUMNS
        if unknown:
            return f"Error: internal error — unknown field(s): {', '.join(sorted(unknown))}"
        actor = _get_actor()
        async with _lock:
            old_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if old_row is None:
                return f"Error: task '{task_id}' not found"
            old_data = dict(old_row)
            updates["updated_at"] = _now()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [task_id]
            try:
                conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
                for field, new_val in updates.items():
                    if field == "updated_at":
                        continue
                    old_val = old_data.get(field)
                    if old_val != new_val:
                        _log(task_id, "updated", field=field, old_value=old_val, new_value=new_val, actor=actor)
                conn.commit()
            except sqlite3.Error:
                return "Error: database error updating task"
        task_project = updates.get("project") or old_data.get("project", "default")
        asyncio.create_task(
            _fire_webhooks("task.updated", task_id, task_project, {"id": task_id, "updated": list(updates.keys())})
        )
        return json.dumps({"id": task_id, "updated": list(updates.keys())})

    @mcp.tool()
    async def complete_task(task_id: str) -> str:
        """Mark a task as done."""
        actor = _get_actor()
        async with _lock:
            old_row = conn.execute("SELECT project FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if old_row is None:
                return f"Error: task '{task_id}' not found"
            task_project = old_row["project"]
            cur = conn.execute(
                "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
                (_now(), task_id),
            )
            _log(task_id, "completed", actor=actor)
            conn.commit()
            if cur.rowcount == 0:
                return f"Error: task '{task_id}' not found"
        asyncio.create_task(
            _fire_webhooks("task.completed", task_id, task_project, {"id": task_id, "status": "done"})
        )
        return json.dumps({"id": task_id, "status": "done"})

    @mcp.tool()
    async def delete_task(task_id: str, human_approval: bool = False) -> str:
        """Delete a task and its dependency edges. Requires human_approval=True."""
        if not human_approval:
            return "Error: human_approval=True is required to delete a task"
        actor = _get_actor()
        async with _lock:
            row = conn.execute("SELECT project FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return f"Error: task '{task_id}' not found"
            task_project = row["project"]
            conn.execute(
                "DELETE FROM task_deps WHERE task_id = ? OR depends_on = ?",
                (task_id, task_id),
            )
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            _log(task_id, "deleted", actor=actor)
            conn.commit()
            if cur.rowcount == 0:
                return f"Error: task '{task_id}' not found"
        asyncio.create_task(
            _fire_webhooks("task.deleted", task_id, task_project, {"id": task_id})
        )
        return json.dumps({"id": task_id, "deleted": True})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_task(task_id: str) -> str:
        """Get a single task by ID, including its dependency info."""
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return f"Error: task '{task_id}' not found"
            task = _row(row)
            task["depends_on"] = [
                r[0]
                for r in conn.execute(
                    "SELECT depends_on FROM task_deps WHERE task_id = ?", (task_id,)
                ).fetchall()
            ]
            task["blocked_by"] = [
                r[0]
                for r in conn.execute(
                    "SELECT td.depends_on FROM task_deps td"
                    " JOIN tasks t ON td.depends_on = t.id"
                    " WHERE td.task_id = ? AND t.status != 'done'",
                    (task_id,),
                ).fetchall()
            ]
            return json.dumps(task)
        except sqlite3.Error:
            return "Error: database error reading task"

    @mcp.tool()
    def list_tasks(
        project: Optional[str] = None,
        assignee: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """List tasks with optional filters. Returns compact rows: id, title, priority, status, assignee.
        Sorted by priority (critical first), then created_at. Supports pagination via limit/offset.
        Use get_task(task_id) to fetch full details for a specific task."""
        limit = max(1, min(limit, _MAX_LIMIT))
        conditions, params = [], []
        if project:
            conditions.append("project = ?")
            params.append(project)
        if assignee:
            conditions.append("assignee = ?")
            params.append(assignee)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if priority:
            conditions.append("priority = ?")
            params.append(priority)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        try:
            rows = conn.execute(
                f"SELECT id, title, priority, status, assignee FROM tasks"
                f" {where} ORDER BY {_PRIORITY_CASE}, created_at LIMIT ? OFFSET ?",
                params + [limit + 1, offset],
            ).fetchall()
        except sqlite3.Error:
            return "Error: database error listing tasks"
        has_more = len(rows) > limit
        return json.dumps({
            "tasks": [dict(r) for r in rows[:limit]],
            "has_more": has_more,
            "offset": offset,
        })

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    @mcp.tool()
    async def add_dependency(task_id: str, depends_on_id: str) -> str:
        """Mark that task_id cannot start until depends_on_id is done."""
        if task_id == depends_on_id:
            return "Error: a task cannot depend on itself"
        actor = _get_actor()
        async with _lock:
            if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
                return f"Error: task '{task_id}' not found"
            if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (depends_on_id,)).fetchone():
                return f"Error: task '{depends_on_id}' not found"
            try:
                conn.execute(
                    "INSERT INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                    (task_id, depends_on_id),
                )
                _log(task_id, "dep_added", field="depends_on", new_value=depends_on_id, actor=actor)
                conn.commit()
            except sqlite3.IntegrityError:
                return "Dependency already exists"
        return json.dumps({"task_id": task_id, "depends_on": depends_on_id})

    @mcp.tool()
    async def remove_dependency(task_id: str, depends_on_id: str) -> str:
        """Remove a dependency edge between two tasks."""
        actor = _get_actor()
        async with _lock:
            cur = conn.execute(
                "DELETE FROM task_deps WHERE task_id = ? AND depends_on = ?",
                (task_id, depends_on_id),
            )
            if cur.rowcount == 0:
                return "Error: dependency not found"
            _log(task_id, "dep_removed", field="depends_on", old_value=depends_on_id, actor=actor)
            conn.commit()
        return json.dumps({"task_id": task_id, "depends_on": depends_on_id, "removed": True})

    @mcp.tool()
    def list_ready_tasks(
        project: Optional[str] = None,
        assignee: Optional[str] = None,
        limit: int = 10,
    ) -> str:
        """List pending tasks whose dependencies are all done — safe to start immediately.
        Returns compact rows: id, title, priority, status, assignee, sorted by priority (critical first).
        Use get_task(task_id) to inspect full details or dependency list."""
        limit = max(1, min(limit, _MAX_LIMIT))
        conditions = ["t.status = 'pending'"]
        params: list[object] = []
        if project:
            conditions.append("t.project = ?")
            params.append(project)
        if assignee:
            conditions.append("t.assignee = ?")
            params.append(assignee)
        where = f"WHERE {' AND '.join(conditions)}"
        priority_case = _PRIORITY_CASE.replace("priority", "t.priority")
        try:
            rows = conn.execute(
                f"""
                SELECT t.id, t.title, t.priority, t.status, t.assignee FROM tasks t
                {where}
                AND NOT EXISTS (
                    SELECT 1 FROM task_deps td
                    JOIN tasks dep ON td.depends_on = dep.id
                    WHERE td.task_id = t.id AND dep.status != 'done'
                )
                ORDER BY {priority_case}, t.created_at
                LIMIT ?
                """,
                params + [limit],
            ).fetchall()
        except sqlite3.Error:
            return "Error: database error listing ready tasks"
        return json.dumps({"tasks": [dict(r) for r in rows], "count": len(rows)})

    # ------------------------------------------------------------------
    # Projects & Stats
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_projects() -> str:
        """List all projects with open and total task counts."""
        try:
            rows = conn.execute(
                "SELECT project,"
                " COUNT(*) as total,"
                " SUM(CASE WHEN status != 'done' THEN 1 ELSE 0 END) as open"
                " FROM tasks GROUP BY project ORDER BY project"
            ).fetchall()
        except sqlite3.Error:
            return "Error: database error listing projects"
        return json.dumps({
            "projects": [
                {"project": r["project"], "open": r["open"], "total": r["total"]}
                for r in rows
            ]
        })

    @mcp.tool()
    def get_stats() -> str:
        """Task counts by status and priority, plus the age of the oldest open item."""
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
            return "Error: database error reading stats"
        return json.dumps({
            "by_status": by_status,
            "by_priority": by_priority,
            "oldest_open": oldest["oldest"] if oldest else None,
        })

    # ------------------------------------------------------------------
    # Feature 1: Due dates
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_overdue_tasks(
        project: Optional[str] = None,
        assignee: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """List tasks whose due_date is in the past and status is not done.
        Returns compact rows: id, title, priority, status, due_date."""
        limit = max(1, min(limit, _MAX_LIMIT))
        now_str = datetime.now(timezone.utc).isoformat()
        conditions = [
            "due_date IS NOT NULL",
            "due_date < ?",
            "status != 'done'",
        ]
        params: list[object] = [now_str]
        if project:
            conditions.append("project = ?")
            params.append(project)
        if assignee:
            conditions.append("assignee = ?")
            params.append(assignee)
        where = f"WHERE {' AND '.join(conditions)}"
        priority_case = _PRIORITY_CASE
        try:
            rows = conn.execute(
                f"SELECT id, title, priority, status, due_date FROM tasks"
                f" {where} ORDER BY {priority_case}, due_date ASC LIMIT ?",
                params + [limit],
            ).fetchall()
        except sqlite3.Error:
            return "Error: database error listing overdue tasks"
        return json.dumps({"tasks": [dict(r) for r in rows], "count": len(rows)})

    @mcp.tool()
    def list_due_soon_tasks(
        days: int = 7,
        project: Optional[str] = None,
        assignee: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """List tasks due within the next N days (1–365). Returns compact rows."""
        days = max(1, min(days, 365))
        limit = max(1, min(limit, _MAX_LIMIT))
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        future_str = (now_dt + timedelta(days=days)).isoformat()
        conditions = [
            "due_date IS NOT NULL",
            "due_date >= ?",
            "due_date <= ?",
            "status != 'done'",
        ]
        params: list[object] = [now_str, future_str]
        if project:
            conditions.append("project = ?")
            params.append(project)
        if assignee:
            conditions.append("assignee = ?")
            params.append(assignee)
        where = f"WHERE {' AND '.join(conditions)}"
        priority_case = _PRIORITY_CASE
        try:
            rows = conn.execute(
                f"SELECT id, title, priority, status, due_date FROM tasks"
                f" {where} ORDER BY {priority_case}, due_date ASC LIMIT ?",
                params + [limit],
            ).fetchall()
        except sqlite3.Error:
            return "Error: database error listing due-soon tasks"
        return json.dumps({"tasks": [dict(r) for r in rows], "count": len(rows)})

    # ------------------------------------------------------------------
    # Feature 2: Full-text search
    # ------------------------------------------------------------------

    @mcp.tool()
    def search_tasks(
        query: str,
        project: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """Full-text search across task title, description, and tags. Ranked by relevance."""
        if not _fts_available:
            return "Error: full-text search is not available (FTS5 not compiled into SQLite)"
        if len(query) > _MAX_SHORT_FIELD:
            return f"Error: 'query' exceeds maximum length of {_MAX_SHORT_FIELD} characters"
        limit = max(1, min(limit, _MAX_LIMIT))
        conditions: list[str] = []
        params: list[object] = [query]
        if project:
            conditions.append("t.project = ?")
            params.append(project)
        if status:
            conditions.append("t.status = ?")
            params.append(status)
        extra_where = (" AND " + " AND ".join(conditions)) if conditions else ""
        try:
            rows = conn.execute(
                f"SELECT t.id, t.title, t.priority, t.status, t.assignee"
                f" FROM tasks_fts"
                f" JOIN tasks t ON tasks_fts.rowid = t.rowid"
                f" WHERE tasks_fts MATCH ?"
                f"{extra_where}"
                f" ORDER BY rank LIMIT ?",
                params + [limit + 1],
            ).fetchall()
        except sqlite3.Error:
            return "Error: search failed — invalid query syntax"
        has_more = len(rows) > limit
        return json.dumps({
            "tasks": [dict(r) for r in rows[:limit]],
            "has_more": has_more,
        })

    # ------------------------------------------------------------------
    # Feature 3: Bulk operations
    # ------------------------------------------------------------------

    @mcp.tool()
    async def create_tasks(tasks: list[dict]) -> str:
        """Bulk-create up to 50 tasks in a single transaction. Per-item errors collected."""
        if len(tasks) > _BULK_MAX:
            return f"Error: too many tasks (max {_BULK_MAX}, got {len(tasks)})"
        actor = _get_actor()
        created: list[str] = []
        errors: list[dict] = []
        async with _lock:
            for item in tasks:
                tid = item.get("id", "")
                err = _validate_create_params(
                    tid,
                    item.get("title", ""),
                    item.get("description"),
                    item.get("priority", "medium"),
                    item.get("project", "default"),
                    item.get("assignee"),
                    item.get("due_date"),
                )
                if err:
                    errors.append({"id": tid, "error": err})
                    continue
                if not tid:
                    errors.append({"id": "", "error": "Error: 'id' is required"})
                    continue
                if not item.get("title"):
                    errors.append({"id": tid, "error": "Error: 'title' is required"})
                    continue
                item_tags = item.get("tags")
                tags_err = _validate_tags(item_tags)
                if tags_err:
                    errors.append({"id": tid, "error": tags_err})
                    continue
                now = _now()
                tags_json = json.dumps(item_tags) if item_tags else None
                try:
                    conn.execute(
                        "INSERT INTO tasks"
                        " (id, title, description, project, priority, status, assignee, tags, due_date, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                        (
                            tid,
                            item["title"],
                            item.get("description"),
                            item.get("project", "default"),
                            item.get("priority", "medium"),
                            item.get("assignee"),
                            tags_json,
                            item.get("due_date"),
                            now,
                            now,
                        ),
                    )
                    _log(tid, "created", actor=actor)
                    created.append(tid)
                except sqlite3.IntegrityError:
                    errors.append({"id": tid, "error": f"Error: task '{tid}' already exists"})
                except sqlite3.Error as exc:
                    errors.append({"id": tid, "error": f"Error: database error: {exc}"})
            conn.commit()
        return json.dumps({"created": created, "errors": errors})

    @mcp.tool()
    async def update_tasks(updates: list[dict]) -> str:
        """Bulk-update up to 50 tasks in a single transaction. Per-item errors collected."""
        if len(updates) > _BULK_MAX:
            return f"Error: too many updates (max {_BULK_MAX}, got {len(updates)})"
        actor = _get_actor()
        updated: list[str] = []
        errors: list[dict] = []
        async with _lock:
            for item in updates:
                task_id = item.get("task_id", "")
                if not task_id:
                    errors.append({"id": "", "error": "Error: 'task_id' is required"})
                    continue
                err = _validate_update_params(
                    item.get("title"),
                    item.get("description"),
                    item.get("priority"),
                    item.get("project"),
                    item.get("status"),
                    item.get("assignee"),
                    item.get("due_date"),
                )
                if err:
                    errors.append({"id": task_id, "error": err})
                    continue
                upd: dict[str, object] = {}
                for f in ("title", "description", "priority", "project", "status", "assignee", "due_date"):
                    if f in item and item[f] is not None:
                        upd[f] = item[f]
                if "tags" in item and item["tags"] is not None:
                    tags_err = _validate_tags(item["tags"])
                    if tags_err:
                        errors.append({"id": task_id, "error": tags_err})
                        continue
                    upd["tags"] = json.dumps(item["tags"])
                if not upd:
                    errors.append({"id": task_id, "error": "Error: no fields to update"})
                    continue
                old_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if old_row is None:
                    errors.append({"id": task_id, "error": f"Error: task '{task_id}' not found"})
                    continue
                old_data = dict(old_row)
                upd["updated_at"] = _now()
                set_clause = ", ".join(f"{k} = ?" for k in upd)
                vals = list(upd.values()) + [task_id]
                try:
                    conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", vals)
                    for field, new_val in upd.items():
                        if field == "updated_at":
                            continue
                        old_val = old_data.get(field)
                        if old_val != new_val:
                            _log(task_id, "updated", field=field, old_value=old_val, new_value=new_val, actor=actor)
                    updated.append(task_id)
                except sqlite3.Error as exc:
                    errors.append({"id": task_id, "error": f"Error: database error: {exc}"})
            conn.commit()
        return json.dumps({"updated": updated, "errors": errors})

    @mcp.tool()
    async def complete_tasks(ids: list[str]) -> str:
        """Bulk-complete up to 50 tasks in a single transaction."""
        if len(ids) > _BULK_MAX:
            return f"Error: too many ids (max {_BULK_MAX}, got {len(ids)})"
        for i, task_id in enumerate(ids):
            if len(task_id) > _MAX_SHORT_FIELD:
                return f"Error: task_id at index {i} exceeds maximum length of {_MAX_SHORT_FIELD} characters"
        actor = _get_actor()
        completed: list[str] = []
        not_found: list[str] = []
        async with _lock:
            for task_id in ids:
                row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if row is None:
                    not_found.append(task_id)
                    continue
                conn.execute(
                    "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
                    (_now(), task_id),
                )
                _log(task_id, "completed", actor=actor)
                completed.append(task_id)
            conn.commit()
        return json.dumps({"completed": completed, "not_found": not_found})

    # ------------------------------------------------------------------
    # Feature 4: Activity log
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_task_activity(task_id: str, limit: int = 50) -> str:
        """Get the activity history for a task, newest first."""
        limit = max(1, min(limit, 200))
        # Activity log is orphan-safe — query it directly even if task was deleted.
        try:
            rows = conn.execute(
                "SELECT id, action, field, old_value, new_value, actor, created_at"
                " FROM activity_log WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        except sqlite3.Error:
            return "Error: database error reading activity log"
        return json.dumps({
            "activity": [dict(r) for r in rows],
            "count": len(rows),
        })

    @mcp.tool()
    def get_activity_log(project: Optional[str] = None, limit: int = 50) -> str:
        """Get recent activity across all tasks (or filtered by project)."""
        limit = max(1, min(limit, 200))
        try:
            if project:
                rows = conn.execute(
                    "SELECT al.id, al.task_id, al.action, al.field, al.old_value,"
                    " al.new_value, al.actor, al.created_at"
                    " FROM activity_log al"
                    " JOIN tasks t ON al.task_id = t.id"
                    " WHERE t.project = ?"
                    " ORDER BY al.created_at DESC LIMIT ?",
                    (project, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, task_id, action, field, old_value, new_value, actor, created_at"
                    " FROM activity_log ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error:
            return "Error: database error reading activity log"
        return json.dumps({"activity": [dict(r) for r in rows], "count": len(rows)})

    # ------------------------------------------------------------------
    # Feature 5: Export / Import
    # ------------------------------------------------------------------

    @mcp.tool()
    def export_all_tasks(project: Optional[str] = None) -> str:
        """Export tasks (and their dependencies) to a portable JSON string."""
        try:
            if project:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE project = ? ORDER BY created_at", (project,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
            task_ids = {r["id"] for r in rows}
            tasks_list = [_row(r) for r in rows]
            if project and task_ids:
                placeholders = ",".join("?" * len(task_ids))
                dep_rows = conn.execute(
                    f"SELECT task_id, depends_on FROM task_deps"
                    f" WHERE task_id IN ({placeholders}) AND depends_on IN ({placeholders})",
                    list(task_ids) + list(task_ids),
                ).fetchall()
            elif not project:
                dep_rows = conn.execute("SELECT task_id, depends_on FROM task_deps").fetchall()
            else:
                dep_rows = []
        except sqlite3.Error as exc:
            return f"Error: database error during export: {exc}"
        return json.dumps({
            "version": "1.0",
            "exported_at": _now(),
            "tasks": tasks_list,
            "deps": [{"task_id": r["task_id"], "depends_on": r["depends_on"]} for r in dep_rows],
        })

    @mcp.tool()
    async def import_tasks(data: str, merge: bool = False) -> str:
        """Import tasks from a JSON string produced by export_all_tasks.
        merge=False aborts on any conflicting task ID; merge=True silently skips existing."""
        if len(data) > _MAX_IMPORT_SIZE:
            return f"Error: data exceeds maximum size of {_MAX_IMPORT_SIZE} characters (~5MB)"
        try:
            doc = json.loads(data)
        except json.JSONDecodeError as exc:
            return f"Error: invalid JSON: {exc}"
        if "version" not in doc:
            return "Error: missing 'version' field in import data"
        if not isinstance(doc.get("tasks"), list):
            return "Error: 'tasks' must be a list in import data"
        tasks_to_import = doc["tasks"]
        deps_to_import = doc.get("deps", [])
        for i, task in enumerate(tasks_to_import):
            if not isinstance(task, dict):
                return f"Error: task at index {i} must be an object"
            if not task.get("id"):
                return f"Error: task at index {i} is missing 'id'"
            if not task.get("title"):
                return f"Error: task '{task.get('id', i)}' is missing 'title'"
            err = _validate_create_params(
                task["id"],
                task["title"],
                task.get("description"),
                task.get("priority", "medium"),
                task.get("project", "default"),
                task.get("assignee"),
                task.get("due_date"),
            )
            if err:
                return f"Error in task '{task['id']}': {err}"
        imported = 0
        skipped = 0
        errs: list[dict] = []
        async with _lock:
            if not merge:
                conflicts = [
                    t["id"]
                    for t in tasks_to_import
                    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (t["id"],)).fetchone()
                ]
                if conflicts:
                    return f"Error: task IDs already exist: {', '.join(conflicts)}"
            for task in tasks_to_import:
                now = _now()
                tags = task.get("tags")
                tags_json = json.dumps(tags) if tags else None
                try:
                    if merge:
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO tasks"
                            " (id, title, description, project, priority, status, assignee, tags, due_date, created_at, updated_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                task["id"], task["title"], task.get("description"),
                                task.get("project", "default"), task.get("priority", "medium"),
                                task.get("status", "pending"), task.get("assignee"),
                                tags_json, task.get("due_date"),
                                task.get("created_at", now), task.get("updated_at", now),
                            ),
                        )
                        if cur.rowcount > 0:
                            imported += 1
                        else:
                            skipped += 1
                    else:
                        conn.execute(
                            "INSERT INTO tasks"
                            " (id, title, description, project, priority, status, assignee, tags, due_date, created_at, updated_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                task["id"], task["title"], task.get("description"),
                                task.get("project", "default"), task.get("priority", "medium"),
                                task.get("status", "pending"), task.get("assignee"),
                                tags_json, task.get("due_date"),
                                task.get("created_at", now), task.get("updated_at", now),
                            ),
                        )
                        imported += 1
                except sqlite3.IntegrityError as exc:
                    errs.append({"id": task["id"], "error": str(exc)})
                except sqlite3.Error as exc:
                    errs.append({"id": task["id"], "error": str(exc)})
            for dep in deps_to_import:
                try:
                    if merge:
                        conn.execute(
                            "INSERT OR IGNORE INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                            (dep["task_id"], dep["depends_on"]),
                        )
                    else:
                        conn.execute(
                            "INSERT OR IGNORE INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                            (dep["task_id"], dep["depends_on"]),
                        )
                except sqlite3.Error:
                    pass
            conn.commit()
        return json.dumps({"imported": imported, "skipped": skipped, "errors": errs})

    # ------------------------------------------------------------------
    # Feature 7: Webhooks
    # ------------------------------------------------------------------

    @mcp.tool()
    async def register_webhook(
        id: str,
        url: str,
        events: list[str],
        project: Optional[str] = None,
        secret: Optional[str] = None,
    ) -> str:
        """Register a webhook. URL must be HTTPS and resolve to a public address."""
        try:
            import httpx  # noqa: F401
        except ImportError:
            return (
                "Error: httpx is required for webhooks. "
                "Install with: pip install 'open-project-manager-mcp[webhooks]'"
            )
        if len(id) > _MAX_SHORT_FIELD:
            return f"Error: 'id' exceeds maximum length of {_MAX_SHORT_FIELD} characters"
        ssrf_err = await _check_ssrf(url)
        if ssrf_err:
            return ssrf_err
        if not events:
            return "Error: 'events' must be a non-empty list"
        invalid_events = set(events) - VALID_WEBHOOK_EVENTS
        if invalid_events:
            return f"Error: invalid events: {', '.join(sorted(invalid_events))}. Valid: {', '.join(sorted(VALID_WEBHOOK_EVENTS))}"
        async with _lock:
            try:
                conn.execute(
                    "INSERT INTO webhooks (id, url, project, events, secret, enabled, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (id, url, project, json.dumps(events), secret, _now()),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                return f"Error: webhook '{id}' already exists"
            except sqlite3.Error:
                return "Error: database error registering webhook"
        return json.dumps({"id": id, "url": url, "events": events, "project": project})

    @mcp.tool()
    def list_webhooks(project: Optional[str] = None) -> str:
        """List registered webhooks. Secrets are never returned."""
        try:
            if project:
                rows = conn.execute(
                    "SELECT id, url, project, events, enabled FROM webhooks WHERE project = ? ORDER BY created_at",
                    (project,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, url, project, events, enabled FROM webhooks ORDER BY created_at"
                ).fetchall()
        except sqlite3.Error:
            return "Error: database error listing webhooks"
        webhooks = []
        for r in rows:
            w = dict(r)
            try:
                w["events"] = json.loads(w["events"])
            except Exception:
                pass
            webhooks.append(w)
        return json.dumps({"webhooks": webhooks})

    @mcp.tool()
    async def delete_webhook(id: str, human_approval: bool = False) -> str:
        """Delete a webhook registration. Requires human_approval=True."""
        if not human_approval:
            return "Error: human_approval=True is required to delete a webhook"
        async with _lock:
            cur = conn.execute("DELETE FROM webhooks WHERE id = ?", (id,))
            conn.commit()
            if cur.rowcount == 0:
                return f"Error: webhook '{id}' not found"
        return json.dumps({"id": id, "deleted": True})

    # ------------------------------------------------------------------
    # Feature 6: REST API
    # ------------------------------------------------------------------

    if enable_rest:

        def _build_rest_router() -> Router:
            """Build and return a Starlette Router for the /api/v1 REST endpoints."""

            async def _read_json_body(request: Request):
                """Read and parse the request body with a size cap. Returns (data, None) or (None, JSONResponse)."""
                cl_header = request.headers.get("content-length")
                if cl_header is not None:
                    try:
                        if int(cl_header) > _MAX_REST_BODY:
                            return None, JSONResponse(
                                {"error": "Request body too large (max 1 MiB)"}, status_code=413
                            )
                    except ValueError:
                        pass
                raw = await request.body()
                if len(raw) > _MAX_REST_BODY:
                    return None, JSONResponse(
                        {"error": "Request body too large (max 1 MiB)"}, status_code=413
                    )
                try:
                    return json.loads(raw), None
                except Exception:
                    return None, JSONResponse({"error": "Error: invalid JSON body"}, status_code=400)

            async def _check_auth(request: Request):
                """Returns (actor, None) on success or (None, JSONResponse) on failure."""
                if not tenant_keys:
                    return "system", None
                auth_header = request.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer "):
                    return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
                token = auth_header[7:]
                for tid, key in tenant_keys.items():
                    if hmac.compare_digest(token, key):
                        return tid, None
                return None, JSONResponse({"error": "Unauthorized"}, status_code=401)

            def _error_status(msg: str) -> int:
                if "not found" in msg:
                    return 404
                if "already exists" in msg:
                    return 409
                if "database error" in msg:
                    return 500
                return 400

            def _tool_resp(result: str, created: bool = False) -> JSONResponse:
                if result.startswith("Error:"):
                    return JSONResponse({"error": result}, status_code=_error_status(result))
                try:
                    data = json.loads(result)
                    return JSONResponse(data, status_code=201 if created else 200)
                except json.JSONDecodeError:
                    return JSONResponse({"message": result}, status_code=200)

            async def tasks_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                if request.method == "GET":
                    p = request.query_params
                    project = p.get("project")
                    assignee = p.get("assignee")
                    status = p.get("status")
                    priority = p.get("priority")
                    try:
                        limit = int(p.get("limit", 20))
                        offset = int(p.get("offset", 0))
                    except ValueError:
                        return JSONResponse({"error": "Error: invalid limit or offset"}, status_code=400)
                    limit = max(1, min(limit, _MAX_LIMIT))
                    conditions, params = [], []
                    if project:
                        conditions.append("project = ?")
                        params.append(project)
                    if assignee:
                        conditions.append("assignee = ?")
                        params.append(assignee)
                    if status:
                        conditions.append("status = ?")
                        params.append(status)
                    if priority:
                        conditions.append("priority = ?")
                        params.append(priority)
                    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                    try:
                        rows = conn.execute(
                            f"SELECT id, title, priority, status, assignee FROM tasks"
                            f" {where} ORDER BY {_PRIORITY_CASE}, created_at LIMIT ? OFFSET ?",
                            params + [limit + 1, offset],
                        ).fetchall()
                    except sqlite3.Error:
                        return JSONResponse({"error": "Error: database error"}, status_code=500)
                    has_more = len(rows) > limit
                    return JSONResponse({
                        "tasks": [dict(r) for r in rows[:limit]],
                        "has_more": has_more,
                        "offset": offset,
                    })
                elif request.method == "POST":
                    body, body_err = await _read_json_body(request)
                    if body_err:
                        return body_err
                    tid = body.get("id", "")
                    if not tid:
                        return JSONResponse({"error": "Error: 'id' is required"}, status_code=400)
                    if not body.get("title", ""):
                        return JSONResponse({"error": "Error: 'title' is required"}, status_code=400)
                    err2 = _validate_create_params(
                        tid,
                        body.get("title", ""),
                        body.get("description"),
                        body.get("priority", "medium"),
                        body.get("project", "default"),
                        body.get("assignee"),
                        body.get("due_date"),
                    )
                    if err2:
                        return JSONResponse({"error": err2}, status_code=400)
                    now = _now()
                    tags = body.get("tags")
                    tags_err = _validate_tags(tags)
                    if tags_err:
                        return JSONResponse({"error": tags_err}, status_code=400)
                    tags_json = json.dumps(tags) if tags else None
                    async with _lock:
                        try:
                            conn.execute(
                                "INSERT INTO tasks"
                                " (id, title, description, project, priority, status, assignee, tags, due_date, created_at, updated_at)"
                                " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                                (
                                    tid, body.get("title"), body.get("description"),
                                    body.get("project", "default"), body.get("priority", "medium"),
                                    body.get("assignee"), tags_json, body.get("due_date"), now, now,
                                ),
                            )
                            _log(tid, "created", actor=actor)
                            conn.commit()
                        except sqlite3.IntegrityError:
                            return JSONResponse({"error": f"Error: task '{tid}' already exists"}, status_code=409)
                        except sqlite3.Error:
                            return JSONResponse({"error": "Error: database error"}, status_code=500)
                    return JSONResponse(
                        {"id": tid, "status": "pending", "priority": body.get("priority", "medium"), "project": body.get("project", "default")},
                        status_code=201,
                    )
                return JSONResponse({"error": "Method not allowed"}, status_code=405)

            async def task_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                task_id = request.path_params["id"]
                if request.method == "GET":
                    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                    if row is None:
                        return JSONResponse({"error": f"Error: task '{task_id}' not found"}, status_code=404)
                    task = _row(row)
                    task["depends_on"] = [
                        r[0] for r in conn.execute(
                            "SELECT depends_on FROM task_deps WHERE task_id = ?", (task_id,)
                        ).fetchall()
                    ]
                    task["blocked_by"] = [
                        r[0] for r in conn.execute(
                            "SELECT td.depends_on FROM task_deps td JOIN tasks t ON td.depends_on = t.id"
                            " WHERE td.task_id = ? AND t.status != 'done'",
                            (task_id,),
                        ).fetchall()
                    ]
                    return JSONResponse(task)
                elif request.method == "PATCH":
                    body, body_err = await _read_json_body(request)
                    if body_err:
                        return body_err
                    err2 = _validate_update_params(
                        body.get("title"),
                        body.get("description"),
                        body.get("priority"),
                        body.get("project"),
                        body.get("status"),
                        body.get("assignee"),
                        body.get("due_date"),
                    )
                    if err2:
                        return JSONResponse({"error": err2}, status_code=400)
                    upd: dict[str, object] = {}
                    for f in ("title", "description", "priority", "project", "status", "assignee", "due_date"):
                        if f in body and body[f] is not None:
                            upd[f] = body[f]
                    if "tags" in body and body["tags"] is not None:
                        tags_err = _validate_tags(body["tags"])
                        if tags_err:
                            return JSONResponse({"error": tags_err}, status_code=400)
                        upd["tags"] = json.dumps(body["tags"])
                    if not upd:
                        return JSONResponse({"error": "Error: no fields to update"}, status_code=400)
                    async with _lock:
                        old_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                        if old_row is None:
                            return JSONResponse({"error": f"Error: task '{task_id}' not found"}, status_code=404)
                        old_data = dict(old_row)
                        upd["updated_at"] = _now()
                        set_clause = ", ".join(f"{k} = ?" for k in upd)
                        vals = list(upd.values()) + [task_id]
                        try:
                            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", vals)
                            for field, new_val in upd.items():
                                if field == "updated_at":
                                    continue
                                old_val = old_data.get(field)
                                if old_val != new_val:
                                    _log(task_id, "updated", field=field, old_value=old_val, new_value=new_val, actor=actor)
                            conn.commit()
                        except sqlite3.Error:
                            return JSONResponse({"error": "Error: database error"}, status_code=500)
                    return JSONResponse({"id": task_id, "updated": list(upd.keys())})
                elif request.method == "DELETE":
                    confirm = request.query_params.get("confirm", "").lower() == "true"
                    if not confirm:
                        return JSONResponse({"error": "Error: confirm=true is required to delete a task"}, status_code=400)
                    async with _lock:
                        row = conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
                        if row is None:
                            return JSONResponse({"error": f"Error: task '{task_id}' not found"}, status_code=404)
                        conn.execute(
                            "DELETE FROM task_deps WHERE task_id = ? OR depends_on = ?", (task_id, task_id)
                        )
                        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                        _log(task_id, "deleted", actor=actor)
                        conn.commit()
                    return JSONResponse({"id": task_id, "deleted": True})
                return JSONResponse({"error": "Method not allowed"}, status_code=405)

            async def projects_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                try:
                    rows = conn.execute(
                        "SELECT project, COUNT(*) as total,"
                        " SUM(CASE WHEN status != 'done' THEN 1 ELSE 0 END) as open"
                        " FROM tasks GROUP BY project ORDER BY project"
                    ).fetchall()
                except sqlite3.Error:
                    return JSONResponse({"error": "Error: database error"}, status_code=500)
                return JSONResponse({
                    "projects": [
                        {"project": r["project"], "open": r["open"], "total": r["total"]}
                        for r in rows
                    ]
                })

            async def stats_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
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
                return JSONResponse({
                    "by_status": by_status,
                    "by_priority": by_priority,
                    "oldest_open": oldest["oldest"] if oldest else None,
                })

            return Router(routes=[
                Route("/tasks", endpoint=tasks_endpoint, methods=["GET", "POST"]),
                Route("/tasks/{id:str}", endpoint=task_endpoint, methods=["GET", "PATCH", "DELETE"]),
                Route("/projects", endpoint=projects_endpoint, methods=["GET"]),
                Route("/stats", endpoint=stats_endpoint, methods=["GET"]),
            ])

        mcp._rest_router = _build_rest_router()

    return mcp
