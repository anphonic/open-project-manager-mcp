"""SQLite-backed project management MCP server."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, Router

VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}
VALID_WEBHOOK_EVENTS = {"task.created", "task.updated", "task.completed", "task.deleted"}
VALID_TEAM_STATUSES = {"online", "offline", "busy", "degraded"}
VALID_NOTIFICATION_TYPES = {"squad.status", "squad.alert", "squad.heartbeat"}
VALID_SUBSCRIPTION_EVENTS = {"server.stats", "server.health", "project.summary"}
_SUB_MIN_INTERVAL = 60       # seconds
_SUB_MAX_INTERVAL = 86400    # seconds

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
    """Validates Bearer API keys; checks env var keys then DB-registered keys."""

    def __init__(self, verify_fn):
        self._verify = verify_fn

    async def verify_token(self, token: str) -> AccessToken | None:
        # Must return None (not raise) for invalid tokens — the TokenVerifier
        # protocol contract requires None on failure.  Raising AuthenticationError
        # propagates through BearerAuthBackend to Starlette's default_on_error,
        # which returns HTTP 400 instead of the correct HTTP 401.
        try:
            tenant_id = await self._verify(token)
            if not tenant_id:
                return None
            return AccessToken(
                token=token,
                client_id=tenant_id,
                scopes=["api"],
            )
        except Exception:
            return None


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

CREATE TABLE IF NOT EXISTS tenant_keys (
    squad      TEXT PRIMARY KEY,
    key        TEXT NOT NULL,
    created_at TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS telemetry_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT    NOT NULL,
    metric_type  TEXT    NOT NULL,
    metric_name  TEXT    NOT NULL,
    bucket_hour  TEXT    NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    sum_ms       INTEGER,
    min_ms       INTEGER,
    max_ms       INTEGER,
    error_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(tenant_id, metric_type, metric_name, bucket_hour)
);
CREATE INDEX IF NOT EXISTS telemetry_tenant_hour_idx ON telemetry_metrics(tenant_id, bucket_hour DESC);
CREATE INDEX IF NOT EXISTS telemetry_type_idx ON telemetry_metrics(metric_type);

CREATE TABLE IF NOT EXISTS telemetry_daily (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT    NOT NULL,
    metric_type  TEXT    NOT NULL,
    metric_name  TEXT    NOT NULL,
    bucket_date  TEXT    NOT NULL,
    total_count  INTEGER NOT NULL DEFAULT 0,
    total_errors INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms REAL,
    p95_latency_ms INTEGER,
    created_at   TEXT    NOT NULL,
    UNIQUE(tenant_id, metric_type, metric_name, bucket_date)
);
CREATE INDEX IF NOT EXISTS telemetry_daily_tenant_idx ON telemetry_daily(tenant_id, bucket_date DESC);

CREATE TABLE IF NOT EXISTS project_permissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project      TEXT    NOT NULL,
    tenant_id    TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    granted_by   TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(project, tenant_id)
);
CREATE INDEX IF NOT EXISTS perm_project_idx ON project_permissions(project);
CREATE INDEX IF NOT EXISTS perm_tenant_idx ON project_permissions(tenant_id);
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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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
    _start_time: float = time.time()
    _event_bus_clients: list[asyncio.Queue] = []
    _bg_health_task: Optional[asyncio.Task] = None
    _bg_sub_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Async database helpers — wrap sqlite3 calls to avoid blocking event loop
    # ------------------------------------------------------------------

    async def _db_execute(query: str, params: tuple = ()) -> list:
        """Execute a SELECT query and return all rows (runs in thread pool)."""
        return await asyncio.to_thread(lambda: conn.execute(query, params).fetchall())

    async def _db_execute_one(query: str, params: tuple = ()) -> dict | None:
        """Execute a SELECT query and return one row or None (runs in thread pool)."""
        return await asyncio.to_thread(lambda: conn.execute(query, params).fetchone())

    async def _locked_write(write_fn):
        """Acquire _lock with 30s timeout, run write_fn in thread pool, release. Returns error string on timeout."""
        try:
            await asyncio.wait_for(_lock.acquire(), timeout=30.0)
        except asyncio.TimeoutError:
            return "Error: write operation timed out waiting for lock — server may need restart"
        try:
            return await asyncio.to_thread(write_fn)
        finally:
            _lock.release()

    async def _verify_bearer(token: str) -> str | None:
        """Return tenant_id if token is valid, else None. Env var keys take precedence."""
        # 1. Env var keys — checked first
        if tenant_keys:
            for tid, key in tenant_keys.items():
                if hmac.compare_digest(token, key):
                    return tid
        # 2. DB-registered keys — re-queried on every call (no restart needed)
        # Note: DB keys grant REST API access only; MCP access requires OPM_TENANT_KEYS.
        try:
            rows = await _db_execute("SELECT squad, key FROM tenant_keys", ())
        except sqlite3.Error:
            return None
        for row in rows:
            if hmac.compare_digest(token, row["key"]):
                return row["squad"]
        return None

    auth_settings = None
    token_verifier = None
    if tenant_keys:
        token_verifier = ApiKeyVerifier(_verify_bearer)
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

    async def _record_metric(tenant_id: str, metric_type: str, metric_name: str, 
                             latency_ms: int = None, is_error: bool = False):
        """Record a telemetry metric. Fire-and-forget — doesn't block caller."""
        bucket_hour = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        ).isoformat().replace('+00:00', 'Z')
        now = _now()
        
        def _do_write():
            try:
                conn.execute("""
                    INSERT INTO telemetry_metrics 
                        (tenant_id, metric_type, metric_name, bucket_hour, 
                         count, sum_ms, min_ms, max_ms, error_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, metric_type, metric_name, bucket_hour) DO UPDATE SET
                        count = count + 1,
                        sum_ms = CASE WHEN excluded.sum_ms IS NOT NULL 
                                 THEN COALESCE(sum_ms, 0) + excluded.sum_ms ELSE sum_ms END,
                        min_ms = CASE WHEN excluded.min_ms IS NOT NULL 
                                 THEN MIN(COALESCE(min_ms, excluded.min_ms), excluded.min_ms) ELSE min_ms END,
                        max_ms = CASE WHEN excluded.max_ms IS NOT NULL 
                                 THEN MAX(COALESCE(max_ms, 0), excluded.max_ms) ELSE max_ms END,
                        error_count = error_count + excluded.error_count,
                        updated_at = excluded.updated_at
                """, (tenant_id, metric_type, metric_name, bucket_hour, 
                      latency_ms, latency_ms, latency_ms, 
                      1 if is_error else 0, now, now))
                conn.commit()
            except Exception:
                pass  # Fire-and-forget — silent failure
        
        asyncio.create_task(asyncio.to_thread(_do_write))

    VALID_ROLES = {"owner", "contributor", "reader"}
    _ROLE_HIERARCHY = {"owner": 3, "contributor": 2, "reader": 1}

    async def _check_project_access(project: str, required_role: str) -> str | None:
        """
        Check if current tenant has required_role on project.
        Returns None if allowed, error string if denied.
        """
        # Explicit check for "1" to prevent empty string bypass
        enforce = os.environ.get("OPM_ENFORCE_PERMISSIONS", "")
        if enforce != "1":
            return None  # Enforcement disabled by default
        
        tenant_id = _get_actor()
        if tenant_id == "system":
            return None  # Unauthenticated mode — allow all
        
        row = await _db_execute_one(
            "SELECT role FROM project_permissions WHERE project = ? AND tenant_id = ?",
            (project, tenant_id)
        )
        if not row:
            return f"Error: access denied to project '{project}'"
        
        role = row["role"]
        required_level = _ROLE_HIERARCHY.get(required_role, 0)
        actual_level = _ROLE_HIERARCHY.get(role, 0)
        
        if actual_level < required_level:
            return f"Error: insufficient permissions (have '{role}', need '{required_role}')"
        return None

    # ------------------------------------------------------------------
    # SSE event bus helpers
    # ------------------------------------------------------------------

    def _publish_event(event_type: str, data: dict) -> None:
        """Fanout an event to all connected SSE clients. Silently drops if a client queue is full."""
        payload = {"event": event_type, "data": data, "timestamp": _now()}
        for q in list(_event_bus_clients):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

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

    async def _subscriptions_loop() -> None:
        """Check every 30s for due interval subscriptions and fire them."""
        while True:
            await asyncio.sleep(30)
            try:
                rows = await _db_execute(
                    "SELECT id, subscriber, url, event_type, project, interval_sec"
                    " FROM event_subscriptions"
                    " WHERE enabled = 1 AND interval_sec IS NOT NULL"
                    " AND (last_fired_at IS NULL"
                    "      OR datetime(last_fired_at, '+' || interval_sec || ' seconds')"
                    "         <= datetime('now'))",
                    (),
                )
            except Exception:
                continue
            for row in rows:
                event_type = row["event_type"]
                try:
                    if event_type == "server.stats":
                        by_status_rows = await _db_execute(
                            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status", ()
                        )
                        by_status = {r["status"]: r["cnt"] for r in by_status_rows}
                        payload = {
                            "queue_depth": sum(v for k, v in by_status.items() if k != "done"),
                            "by_status": by_status,
                            "uptime_sec": int(time.time() - _start_time),
                        }
                    elif event_type == "project.summary":
                        project = row["project"] or ""
                        by_status_rows = await _db_execute(
                            "SELECT status, COUNT(*) as cnt FROM tasks WHERE project = ? GROUP BY status",
                            (project,),
                        )
                        by_status = {r["status"]: r["cnt"] for r in by_status_rows}
                        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        overdue_row = await _db_execute_one(
                            "SELECT COUNT(*) as cnt FROM tasks"
                            " WHERE project = ? AND due_date IS NOT NULL AND due_date < ? AND status != 'done'",
                            (project, today),
                        )
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

    async def _project_summary(project: str) -> str:
        """Shared logic for project summary — used by MCP tool and REST endpoint."""
        if not project or len(project) > _MAX_SHORT_FIELD:
            return f"Error: 'project' is required and must be under {_MAX_SHORT_FIELD} characters"
        try:
            by_status_rows = await _db_execute(
                "SELECT status, COUNT(*) as cnt FROM tasks WHERE project = ? GROUP BY status",
                (project,),
            )
            by_status = {r["status"]: r["cnt"] for r in by_status_rows}
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            overdue_row = await _db_execute_one(
                "SELECT COUNT(*) as cnt FROM tasks"
                " WHERE project = ? AND due_date IS NOT NULL AND due_date < ? AND status != 'done'",
                (project, today),
            )
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
                async with httpx.AsyncClient(timeout=5.0, verify=True) as client:
                    await client.post(row["url"], content=payload_bytes, headers=headers)
            except Exception:
                pass  # fire-and-forget; no retries in v0.2.0

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
                pass
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
        start_time = time.time()
        actor = _get_actor()
        
        # Permissions check
        perm_err = await _check_project_access(project, "contributor")
        if perm_err:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "create_task", latency_ms, is_error=True))
            return perm_err
        
        err = _validate_create_params(id, title, description, priority, project, assignee, due_date)
        if err:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "create_task", latency_ms, is_error=True))
            return err
        tags_err = _validate_tags(tags)
        if tags_err:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "create_task", latency_ms, is_error=True))
            return tags_err
        
        now = _now()
        tags_json = json.dumps(tags) if tags else None
        
        def _do_write():
            try:
                conn.execute(
                    "INSERT INTO tasks"
                    " (id, title, description, project, priority, status, assignee, tags, due_date, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                    (id, title, description, project, priority, assignee, tags_json, due_date, now, now),
                )
                _log(id, "created", actor=actor)
                conn.commit()
                return None
            except sqlite3.IntegrityError:
                return f"Error: task '{id}' already exists"
            except sqlite3.Error:
                return "Error: database error creating task"
        
        result = await _locked_write(_do_write)
        latency_ms = int((time.time() - start_time) * 1000)
        is_error = result is not None
        asyncio.create_task(_record_metric(actor, "tool_call", "create_task", latency_ms, is_error))
        
        if result:
            return result
        asyncio.create_task(
            _fire_webhooks(
                "task.created",
                id,
                project,
                {"id": id, "title": title, "priority": priority, "status": "pending", "project": project},
            )
        )
        _publish_event("task.created", {
            "id": id, "title": title, "priority": priority,
            "status": "pending", "project": project,
        })
        _publish_queue_stats()
        return json.dumps({"id": id, "title": title, "status": "pending", "priority": priority, "project": project})

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
        start_time = time.time()
        actor = _get_actor()
        
        # Get task's project for permissions check
        row = await _db_execute_one("SELECT project FROM tasks WHERE id = ?", (task_id,))
        if not row:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "update_task", latency_ms, is_error=True))
            return f"Error: task '{task_id}' not found"
        
        perm_err = await _check_project_access(row["project"], "contributor")
        if perm_err:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "update_task", latency_ms, is_error=True))
            return perm_err
        
        err = _validate_update_params(title, description, priority, project, status, assignee, due_date)
        if err:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "update_task", latency_ms, is_error=True))
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
                latency_ms = int((time.time() - start_time) * 1000)
                asyncio.create_task(_record_metric(actor, "tool_call", "update_task", latency_ms, is_error=True))
                return tags_err
            updates["tags"] = json.dumps(tags)
        if due_date is not None:
            updates["due_date"] = due_date
        if not updates:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "update_task", latency_ms, is_error=True))
            return "Error: no fields to update"
        unknown = set(updates) - _VALID_UPDATE_COLUMNS
        if unknown:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "update_task", latency_ms, is_error=True))
            return f"Error: internal error — unknown field(s): {', '.join(sorted(unknown))}"
        
        def _do_write():
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
                return None
            except sqlite3.Error:
                return "Error: database error updating task"
        
        result = await _locked_write(_do_write)
        latency_ms = int((time.time() - start_time) * 1000)
        is_error = result is not None
        asyncio.create_task(_record_metric(actor, "tool_call", "update_task", latency_ms, is_error))
        
        if result:
            return result
        
        old_data = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        old_data = dict(old_data) if old_data else {}
        task_project = updates.get("project") or old_data.get("project", "default")
        asyncio.create_task(
            _fire_webhooks("task.updated", task_id, task_project, {"id": task_id, "updated": list(updates.keys())})
        )
        _publish_event("task.updated", {
            "id": task_id, "updated": list(updates.keys()), "project": task_project,
        })
        _publish_queue_stats()
        return json.dumps({"id": task_id, "updated": list(updates.keys())})

    @mcp.tool()
    async def complete_task(task_id: str) -> str:
        """Mark a task as done."""
        actor = _get_actor()
        
        # Get task's project for permissions check
        row = await _db_execute_one("SELECT project FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return f"Error: task '{task_id}' not found"
        
        perm_err = await _check_project_access(row["project"], "contributor")
        if perm_err:
            return perm_err
        
        def _do_write():
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
            return task_project
        
        result = await _locked_write(_do_write)
        if isinstance(result, str) and result.startswith("Error"):
            return result
        task_project = result
        asyncio.create_task(
            _fire_webhooks("task.completed", task_id, task_project, {"id": task_id, "status": "done"})
        )
        _publish_event("task.completed", {"id": task_id, "status": "done", "project": task_project})
        _publish_queue_stats()
        return json.dumps({"id": task_id, "status": "done"})

    @mcp.tool()
    async def delete_task(task_id: str, human_approval: bool = False) -> str:
        """Delete a task and its dependency edges. Requires human_approval=True."""
        if not human_approval:
            return "Error: human_approval=True is required to delete a task"
        actor = _get_actor()
        
        # Get task's project for permissions check
        row = await _db_execute_one("SELECT project FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return f"Error: task '{task_id}' not found"
        
        perm_err = await _check_project_access(row["project"], "owner")
        if perm_err:
            return perm_err
        
        def _do_write():
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
            return task_project
        
        result = await _locked_write(_do_write)
        if isinstance(result, str) and result.startswith("Error"):
            return result
        task_project = result
        asyncio.create_task(
            _fire_webhooks("task.deleted", task_id, task_project, {"id": task_id})
        )
        _publish_event("task.deleted", {"id": task_id, "project": task_project})
        _publish_queue_stats()
        return json.dumps({"id": task_id, "deleted": True})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_task(task_id: str) -> str:
        """Get a single task by ID, including its dependency info."""
        start_time = time.time()
        actor = _get_actor()
        
        try:
            row = await _db_execute_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
            if row is None:
                latency_ms = int((time.time() - start_time) * 1000)
                asyncio.create_task(_record_metric(actor, "tool_call", "get_task", latency_ms, is_error=True))
                return f"Error: task '{task_id}' not found"
            
            task = _row(row)
            perm_err = await _check_project_access(task["project"], "reader")
            if perm_err:
                latency_ms = int((time.time() - start_time) * 1000)
                asyncio.create_task(_record_metric(actor, "tool_call", "get_task", latency_ms, is_error=True))
                return perm_err
            
            depends_on_rows = await _db_execute(
                "SELECT depends_on FROM task_deps WHERE task_id = ?", (task_id,)
            )
            task["depends_on"] = [r[0] for r in depends_on_rows]
            blocked_by_rows = await _db_execute(
                "SELECT td.depends_on FROM task_deps td"
                " JOIN tasks t ON td.depends_on = t.id"
                " WHERE td.task_id = ? AND t.status != 'done'",
                (task_id,),
            )
            task["blocked_by"] = [r[0] for r in blocked_by_rows]
            
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "get_task", latency_ms, is_error=False))
            return json.dumps(task)
        except sqlite3.Error:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "get_task", latency_ms, is_error=True))
            return "Error: database error reading task"

    @mcp.tool()
    async def list_tasks(
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
        start_time = time.time()
        actor = _get_actor()
        
        if project:
            perm_err = await _check_project_access(project, "reader")
            if perm_err:
                latency_ms = int((time.time() - start_time) * 1000)
                asyncio.create_task(_record_metric(actor, "tool_call", "list_tasks", latency_ms, is_error=True))
                return perm_err
        
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
            rows = await _db_execute(
                f"SELECT id, title, priority, status, assignee FROM tasks"
                f" {where} ORDER BY {_PRIORITY_CASE}, created_at LIMIT ? OFFSET ?",
                tuple(params + [limit + 1, offset]),
            )
            has_more = len(rows) > limit
            
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "list_tasks", latency_ms, is_error=False))
            
            return json.dumps({
                "tasks": [dict(r) for r in rows[:limit]],
                "has_more": has_more,
                "offset": offset,
            })
        except sqlite3.Error:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "list_tasks", latency_ms, is_error=True))
            return "Error: database error listing tasks"

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    @mcp.tool()
    async def add_dependency(task_id: str, depends_on_id: str) -> str:
        """Mark that task_id cannot start until depends_on_id is done."""
        if task_id == depends_on_id:
            return "Error: a task cannot depend on itself"
        actor = _get_actor()
        
        def _do_write():
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
                return None
            except sqlite3.IntegrityError:
                return "Dependency already exists"
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({"task_id": task_id, "depends_on": depends_on_id})

    @mcp.tool()
    async def remove_dependency(task_id: str, depends_on_id: str) -> str:
        """Remove a dependency edge between two tasks."""
        actor = _get_actor()
        
        def _do_write():
            cur = conn.execute(
                "DELETE FROM task_deps WHERE task_id = ? AND depends_on = ?",
                (task_id, depends_on_id),
            )
            if cur.rowcount == 0:
                return "Error: dependency not found"
            _log(task_id, "dep_removed", field="depends_on", old_value=depends_on_id, actor=actor)
            conn.commit()
            return None
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({"task_id": task_id, "depends_on": depends_on_id, "removed": True})

    @mcp.tool()
    async def list_ready_tasks(
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
            rows = await _db_execute(
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
                tuple(params + [limit]),
            )
        except sqlite3.Error:
            return "Error: database error listing ready tasks"
        return json.dumps({"tasks": [dict(r) for r in rows], "count": len(rows)})

    # ------------------------------------------------------------------
    # Projects & Stats
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_projects() -> str:
        """List all projects with open and total task counts."""
        try:
            rows = await _db_execute(
                "SELECT project,"
                " COUNT(*) as total,"
                " SUM(CASE WHEN status != 'done' THEN 1 ELSE 0 END) as open"
                " FROM tasks GROUP BY project ORDER BY project",
                (),
            )
        except sqlite3.Error:
            return "Error: database error listing projects"
        return json.dumps({
            "projects": [
                {"project": r["project"], "open": r["open"], "total": r["total"]}
                for r in rows
            ]
        })

    @mcp.tool()
    async def get_stats() -> str:
        """Task counts by status and priority, plus the age of the oldest open item."""
        try:
            by_status_rows = await _db_execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status", ()
            )
            by_status = {r["status"]: r["cnt"] for r in by_status_rows}
            by_priority_rows = await _db_execute(
                "SELECT priority, COUNT(*) as cnt FROM tasks WHERE status != 'done' GROUP BY priority", ()
            )
            by_priority = {r["priority"]: r["cnt"] for r in by_priority_rows}
            oldest = await _db_execute_one(
                "SELECT MIN(created_at) as oldest FROM tasks WHERE status != 'done'", ()
            )
        except sqlite3.Error:
            return "Error: database error reading stats"
        return json.dumps({
            "by_status": by_status,
            "by_priority": by_priority,
            "oldest_open": oldest["oldest"] if oldest else None,
        })

    @mcp.tool()
    async def get_server_stats() -> str:
        """Get server statistics: task counts, uptime, and active SSE connections."""
        try:
            by_status_rows = await _db_execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status", ()
            )
            by_status = {r["status"]: r["cnt"] for r in by_status_rows}
            by_project: dict[str, dict] = {}
            project_rows = await _db_execute(
                "SELECT project, status, COUNT(*) as cnt FROM tasks GROUP BY project, status", ()
            )
            for r in project_rows:
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

    @mcp.tool()
    async def get_project_summary(project: str) -> str:
        """Get a task summary for a specific project, including overdue count."""
        return await _project_summary(project)

    # ------------------------------------------------------------------
    # Feature 8: Team status & events
    # ------------------------------------------------------------------

    @mcp.tool()
    async def set_team_status(squad: str, status: str, message: Optional[str] = None) -> str:
        """Set the online/offline/busy/degraded status for a squad."""
        if not squad or len(squad) > _MAX_SHORT_FIELD:
            return f"Error: 'squad' is required and must be under {_MAX_SHORT_FIELD} characters"
        if status not in VALID_TEAM_STATUSES:
            return f"Error: invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_TEAM_STATUSES))}"
        if message and len(message) > _MAX_SHORT_FIELD:
            return f"Error: 'message' exceeds maximum length of {_MAX_SHORT_FIELD} characters"
        now = _now()
        
        def _do_write():
            try:
                conn.execute(
                    "INSERT INTO team_status (squad, status, message, updated_at)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(squad) DO UPDATE SET status=excluded.status,"
                    " message=excluded.message, updated_at=excluded.updated_at",
                    (squad, status, message, now),
                )
                conn.commit()
                return None
            except sqlite3.Error:
                return "Error: database error setting team status"
        
        result = await _locked_write(_do_write)
        if result:
            return result
        _publish_event("squad.status", {"squad": squad, "status": status, "message": message})
        return json.dumps({"squad": squad, "status": status, "message": message, "updated_at": now})

    @mcp.tool()
    async def get_team_status(squad: Optional[str] = None) -> str:
        """Get current status for all squads or a specific squad."""
        try:
            if squad:
                row = await _db_execute_one(
                    "SELECT squad, status, message, updated_at FROM team_status WHERE squad = ?",
                    (squad,),
                )
                if row is None:
                    return f"Error: squad '{squad}' not found"
                return json.dumps(dict(row))
            rows = await _db_execute(
                "SELECT squad, status, message, updated_at FROM team_status ORDER BY squad", ()
            )
        except sqlite3.Error:
            return "Error: database error reading team status"
        return json.dumps({"squads": [dict(r) for r in rows]})

    @mcp.tool()
    async def post_team_event(squad: str, event_type: str, data: Optional[str] = None) -> str:
        """Post a notification event for a squad (alert, heartbeat, etc.)."""
        if not squad or len(squad) > _MAX_SHORT_FIELD:
            return f"Error: 'squad' is required and must be under {_MAX_SHORT_FIELD} characters"
        if event_type not in VALID_NOTIFICATION_TYPES:
            return f"Error: invalid event_type '{event_type}'. Must be one of: {', '.join(sorted(VALID_NOTIFICATION_TYPES))}"
        if data and len(data) > _MAX_DESCRIPTION:
            return f"Error: 'data' exceeds maximum length of {_MAX_DESCRIPTION} characters"
        now = _now()
        
        def _do_write():
            try:
                conn.execute(
                    "INSERT INTO team_events (squad, event_type, data, created_at) VALUES (?, ?, ?, ?)",
                    (squad, event_type, data, now),
                )
                conn.commit()
                return None
            except sqlite3.Error:
                return "Error: database error posting team event"
        
        result = await _locked_write(_do_write)
        if result:
            return result
        _publish_event(event_type, {"squad": squad, "data": data})
        return json.dumps({"squad": squad, "event_type": event_type, "created_at": now})

    @mcp.tool()
    async def get_team_events(
        squad: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> str:
        """Get recent team events, optionally filtered by squad or event_type."""
        limit = max(1, min(limit, 200))
        conditions: list[str] = []
        params: list[object] = []
        if squad:
            conditions.append("squad = ?")
            params.append(squad)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        try:
            rows = await _db_execute(
                f"SELECT id, squad, event_type, data, created_at FROM team_events"
                f" {where} ORDER BY created_at DESC LIMIT ?",
                tuple(params + [limit]),
            )
        except sqlite3.Error:
            return "Error: database error reading team events"
        return json.dumps({"events": [dict(r) for r in rows], "count": len(rows)})

    # ------------------------------------------------------------------
    # Feature 9: Event subscriptions
    # ------------------------------------------------------------------

    @mcp.tool()
    async def subscribe_events(
        id: str,
        subscriber: str,
        url: str,
        event_type: str,
        project: Optional[str] = None,
        interval_sec: Optional[int] = None,
    ) -> str:
        """Subscribe to periodic server/project events delivered to a HTTPS URL."""
        if len(id) > _MAX_SHORT_FIELD:
            return f"Error: 'id' exceeds maximum length of {_MAX_SHORT_FIELD} characters"
        if len(subscriber) > _MAX_SHORT_FIELD:
            return f"Error: 'subscriber' exceeds maximum length of {_MAX_SHORT_FIELD} characters"
        ssrf_err = await _check_ssrf(url)
        if ssrf_err:
            return ssrf_err
        if event_type not in VALID_SUBSCRIPTION_EVENTS:
            return f"Error: invalid event_type '{event_type}'. Must be one of: {', '.join(sorted(VALID_SUBSCRIPTION_EVENTS))}"
        if interval_sec is not None:
            if interval_sec < _SUB_MIN_INTERVAL or interval_sec > _SUB_MAX_INTERVAL:
                return f"Error: interval_sec must be between {_SUB_MIN_INTERVAL} and {_SUB_MAX_INTERVAL}"
        
        def _do_write():
            try:
                conn.execute(
                    "INSERT INTO event_subscriptions"
                    " (id, subscriber, url, event_type, project, interval_sec, enabled, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                    (id, subscriber, url, event_type, project, interval_sec, _now()),
                )
                conn.commit()
                return None
            except sqlite3.IntegrityError:
                return f"Error: subscription '{id}' already exists"
            except sqlite3.Error:
                return "Error: database error creating subscription"
        
        result = await _locked_write(_do_write)
        if result:
            return result
        _ensure_bg_sub_task()
        return json.dumps({
            "id": id, "subscriber": subscriber, "event_type": event_type,
            "project": project, "interval_sec": interval_sec,
        })

    @mcp.tool()
    async def list_subscriptions(
        subscriber: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> str:
        """List event subscriptions, optionally filtered by subscriber or event_type."""
        conditions: list[str] = []
        params: list[object] = []
        if subscriber:
            conditions.append("subscriber = ?")
            params.append(subscriber)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        try:
            rows = await _db_execute(
                f"SELECT id, subscriber, url, event_type, project, interval_sec,"
                f" enabled, last_fired_at, created_at"
                f" FROM event_subscriptions {where} ORDER BY created_at",
                tuple(params),
            )
        except sqlite3.Error:
            return "Error: database error listing subscriptions"
        return json.dumps({"subscriptions": [dict(r) for r in rows]})

    @mcp.tool()
    async def unsubscribe_events(id: str, human_approval: bool = False) -> str:
        """Delete an event subscription. Requires human_approval=True."""
        if not human_approval:
            return "Error: human_approval=True is required to delete a subscription"
        
        def _do_write():
            cur = conn.execute("DELETE FROM event_subscriptions WHERE id = ?", (id,))
            conn.commit()
            if cur.rowcount == 0:
                return f"Error: subscription '{id}' not found"
            return None
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({"id": id, "deleted": True})

    # ------------------------------------------------------------------
    # Feature 1: Due dates
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_overdue_tasks(
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
            rows = await _db_execute(
                f"SELECT id, title, priority, status, due_date FROM tasks"
                f" {where} ORDER BY {priority_case}, due_date ASC LIMIT ?",
                tuple(params + [limit]),
            )
        except sqlite3.Error:
            return "Error: database error listing overdue tasks"
        return json.dumps({"tasks": [dict(r) for r in rows], "count": len(rows)})

    @mcp.tool()
    async def list_due_soon_tasks(
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
            rows = await _db_execute(
                f"SELECT id, title, priority, status, due_date FROM tasks"
                f" {where} ORDER BY {priority_case}, due_date ASC LIMIT ?",
                tuple(params + [limit]),
            )
        except sqlite3.Error:
            return "Error: database error listing due-soon tasks"
        return json.dumps({"tasks": [dict(r) for r in rows], "count": len(rows)})

    # ------------------------------------------------------------------
    # Feature 2: Full-text search
    # ------------------------------------------------------------------

    @mcp.tool()
    async def search_tasks(
        query: str,
        project: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """Full-text search across task title, description, and tags. Ranked by relevance."""
        start_time = time.time()
        actor = _get_actor()
        
        if project:
            perm_err = await _check_project_access(project, "reader")
            if perm_err:
                latency_ms = int((time.time() - start_time) * 1000)
                asyncio.create_task(_record_metric(actor, "tool_call", "search_tasks", latency_ms, is_error=True))
                return perm_err
        
        if not _fts_available:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "search_tasks", latency_ms, is_error=True))
            return "Error: full-text search is not available (FTS5 not compiled into SQLite)"
        if len(query) > _MAX_SHORT_FIELD:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "search_tasks", latency_ms, is_error=True))
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
            rows = await _db_execute(
                f"SELECT t.id, t.title, t.priority, t.status, t.assignee"
                f" FROM tasks_fts"
                f" JOIN tasks t ON tasks_fts.rowid = t.rowid"
                f" WHERE tasks_fts MATCH ?"
                f"{extra_where}"
                f" ORDER BY rank LIMIT ?",
                tuple(params + [limit + 1]),
            )
            has_more = len(rows) > limit
            
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "search_tasks", latency_ms, is_error=False))
            
            return json.dumps({
                "tasks": [dict(r) for r in rows[:limit]],
                "has_more": has_more,
            })
        except sqlite3.Error:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(_record_metric(actor, "tool_call", "search_tasks", latency_ms, is_error=True))
            return "Error: search failed — invalid query syntax"

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
        
        def _do_write():
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
            return None
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({"created": created, "errors": errors})

    @mcp.tool()
    async def update_tasks(updates: list[dict]) -> str:
        """Bulk-update up to 50 tasks in a single transaction. Per-item errors collected."""
        if len(updates) > _BULK_MAX:
            return f"Error: too many updates (max {_BULK_MAX}, got {len(updates)})"
        actor = _get_actor()
        updated: list[str] = []
        errors: list[dict] = []
        
        def _do_write():
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
            return None
        
        result = await _locked_write(_do_write)
        if result:
            return result
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
        
        def _do_write():
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
            return None
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({"completed": completed, "not_found": not_found})

    # ------------------------------------------------------------------
    # Feature 4: Activity log
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_task_activity(task_id: str, limit: int = 50) -> str:
        """Get the activity history for a task, newest first."""
        limit = max(1, min(limit, 200))
        # Activity log is orphan-safe — query it directly even if task was deleted.
        try:
            rows = await _db_execute(
                "SELECT id, action, field, old_value, new_value, actor, created_at"
                " FROM activity_log WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            )
        except sqlite3.Error:
            return "Error: database error reading activity log"
        return json.dumps({
            "activity": [dict(r) for r in rows],
            "count": len(rows),
        })

    @mcp.tool()
    async def get_activity_log(project: Optional[str] = None, limit: int = 50) -> str:
        """Get recent activity across all tasks (or filtered by project)."""
        limit = max(1, min(limit, 200))
        try:
            if project:
                rows = await _db_execute(
                    "SELECT al.id, al.task_id, al.action, al.field, al.old_value,"
                    " al.new_value, al.actor, al.created_at"
                    " FROM activity_log al"
                    " JOIN tasks t ON al.task_id = t.id"
                    " WHERE t.project = ?"
                    " ORDER BY al.created_at DESC LIMIT ?",
                    (project, limit),
                )
            else:
                rows = await _db_execute(
                    "SELECT id, task_id, action, field, old_value, new_value, actor, created_at"
                    " FROM activity_log ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
        except sqlite3.Error:
            return "Error: database error reading activity log"
        return json.dumps({"activity": [dict(r) for r in rows], "count": len(rows)})

    # ------------------------------------------------------------------
    # Feature 5: Export / Import
    # ------------------------------------------------------------------

    @mcp.tool()
    async def export_all_tasks(project: Optional[str] = None) -> str:
        """Export tasks (and their dependencies) to a portable JSON string."""
        try:
            if project:
                rows = await _db_execute(
                    "SELECT * FROM tasks WHERE project = ? ORDER BY created_at", (project,)
                )
            else:
                rows = await _db_execute("SELECT * FROM tasks ORDER BY created_at", ())
            task_ids = {r["id"] for r in rows}
            tasks_list = [_row(r) for r in rows]
            if project and task_ids:
                placeholders = ",".join("?" * len(task_ids))
                dep_rows = await _db_execute(
                    f"SELECT task_id, depends_on FROM task_deps"
                    f" WHERE task_id IN ({placeholders}) AND depends_on IN ({placeholders})",
                    tuple(list(task_ids) + list(task_ids)),
                )
            elif not project:
                dep_rows = await _db_execute("SELECT task_id, depends_on FROM task_deps", ())
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
        
        def _do_write():
            nonlocal imported, skipped
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
            return None
        
        result = await _locked_write(_do_write)
        if result:
            return result
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
        
        def _do_write():
            try:
                conn.execute(
                    "INSERT INTO webhooks (id, url, project, events, secret, enabled, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (id, url, project, json.dumps(events), secret, _now()),
                )
                conn.commit()
                return None
            except sqlite3.IntegrityError:
                return f"Error: webhook '{id}' already exists"
            except sqlite3.Error:
                return "Error: database error registering webhook"
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({"id": id, "url": url, "events": events, "project": project})

    @mcp.tool()
    async def list_webhooks(project: Optional[str] = None) -> str:
        """List registered webhooks. Secrets are never returned."""
        try:
            if project:
                rows = await _db_execute(
                    "SELECT id, url, project, events, enabled FROM webhooks WHERE project = ? ORDER BY created_at",
                    (project,),
                )
            else:
                rows = await _db_execute(
                    "SELECT id, url, project, events, enabled FROM webhooks ORDER BY created_at", ()
                )
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
        
        def _do_write():
            cur = conn.execute("DELETE FROM webhooks WHERE id = ?", (id,))
            conn.commit()
            if cur.rowcount == 0:
                return f"Error: webhook '{id}' not found"
            return None
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({"id": id, "deleted": True})

    # ------------------------------------------------------------------
    # Feature 7: Telemetry Tools
    # ------------------------------------------------------------------

    _MAX_TELEMETRY_HOURS = 720  # 30 days max lookback to prevent DoS

    @mcp.tool()
    async def get_telemetry_summary(hours: int = 24) -> str:
        """Get aggregated telemetry for calling tenant over last N hours (max 720)."""
        hours = max(1, min(hours, _MAX_TELEMETRY_HOURS))
        actor = _get_actor()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            rows = await _db_execute("""
                SELECT metric_type, metric_name, 
                       SUM(count) as total_calls,
                       SUM(error_count) as total_errors,
                       ROUND(SUM(sum_ms) * 1.0 / SUM(count), 2) as avg_latency_ms
                FROM telemetry_metrics
                WHERE tenant_id = ? AND bucket_hour >= ?
                GROUP BY metric_type, metric_name
                ORDER BY total_calls DESC
            """, (actor, cutoff))
            return json.dumps({"hours": hours, "metrics": [dict(r) for r in rows]})
        except sqlite3.Error:
            return "Error: database error querying telemetry"

    @mcp.tool()
    async def get_telemetry_by_tool(tool_name: str, hours: int = 24) -> str:
        """Get detailed metrics for a specific tool (max 720 hours)."""
        hours = max(1, min(hours, _MAX_TELEMETRY_HOURS))
        actor = _get_actor()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            rows = await _db_execute("""
                SELECT bucket_hour, count, error_count, 
                       ROUND(sum_ms * 1.0 / count, 2) as avg_latency_ms,
                       min_ms, max_ms
                FROM telemetry_metrics
                WHERE tenant_id = ? AND metric_name = ? AND bucket_hour >= ?
                ORDER BY bucket_hour DESC
            """, (actor, tool_name, cutoff))
            return json.dumps({"tool": tool_name, "hours": hours, "buckets": [dict(r) for r in rows]})
        except sqlite3.Error:
            return "Error: database error querying telemetry"

    @mcp.tool()
    async def list_top_tools(limit: int = 10, hours: int = 24) -> str:
        """List most-called tools for calling tenant (max 720 hours, 100 limit)."""
        hours = max(1, min(hours, _MAX_TELEMETRY_HOURS))
        actor = _get_actor()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            rows = await _db_execute("""
                SELECT metric_name, SUM(count) as total_calls
                FROM telemetry_metrics
                WHERE tenant_id = ? AND metric_type = 'tool_call' AND bucket_hour >= ?
                GROUP BY metric_name
                ORDER BY total_calls DESC
                LIMIT ?
            """, (actor, cutoff, min(limit, 100)))
            return json.dumps({"hours": hours, "top_tools": [dict(r) for r in rows]})
        except sqlite3.Error:
            return "Error: database error querying telemetry"

    @mcp.tool()
    async def get_error_summary(hours: int = 24) -> str:
        """Get error counts by tool for calling tenant (max 720 hours)."""
        hours = max(1, min(hours, _MAX_TELEMETRY_HOURS))
        actor = _get_actor()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            rows = await _db_execute("""
                SELECT metric_name, SUM(error_count) as total_errors, SUM(count) as total_calls
                FROM telemetry_metrics
                WHERE tenant_id = ? AND bucket_hour >= ? AND error_count > 0
                GROUP BY metric_name
                ORDER BY total_errors DESC
            """, (actor, cutoff))
            return json.dumps({"hours": hours, "errors": [dict(r) for r in rows]})
        except sqlite3.Error:
            return "Error: database error querying telemetry"

    # ------------------------------------------------------------------
    # Feature 8: Permissions Tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def grant_project_access(
        project: str, 
        target_tenant_id: str, 
        role: str,
        human_approval: bool = False
    ) -> str:
        """Grant a tenant access to a project. Requires owner role."""
        if not human_approval:
            return "Error: human_approval=True required for grant_project_access"
        if role not in VALID_ROLES:
            return f"Error: invalid role '{role}' (must be owner/contributor/reader)"
        
        perm_err = await _check_project_access(project, "owner")
        if perm_err:
            return perm_err
        
        actor = _get_actor()
        now = _now()
        
        def _do_write():
            try:
                conn.execute("""
                    INSERT INTO project_permissions (project, tenant_id, role, granted_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project, tenant_id) DO UPDATE SET
                        role = excluded.role,
                        granted_by = excluded.granted_by,
                        updated_at = excluded.updated_at
                """, (project, target_tenant_id, role, actor, now, now))
                conn.commit()
                return None
            except sqlite3.Error:
                return "Error: database error granting access"
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({"project": project, "tenant_id": target_tenant_id, "role": role, "granted_by": actor})

    @mcp.tool()
    async def revoke_project_access(
        project: str, 
        target_tenant_id: str,
        human_approval: bool = False
    ) -> str:
        """Revoke a tenant's access to a project. Requires owner role."""
        if not human_approval:
            return "Error: human_approval=True required for revoke_project_access"
        
        perm_err = await _check_project_access(project, "owner")
        if perm_err:
            return perm_err
        
        actor = _get_actor()
        if target_tenant_id == actor:
            return "Error: cannot revoke your own access (use transfer_project_ownership)"
        
        def _do_write():
            cur = conn.execute(
                "DELETE FROM project_permissions WHERE project = ? AND tenant_id = ?",
                (project, target_tenant_id)
            )
            conn.commit()
            return cur.rowcount
        
        rows = await _locked_write(_do_write)
        if rows == 0:
            return f"Error: no access entry found for '{target_tenant_id}' on '{project}'"
        return json.dumps({"revoked": True, "project": project, "tenant_id": target_tenant_id})

    @mcp.tool()
    async def list_project_permissions(project: str) -> str:
        """List all members with access to a project."""
        perm_err = await _check_project_access(project, "reader")
        if perm_err:
            return perm_err
        
        try:
            rows = await _db_execute("""
                SELECT tenant_id, role, granted_by, created_at
                FROM project_permissions
                WHERE project = ?
                ORDER BY 
                    CASE role WHEN 'owner' THEN 0 WHEN 'contributor' THEN 1 ELSE 2 END,
                    created_at
            """, (project,))
            return json.dumps({"project": project, "members": [dict(r) for r in rows]})
        except sqlite3.Error:
            return "Error: database error listing permissions"

    @mcp.tool()
    async def get_my_permissions() -> str:
        """List all projects the calling tenant has access to."""
        actor = _get_actor()
        try:
            rows = await _db_execute("""
                SELECT project, role, created_at
                FROM project_permissions
                WHERE tenant_id = ?
                ORDER BY project
            """, (actor,))
            return json.dumps({"tenant_id": actor, "projects": [dict(r) for r in rows]})
        except sqlite3.Error:
            return "Error: database error querying permissions"

    @mcp.tool()
    async def transfer_project_ownership(
        project: str,
        new_owner_tenant_id: str,
        human_approval: bool = False
    ) -> str:
        """Transfer project ownership to another tenant. Current owner becomes contributor."""
        if not human_approval:
            return "Error: human_approval=True required for transfer_project_ownership"
        
        perm_err = await _check_project_access(project, "owner")
        if perm_err:
            return perm_err
        
        actor = _get_actor()
        now = _now()
        
        def _do_write():
            try:
                # Make new tenant owner
                conn.execute("""
                    INSERT INTO project_permissions (project, tenant_id, role, granted_by, created_at, updated_at)
                    VALUES (?, ?, 'owner', ?, ?, ?)
                    ON CONFLICT(project, tenant_id) DO UPDATE SET
                        role = 'owner',
                        granted_by = excluded.granted_by,
                        updated_at = excluded.updated_at
                """, (project, new_owner_tenant_id, actor, now, now))
                # Demote current owner to contributor
                conn.execute("""
                    UPDATE project_permissions SET role = 'contributor', updated_at = ?
                    WHERE project = ? AND tenant_id = ? AND role = 'owner'
                """, (now, project, actor))
                conn.commit()
                return None
            except sqlite3.Error:
                return "Error: database error transferring ownership"
        
        result = await _locked_write(_do_write)
        if result:
            return result
        return json.dumps({
            "transferred": True, 
            "project": project, 
            "new_owner": new_owner_tenant_id,
            "previous_owner": actor,
            "previous_owner_new_role": "contributor"
        })

    @mcp.tool()
    async def get_project_access(project: str, target_tenant_id: str) -> str:
        """Check a specific tenant's access to a project."""
        perm_err = await _check_project_access(project, "reader")
        if perm_err:
            return perm_err
        
        try:
            row = await _db_execute_one(
                "SELECT role, granted_by, created_at FROM project_permissions WHERE project = ? AND tenant_id = ?",
                (project, target_tenant_id)
            )
            if not row:
                return json.dumps({"project": project, "tenant_id": target_tenant_id, "access": None})
            return json.dumps({"project": project, "tenant_id": target_tenant_id, "access": dict(row)})
        except sqlite3.Error:
            return "Error: database error querying access"

    @mcp.tool()
    async def migrate_permissions(human_approval: bool = False) -> str:
        """Admin: Backfill permissions for existing projects. Makes each tenant owner of their projects."""
        if not human_approval:
            return "Error: human_approval=True required for migrate_permissions"
        
        actor = _get_actor()
        now = _now()
        
        try:
            rows = await _db_execute("""
                SELECT DISTINCT t.project, al.actor
                FROM tasks t
                JOIN activity_log al ON al.task_id = t.id AND al.action = 'created'
                WHERE al.actor != 'system'
                  AND NOT EXISTS (
                      SELECT 1 FROM project_permissions pp 
                      WHERE pp.project = t.project AND pp.tenant_id = al.actor
                  )
            """, ())
        except sqlite3.Error:
            return "Error: database error querying tasks"
        
        count = 0
        def _do_write():
            nonlocal count
            for row in rows:
                conn.execute("""
                    INSERT OR IGNORE INTO project_permissions 
                        (project, tenant_id, role, granted_by, created_at, updated_at)
                    VALUES (?, ?, 'owner', 'migration', ?, ?)
                """, (row["project"], row["actor"], now, now))
                count += 1
            conn.commit()
            return None
        
        await _locked_write(_do_write)
        return json.dumps({"migrated": count, "message": f"Granted owner role on {count} project-tenant pairs"})

    @mcp.tool()
    async def set_permission_enforcement(enabled: bool, human_approval: bool = False) -> str:
        """Admin: Toggle permission enforcement. WARNING: affects all operations."""
        if not human_approval:
            return "Error: human_approval=True required for set_permission_enforcement"
        
        if enabled:
            os.environ["OPM_ENFORCE_PERMISSIONS"] = "1"
        else:
            os.environ.pop("OPM_ENFORCE_PERMISSIONS", None)
        
        return json.dumps({
            "enforcement_enabled": enabled,
            "note": "Runtime change — set OPM_ENFORCE_PERMISSIONS=1 for persistent enforcement"
        })

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
                # Unauthenticated mode: no env var keys AND tenant_keys table is empty AND
                # OPM_REGISTRATION_KEY is not set.  If the registration key IS set the admin
                # clearly intends auth to be required — an empty DB means "no valid tokens yet",
                # not "open to everyone".
                if not tenant_keys and not os.environ.get("OPM_REGISTRATION_KEY"):
                    try:
                        has_db_keys_row = await _db_execute_one("SELECT 1 FROM tenant_keys LIMIT 1", ())
                        has_db_keys = bool(has_db_keys_row)
                    except sqlite3.Error:
                        has_db_keys = False
                    if not has_db_keys:
                        return "system", None

                auth_header = request.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer "):
                    return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
                token = auth_header[7:]
                tenant_id = await _verify_bearer(token)
                if tenant_id:
                    return tenant_id, None
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
                        rows = await _db_execute(
                            f"SELECT id, title, priority, status, assignee FROM tasks"
                            f" {where} ORDER BY {_PRIORITY_CASE}, created_at LIMIT ? OFFSET ?",
                            tuple(params + [limit + 1, offset]),
                        )
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
                    
                    def _do_write():
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
                            return None
                        except sqlite3.IntegrityError:
                            return JSONResponse({"error": f"Error: task '{tid}' already exists"}, status_code=409)
                        except sqlite3.Error:
                            return JSONResponse({"error": "Error: database error"}, status_code=500)
                    
                    result = await _locked_write(_do_write)
                    if result:
                        return result
                    t_project = body.get("project", "default")
                    t_priority = body.get("priority", "medium")
                    _publish_event("task.created", {
                        "id": tid, "title": body.get("title"), "priority": t_priority,
                        "status": "pending", "project": t_project,
                    })
                    _publish_queue_stats()
                    return JSONResponse(
                        {"id": tid, "status": "pending", "priority": t_priority, "project": t_project},
                        status_code=201,
                    )
                return JSONResponse({"error": "Method not allowed"}, status_code=405)

            async def task_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                task_id = request.path_params["id"]
                if request.method == "GET":
                    row = await _db_execute_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
                    if row is None:
                        return JSONResponse({"error": f"Error: task '{task_id}' not found"}, status_code=404)
                    task = _row(row)
                    depends_on_rows = await _db_execute(
                        "SELECT depends_on FROM task_deps WHERE task_id = ?", (task_id,)
                    )
                    task["depends_on"] = [r[0] for r in depends_on_rows]
                    blocked_by_rows = await _db_execute(
                        "SELECT td.depends_on FROM task_deps td JOIN tasks t ON td.depends_on = t.id"
                        " WHERE td.task_id = ? AND t.status != 'done'",
                        (task_id,),
                    )
                    task["blocked_by"] = [r[0] for r in blocked_by_rows]
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
                    
                    def _do_write():
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
                            return old_data
                        except sqlite3.Error:
                            return JSONResponse({"error": "Error: database error"}, status_code=500)
                    
                    result = await _locked_write(_do_write)
                    if isinstance(result, JSONResponse):
                        return result
                    old_data = result
                    task_project_patch = upd.get("project") or old_data.get("project", "default")
                    _publish_event("task.updated", {
                        "id": task_id, "updated": list(upd.keys()), "project": task_project_patch,
                    })
                    _publish_queue_stats()
                    return JSONResponse({"id": task_id, "updated": list(upd.keys())})
                elif request.method == "DELETE":
                    confirm = request.query_params.get("confirm", "").lower() == "true"
                    if not confirm:
                        return JSONResponse({"error": "Error: confirm=true is required to delete a task"}, status_code=400)
                    
                    def _do_write():
                        row = conn.execute("SELECT project FROM tasks WHERE id = ?", (task_id,)).fetchone()
                        if row is None:
                            return JSONResponse({"error": f"Error: task '{task_id}' not found"}, status_code=404)
                        task_project_del = row["project"]
                        conn.execute(
                            "DELETE FROM task_deps WHERE task_id = ? OR depends_on = ?", (task_id, task_id)
                        )
                        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                        _log(task_id, "deleted", actor=actor)
                        conn.commit()
                        return task_project_del
                    
                    result = await _locked_write(_do_write)
                    if isinstance(result, JSONResponse):
                        return result
                    task_project_del = result
                    _publish_event("task.deleted", {"id": task_id, "project": task_project_del})
                    _publish_queue_stats()
                    return JSONResponse({"id": task_id, "deleted": True})
                return JSONResponse({"error": "Method not allowed"}, status_code=405)

            async def projects_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                try:
                    rows = await _db_execute(
                        "SELECT project, COUNT(*) as total,"
                        " SUM(CASE WHEN status != 'done' THEN 1 ELSE 0 END) as open"
                        " FROM tasks GROUP BY project ORDER BY project",
                        (),
                    )
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
                detailed = request.query_params.get("detailed", "").lower() == "true"
                try:
                    by_status_rows = await _db_execute(
                        "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status", ()
                    )
                    by_status = {r["status"]: r["cnt"] for r in by_status_rows}
                    by_priority_rows = await _db_execute(
                        "SELECT priority, COUNT(*) as cnt FROM tasks WHERE status != 'done' GROUP BY priority", ()
                    )
                    by_priority = {r["priority"]: r["cnt"] for r in by_priority_rows}
                    oldest = await _db_execute_one(
                        "SELECT MIN(created_at) as oldest FROM tasks WHERE status != 'done'", ()
                    )
                except sqlite3.Error:
                    return JSONResponse({"error": "Error: database error"}, status_code=500)
                result: dict = {
                    "by_status": by_status,
                    "by_priority": by_priority,
                    "oldest_open": oldest["oldest"] if oldest else None,
                }
                if detailed:
                    result["uptime_sec"] = int(time.time() - _start_time)
                    result["active_sse_clients"] = len(_event_bus_clients)
                    try:
                        by_project: dict[str, dict] = {}
                        project_rows = await _db_execute(
                            "SELECT project, status, COUNT(*) as cnt FROM tasks GROUP BY project, status", ()
                        )
                        for r in project_rows:
                            by_project.setdefault(r["project"], {})[r["status"]] = r["cnt"]
                        result["by_project"] = by_project
                    except sqlite3.Error:
                        pass
                return JSONResponse(result)

            # ------------------------------------------------------------------
            # Registration rate limiter (in-memory, resets on restart)
            # ------------------------------------------------------------------
            _reg_attempts: dict[str, list[float]] = defaultdict(list)
            _RATE_WINDOW = 60.0
            _RATE_MAX = 5
            _SQUAD_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

            def _check_rate_limit(ip: str) -> bool:
                """Return True if request is allowed, False if rate limit exceeded."""
                now = time.monotonic()
                # Opportunistic eviction: purge IPs whose entire window has expired so the
                # dict doesn't grow without bound under a flood of unique source IPs.
                stale_keys = [k for k, v in _reg_attempts.items() if not v or now - v[-1] >= _RATE_WINDOW]
                for k in stale_keys:
                    del _reg_attempts[k]
                unexpired = [t for t in _reg_attempts[ip] if now - t < _RATE_WINDOW]
                if len(unexpired) >= _RATE_MAX:
                    _reg_attempts[ip] = unexpired
                    return False
                unexpired.append(now)
                _reg_attempts[ip] = unexpired
                return True

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

                if not isinstance(provided_key, str) or not hmac.compare_digest(provided_key, registration_key):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                if not isinstance(squad, str) or not _SQUAD_RE.match(squad):
                    return JSONResponse(
                        {"error": "Invalid squad name. Must be 1–64 characters: letters, digits, hyphens, underscores."},
                        status_code=400,
                    )

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

            async def deregister_endpoint(request: Request) -> Response:
                registration_key = os.environ.get("OPM_REGISTRATION_KEY")
                if not registration_key:
                    return JSONResponse({"error": "Not Found"}, status_code=404)

                reg_key_header = request.headers.get("X-Registration-Key", "")
                if not reg_key_header or not hmac.compare_digest(reg_key_header, registration_key):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                squad = request.path_params["squad"]

                if not _SQUAD_RE.match(squad):
                    return JSONResponse(
                        {"error": "Invalid squad name. Must be 1–64 characters: letters, digits, hyphens, underscores."},
                        status_code=400,
                    )

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

                return Response(status_code=204)

            async def events_endpoint(request: Request) -> Response:
                actor, err = await _check_auth(request)
                if err:
                    return err
                q: asyncio.Queue = asyncio.Queue(maxsize=100)
                _event_bus_clients.append(q)
                _ensure_bg_health_task()
                _ensure_bg_sub_task()

                async def event_stream():
                    try:
                        while True:
                            try:
                                payload = await asyncio.wait_for(q.get(), timeout=30.0)
                                data = json.dumps(payload)
                                yield f"data: {data}\n\n".encode()
                            except asyncio.TimeoutError:
                                yield b": keepalive\n\n"
                    finally:
                        try:
                            _event_bus_clients.remove(q)
                        except ValueError:
                            pass

                return StreamingResponse(
                    event_stream(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                    },
                )

            async def project_summary_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                project = request.path_params["project"]
                return _tool_resp(await _project_summary(project))

            async def notifications_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                if request.method == "POST":
                    body, body_err = await _read_json_body(request)
                    if body_err:
                        return body_err
                    squad = body.get("squad", "") if isinstance(body, dict) else ""
                    event_type = body.get("event_type", "") if isinstance(body, dict) else ""
                    data = body.get("data") if isinstance(body, dict) else None
                    if not squad or len(squad) > _MAX_SHORT_FIELD:
                        return JSONResponse({"error": "Error: 'squad' is required"}, status_code=400)
                    if event_type not in VALID_NOTIFICATION_TYPES:
                        return JSONResponse(
                            {"error": f"Error: invalid event_type. Must be one of: {', '.join(sorted(VALID_NOTIFICATION_TYPES))}"},
                            status_code=400,
                        )
                    now = _now()
                    
                    def _do_write():
                        try:
                            conn.execute(
                                "INSERT INTO team_events (squad, event_type, data, created_at) VALUES (?, ?, ?, ?)",
                                (squad, event_type, json.dumps(data) if data is not None else None, now),
                            )
                            conn.commit()
                            return None
                        except sqlite3.Error:
                            return "Error: database error"
                    
                    result = await _locked_write(_do_write)
                    if result:
                        return JSONResponse({"error": result}, status_code=500)
                    _publish_event(event_type, {"squad": squad, "data": data})
                    return JSONResponse({"squad": squad, "event_type": event_type, "created_at": now}, status_code=201)
                return JSONResponse({"error": "Method not allowed"}, status_code=405)

            async def status_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                try:
                    rows = await _db_execute(
                        "SELECT squad, status, message, updated_at FROM team_status ORDER BY squad", ()
                    )
                except sqlite3.Error:
                    return JSONResponse({"error": "Error: database error"}, status_code=500)
                return JSONResponse({"squads": [dict(r) for r in rows]})

            async def status_squad_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                squad = request.path_params["squad"]
                if request.method == "GET":
                    try:
                        row = await _db_execute_one(
                            "SELECT squad, status, message, updated_at FROM team_status WHERE squad = ?",
                            (squad,),
                        )
                    except sqlite3.Error:
                        return JSONResponse({"error": "Error: database error"}, status_code=500)
                    if row is None:
                        return JSONResponse({"error": f"Error: squad '{squad}' not found"}, status_code=404)
                    return JSONResponse(dict(row))
                elif request.method == "PUT":
                    body, body_err = await _read_json_body(request)
                    if body_err:
                        return body_err
                    status = body.get("status", "") if isinstance(body, dict) else ""
                    message = body.get("message") if isinstance(body, dict) else None
                    if status not in VALID_TEAM_STATUSES:
                        return JSONResponse(
                            {"error": f"Error: invalid status. Must be one of: {', '.join(sorted(VALID_TEAM_STATUSES))}"},
                            status_code=400,
                        )
                    now = _now()
                    async with _lock:
                        try:
                            conn.execute(
                                "INSERT INTO team_status (squad, status, message, updated_at)"
                                " VALUES (?, ?, ?, ?)"
                                " ON CONFLICT(squad) DO UPDATE SET status=excluded.status,"
                                " message=excluded.message, updated_at=excluded.updated_at",
                                (squad, status, message, now),
                            )
                            conn.commit()
                        except sqlite3.Error:
                            return JSONResponse({"error": "Error: database error"}, status_code=500)
                    _publish_event("squad.status", {"squad": squad, "status": status, "message": message})
                    return JSONResponse({"squad": squad, "status": status, "message": message, "updated_at": now})
                return JSONResponse({"error": "Method not allowed"}, status_code=405)

            async def team_events_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                p = request.query_params
                squad = p.get("squad")
                event_type = p.get("event_type")
                try:
                    limit = int(p.get("limit", 50))
                except ValueError:
                    return JSONResponse({"error": "Error: invalid limit"}, status_code=400)
                limit = max(1, min(limit, 200))
                conditions: list[str] = []
                params: list[object] = []
                if squad:
                    conditions.append("squad = ?")
                    params.append(squad)
                if event_type:
                    conditions.append("event_type = ?")
                    params.append(event_type)
                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                try:
                    rows = await _db_execute(
                        f"SELECT id, squad, event_type, data, created_at FROM team_events"
                        f" {where} ORDER BY created_at DESC LIMIT ?",
                        tuple(params + [limit]),
                    )
                except sqlite3.Error:
                    return JSONResponse({"error": "Error: database error"}, status_code=500)
                return JSONResponse({"events": [dict(r) for r in rows], "count": len(rows)})

            async def subscriptions_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                if request.method == "GET":
                    p = request.query_params
                    subscriber = p.get("subscriber")
                    event_type = p.get("event_type")
                    conditions: list[str] = []
                    params: list[object] = []
                    if subscriber:
                        conditions.append("subscriber = ?")
                        params.append(subscriber)
                    if event_type:
                        conditions.append("event_type = ?")
                        params.append(event_type)
                    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                    try:
                        rows = await _db_execute(
                            f"SELECT id, subscriber, url, event_type, project, interval_sec,"
                            f" enabled, last_fired_at, created_at"
                            f" FROM event_subscriptions {where} ORDER BY created_at",
                            tuple(params),
                        )
                    except sqlite3.Error:
                        return JSONResponse({"error": "Error: database error"}, status_code=500)
                    return JSONResponse({"subscriptions": [dict(r) for r in rows]})
                elif request.method == "POST":
                    body, body_err = await _read_json_body(request)
                    if body_err:
                        return body_err
                    if not isinstance(body, dict):
                        return JSONResponse({"error": "Error: invalid JSON body"}, status_code=400)
                    sub_id = body.get("id", "")
                    subscriber = body.get("subscriber", "")
                    url = body.get("url", "")
                    event_type = body.get("event_type", "")
                    project = body.get("project")
                    interval_sec = body.get("interval_sec")
                    if not sub_id or len(sub_id) > _MAX_SHORT_FIELD:
                        return JSONResponse({"error": "Error: 'id' is required"}, status_code=400)
                    if not subscriber or len(subscriber) > _MAX_SHORT_FIELD:
                        return JSONResponse({"error": "Error: 'subscriber' is required"}, status_code=400)
                    ssrf_err = await _check_ssrf(url)
                    if ssrf_err:
                        return JSONResponse({"error": ssrf_err}, status_code=400)
                    if event_type not in VALID_SUBSCRIPTION_EVENTS:
                        return JSONResponse(
                            {"error": f"Error: invalid event_type. Must be one of: {', '.join(sorted(VALID_SUBSCRIPTION_EVENTS))}"},
                            status_code=400,
                        )
                    if interval_sec is not None:
                        try:
                            interval_sec = int(interval_sec)
                        except (TypeError, ValueError):
                            return JSONResponse({"error": "Error: interval_sec must be an integer"}, status_code=400)
                        if interval_sec < _SUB_MIN_INTERVAL or interval_sec > _SUB_MAX_INTERVAL:
                            return JSONResponse(
                                {"error": f"Error: interval_sec must be between {_SUB_MIN_INTERVAL} and {_SUB_MAX_INTERVAL}"},
                                status_code=400,
                            )
                    
                    def _do_write():
                        try:
                            conn.execute(
                                "INSERT INTO event_subscriptions"
                                " (id, subscriber, url, event_type, project, interval_sec, enabled, created_at)"
                                " VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                                (sub_id, subscriber, url, event_type, project, interval_sec, _now()),
                            )
                            conn.commit()
                            return None
                        except sqlite3.IntegrityError:
                            return JSONResponse({"error": f"Error: subscription '{sub_id}' already exists"}, status_code=409)
                        except sqlite3.Error:
                            return JSONResponse({"error": "Error: database error"}, status_code=500)
                    
                    result = await _locked_write(_do_write)
                    if result:
                        return result
                    _ensure_bg_sub_task()
                    return JSONResponse(
                        {"id": sub_id, "subscriber": subscriber, "event_type": event_type,
                         "project": project, "interval_sec": interval_sec},
                        status_code=201,
                    )
                return JSONResponse({"error": "Method not allowed"}, status_code=405)

            async def subscription_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                sub_id = request.path_params["id"]
                if request.method == "GET":
                    try:
                        row = await _db_execute_one(
                            "SELECT id, subscriber, url, event_type, project, interval_sec,"
                            " enabled, last_fired_at, created_at"
                            " FROM event_subscriptions WHERE id = ?",
                            (sub_id,),
                        )
                    except sqlite3.Error:
                        return JSONResponse({"error": "Error: database error"}, status_code=500)
                    if row is None:
                        return JSONResponse({"error": f"Error: subscription '{sub_id}' not found"}, status_code=404)
                    return JSONResponse(dict(row))
                elif request.method == "DELETE":
                    confirm = request.query_params.get("confirm", "").lower() == "true"
                    if not confirm:
                        return JSONResponse({"error": "Error: confirm=true is required"}, status_code=400)
                    
                    def _do_write():
                        cur = conn.execute("DELETE FROM event_subscriptions WHERE id = ?", (sub_id,))
                        conn.commit()
                        if cur.rowcount == 0:
                            return f"Error: subscription '{sub_id}' not found"
                        return None
                    
                    result = await _locked_write(_do_write)
                    if result:
                        return JSONResponse({"error": result}, status_code=404 if "not found" in result else 500)
                    return JSONResponse({"id": sub_id, "deleted": True})
                return JSONResponse({"error": "Method not allowed"}, status_code=405)

            async def telemetry_summary_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                try:
                    hours = int(request.query_params.get("hours", 24))
                except ValueError:
                    return JSONResponse({"error": "Invalid hours parameter"}, status_code=400)
                result = await get_telemetry_summary(hours)
                if result.startswith("Error:"):
                    return JSONResponse({"error": result}, status_code=500)
                return JSONResponse(json.loads(result))

            async def telemetry_tool_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                tool_name = request.path_params["tool_name"]
                try:
                    hours = int(request.query_params.get("hours", 24))
                except ValueError:
                    return JSONResponse({"error": "Invalid hours parameter"}, status_code=400)
                result = await get_telemetry_by_tool(tool_name, hours)
                if result.startswith("Error:"):
                    return JSONResponse({"error": result}, status_code=500)
                return JSONResponse(json.loads(result))

            async def telemetry_top_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                try:
                    limit = int(request.query_params.get("limit", 10))
                    hours = int(request.query_params.get("hours", 24))
                except ValueError:
                    return JSONResponse({"error": "Invalid limit or hours parameter"}, status_code=400)
                result = await list_top_tools(limit, hours)
                if result.startswith("Error:"):
                    return JSONResponse({"error": result}, status_code=500)
                return JSONResponse(json.loads(result))

            async def telemetry_errors_endpoint(request: Request) -> JSONResponse:
                actor, err = await _check_auth(request)
                if err:
                    return err
                try:
                    hours = int(request.query_params.get("hours", 24))
                except ValueError:
                    return JSONResponse({"error": "Invalid hours parameter"}, status_code=400)
                result = await get_error_summary(hours)
                if result.startswith("Error:"):
                    return JSONResponse({"error": result}, status_code=500)
                return JSONResponse(json.loads(result))

            return Router(routes=[
                Route("/tasks", endpoint=tasks_endpoint, methods=["GET", "POST"]),
                Route("/tasks/{id:str}", endpoint=task_endpoint, methods=["GET", "PATCH", "DELETE"]),
                Route("/projects", endpoint=projects_endpoint, methods=["GET"]),
                Route("/projects/{project:str}/summary", endpoint=project_summary_endpoint, methods=["GET"]),
                Route("/stats", endpoint=stats_endpoint, methods=["GET"]),
                Route("/events", endpoint=events_endpoint, methods=["GET"]),
                Route("/notifications", endpoint=notifications_endpoint, methods=["POST"]),
                Route("/status", endpoint=status_endpoint, methods=["GET"]),
                Route("/status/{squad:str}", endpoint=status_squad_endpoint, methods=["GET", "PUT"]),
                Route("/team/events", endpoint=team_events_endpoint, methods=["GET"]),
                Route("/subscriptions", endpoint=subscriptions_endpoint, methods=["GET", "POST"]),
                Route("/subscriptions/{id:str}", endpoint=subscription_endpoint, methods=["GET", "DELETE"]),
                Route("/register", endpoint=register_endpoint, methods=["POST"]),
                Route("/register/{squad:str}", endpoint=deregister_endpoint, methods=["DELETE"]),
                Route("/telemetry/summary", endpoint=telemetry_summary_endpoint, methods=["GET"]),
                Route("/telemetry/tools/{tool_name:str}", endpoint=telemetry_tool_endpoint, methods=["GET"]),
                Route("/telemetry/top", endpoint=telemetry_top_endpoint, methods=["GET"]),
                Route("/telemetry/errors", endpoint=telemetry_errors_endpoint, methods=["GET"]),
            ])

        mcp._rest_router = _build_rest_router()
    
    def get_write_lock() -> asyncio.Lock:
        """Return the write lock for external monitoring/recovery."""
        return _lock
    
    mcp.get_write_lock = get_write_lock

    return mcp
