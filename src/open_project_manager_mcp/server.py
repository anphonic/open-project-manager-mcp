"""SQLite-backed project management MCP server."""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}

# Explicit allowlist of columns that update_task may write — guards against
# any future refactor that could introduce attacker-controlled column names
# into the f-string SET clause.
_VALID_UPDATE_COLUMNS = frozenset(
    {"title", "description", "priority", "project", "status", "assignee", "tags", "updated_at"}
)

# Query result caps — prevent DoS via huge LIMIT values.
_MAX_LIMIT = 500

# Input length caps — prevent excessively large payloads from being stored.
_MAX_SHORT_FIELD = 500       # id, title, project, assignee
_MAX_DESCRIPTION = 50_000   # description

_PRIORITY_CASE = (
    "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
    "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"
)

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
    FOREIGN KEY (task_id)   REFERENCES tasks(id),
    FOREIGN KEY (depends_on) REFERENCES tasks(id)
);
"""


def create_server(
    db_path: str,
    transport_security: Optional[TransportSecuritySettings] = None,
) -> FastMCP:
    """Create and return the project manager MCP server backed by SQLite at db_path.

    Args:
        db_path: Path to the SQLite database file.
        transport_security: DNS rebinding / Host-header validation settings passed
            directly to FastMCP. Pass
            ``TransportSecuritySettings(enable_dns_rebinding_protection=False)``
            when binding to a non-localhost interface so that LAN clients with a
            non-localhost Host header are not rejected.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    _lock = asyncio.Lock()
    mcp = FastMCP("open-project-manager-mcp", transport_security=transport_security)

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row(row) -> dict:
        d = dict(row)
        if d.get("tags"):
            d["tags"] = json.loads(d["tags"])
        return d

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
    ) -> str:
        """Create a new task. id is a caller-supplied slug (e.g. 'auth-login-ui')."""
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
        now = _now()
        tags_json = json.dumps(tags) if tags else None
        async with _lock:
            try:
                conn.execute(
                    "INSERT INTO tasks"
                    " (id, title, description, project, priority, status, assignee, tags, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                    (id, title, description, project, priority, assignee, tags_json, now, now),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                return f"Error: task '{id}' already exists"
            except sqlite3.Error:
                return "Error: database error creating task"
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
    ) -> str:
        """Update fields on an existing task. Only provided fields are changed."""
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
            updates["tags"] = json.dumps(tags)
        if not updates:
            return "Error: no fields to update"
        updates["updated_at"] = _now()
        # Safety: assert every key is in the compile-time allowlist before
        # building the f-string. This can never fail with the current code
        # but makes the invariant explicit and safe against future refactors.
        unknown = set(updates) - _VALID_UPDATE_COLUMNS
        if unknown:
            return f"Error: internal error — unknown field(s): {', '.join(sorted(unknown))}"
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        async with _lock:
            try:
                cur = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
                conn.commit()
            except sqlite3.Error:
                return "Error: database error updating task"
            if cur.rowcount == 0:
                return f"Error: task '{task_id}' not found"
        return json.dumps({"id": task_id, "updated": list(updates.keys())})

    @mcp.tool()
    async def complete_task(task_id: str) -> str:
        """Mark a task as done."""
        async with _lock:
            cur = conn.execute(
                "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
                (_now(), task_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return f"Error: task '{task_id}' not found"
        return json.dumps({"id": task_id, "status": "done"})

    @mcp.tool()
    async def delete_task(task_id: str, human_approval: bool = False) -> str:
        """Delete a task and its dependency edges. Requires human_approval=True."""
        if not human_approval:
            return "Error: human_approval=True is required to delete a task"
        async with _lock:
            conn.execute(
                "DELETE FROM task_deps WHERE task_id = ? OR depends_on = ?",
                (task_id, task_id),
            )
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            if cur.rowcount == 0:
                return f"Error: task '{task_id}' not found"
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
                conn.commit()
            except sqlite3.IntegrityError:
                return "Dependency already exists"
        return json.dumps({"task_id": task_id, "depends_on": depends_on_id})

    @mcp.tool()
    async def remove_dependency(task_id: str, depends_on_id: str) -> str:
        """Remove a dependency edge between two tasks."""
        async with _lock:
            cur = conn.execute(
                "DELETE FROM task_deps WHERE task_id = ? AND depends_on = ?",
                (task_id, depends_on_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return "Error: dependency not found"
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

    return mcp
