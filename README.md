# open-project-manager-mcp

SQLite-backed project management MCP server for AI agent squads.

## What it does

Gives agent squads a persistent, prioritized task queue with dependency tracking — local-first, fast, and zero external dependencies beyond Python + FastMCP.

**24 tools** across task CRUD, due dates, full-text search, bulk operations, activity log, export/import, and webhooks.

### Core tools

| Tool | Description |
|------|-------------|
| `create_task` | Create a task with a caller-supplied ID slug |
| `update_task` | Update any task field |
| `complete_task` | Mark a task done |
| `delete_task` | Delete a task (requires `human_approval=True`) |
| `get_task` | Get a single task with dependency info |
| `list_tasks` | List tasks with optional filters, paginated |
| `add_dependency` | Mark task A blocked by task B |
| `remove_dependency` | Remove a dependency edge |
| `list_ready_tasks` | Tasks with no unresolved blockers — safe to start |
| `list_projects` | All projects with open/total task counts |
| `get_stats` | Counts by status/priority + oldest open item age |

### Due dates

| Tool | Parameters | Description |
|------|------------|-------------|
| `list_overdue_tasks` | `project?`, `assignee?`, `limit=20` | Tasks past their `due_date` that are not done |
| `list_due_soon_tasks` | `days=7`, `project?`, `assignee?`, `limit=20` | Tasks due within the next N days (1–365) |

`due_date` is accepted by `create_task` and `update_task` as `YYYY-MM-DD` or ISO 8601 datetime.

### Full-text search

| Tool | Parameters | Description |
|------|------------|-------------|
| `search_tasks` | `query`, `project?`, `status?`, `limit=20` | FTS5 search across title, description, and tags; results ranked by relevance |

Requires SQLite compiled with FTS5 (standard in CPython wheels). Falls back to an error message if unavailable.

### Bulk operations

All bulk tools accept up to 50 items per call and collect per-item errors without aborting the batch.

| Tool | Parameters | Description |
|------|------------|-------------|
| `create_tasks` | `tasks[]` | Bulk-create tasks in one transaction |
| `update_tasks` | `updates[]` | Bulk-update tasks in one transaction |
| `complete_tasks` | `ids[]` | Bulk-mark tasks done in one transaction |

**`tasks[]` item shape** (same fields as `create_task`):

| Field | Required | Notes |
|-------|----------|-------|
| `id` | ✓ | Caller-supplied slug |
| `title` | ✓ | |
| `description` | | |
| `priority` | | `critical`/`high`/`medium`/`low`, default `medium` |
| `project` | | Default `default` |
| `assignee` | | |
| `tags` | | JSON array of strings |
| `due_date` | | `YYYY-MM-DD` or ISO 8601 |

**`updates[]` item shape**: same optional fields plus required `task_id`.

### Activity log

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_task_activity` | `task_id`, `limit=50` | Audit trail for one task, newest first (max 200) |
| `get_activity_log` | `project?`, `limit=50` | Recent activity across all tasks or a single project (max 200) |

Each entry: `{ id, task_id, action, field, old_value, new_value, actor, created_at }`.

### Export / Import

| Tool | Parameters | Description |
|------|------------|-------------|
| `export_all_tasks` | `project?` | Returns a portable JSON string with tasks + dependency edges |
| `import_tasks` | `data`, `merge=False` | Import from `export_all_tasks` output. `merge=True` skips existing IDs; `merge=False` aborts on any conflict. Max payload: 5 MB |

### Webhooks

| Tool | Parameters | Description |
|------|------------|-------------|
| `register_webhook` | `id`, `url`, `events[]`, `project?`, `secret?` | Register an HTTPS webhook |
| `list_webhooks` | `project?` | List webhooks; secrets never returned |
| `delete_webhook` | `id`, `human_approval=True` | Delete a webhook registration |

Requires `pip install 'open-project-manager-mcp[webhooks]'` (adds `httpx`).

See [Webhooks](#webhooks) for payload shape, events, and signature verification.

---

## Install

```bash
pip install open-project-manager-mcp
```

Or run without installing:

```bash
uvx open-project-manager-mcp
```

## MCP config (stdio — default)

```json
{
  "mcpServers": {
    "project-manager": {
      "command": "uvx",
      "args": ["open-project-manager-mcp"]
    }
  }
}
```

## HTTP mode (shared server)

```bash
# Install with HTTP deps
pip install 'open-project-manager-mcp[http]'

# Start server (LAN)
open-project-manager-mcp --http --host 0.0.0.0 --port 8765 --allow-unauthenticated-network
```

### Bearer token auth

```bash
# Generate a token for a squad
open-project-manager-mcp --generate-token my-squad

# Start with auth enabled
OPM_TENANT_KEYS='{"my-squad":{"key":"<token>"}}' \
  open-project-manager-mcp --http --host 0.0.0.0 --port 8765
```

Tokens are validated with constant-time comparison. Transmit over TLS in production.

---

## REST API

Enable alongside the MCP endpoint with `--rest-api` (requires `--http`):

```bash
open-project-manager-mcp --http --rest-api --host 0.0.0.0 --port 8765
```

All endpoints are mounted at `/api/v1`. Auth uses the same Bearer token as the MCP endpoint (`Authorization: Bearer <token>`). When `OPM_TENANT_KEYS` is not set, auth is skipped.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/tasks` | List tasks |
| `POST` | `/api/v1/tasks` | Create a task |
| `GET` | `/api/v1/tasks/{id}` | Get a task (includes `depends_on`, `blocked_by`) |
| `PATCH` | `/api/v1/tasks/{id}` | Partial update |
| `DELETE` | `/api/v1/tasks/{id}` | Delete (requires `?confirm=true`) |
| `GET` | `/api/v1/projects` | List projects with open/total counts |
| `GET` | `/api/v1/stats` | Counts by status/priority + oldest open |

### `GET /api/v1/tasks` query params

| Param | Description |
|-------|-------------|
| `project` | Filter by project |
| `assignee` | Filter by assignee |
| `status` | Filter by status (`pending`/`in_progress`/`done`/`blocked`) |
| `priority` | Filter by priority (`critical`/`high`/`medium`/`low`) |
| `limit` | Page size (1–500, default 20) |
| `offset` | Pagination offset (default 0) |

Response: `{ tasks: [...], has_more: bool, offset: int }`

### `POST /api/v1/tasks` body

Same fields as `create_task`. Returns `201` on success.

### `PATCH /api/v1/tasks/{id}` body

Any subset of: `title`, `description`, `priority`, `project`, `status`, `assignee`, `tags`, `due_date`.

### `DELETE /api/v1/tasks/{id}`

Requires `?confirm=true` query parameter. Returns `{ id, deleted: true }`.

---

## Webhooks

Register a webhook to receive HTTP POST notifications on task events.

### Events

| Event | Fired when |
|-------|-----------|
| `task.created` | A task is created |
| `task.updated` | Any task field is updated |
| `task.completed` | A task is marked done |
| `task.deleted` | A task is deleted |

### Payload shape

```json
{
  "event": "task.created",
  "task_id": "auth-login-ui",
  "timestamp": "2025-01-15T10:30:00+00:00",
  "data": { "id": "auth-login-ui", "title": "...", "priority": "high", "status": "pending", "project": "frontend" }
}
```

`data` contents vary by event type.

### Signature verification

When a `secret` is provided at registration, every delivery includes:

```
X-Hub-Signature-256: sha256=<hex>
```

Computed as HMAC-SHA256 over the raw JSON payload bytes using the secret. Verify this header before trusting the payload.

### Requirements

- **URL must be HTTPS** and resolve to a public IP address. Private/RFC-1918 addresses and loopback are rejected (SSRF protection).
- Delivery is fire-and-forget with a 5 s timeout. No retries in v0.2.0.
- Requires `pip install 'open-project-manager-mcp[webhooks]'`.

### Example registration

```python
register_webhook(
    id="my-hook",
    url="https://hooks.example.com/opm",
    events=["task.created", "task.completed"],
    project="frontend",       # optional — omit to receive all projects
    secret="s3cr3t",          # optional but recommended
)
```

---

## Configuration

| CLI flag | Env var | Default | Notes |
|----------|---------|---------|-------|
| `--db-path` | `OPM_DB_PATH` | Platform data dir | Path to SQLite file |
| `--host` | `OPM_HOST` | `127.0.0.1` | Bind address for HTTP/SSE |
| `--port` | `OPM_PORT` | `8765` | |
| `--max-connections` | `OPM_MAX_CONNECTIONS` | `100` | HTTP/SSE concurrency cap |
| `--rest-api` | — | off | Mount REST API at `/api/v1` (requires `--http`) |
| — | `OPM_TENANT_KEYS` | unset | JSON object of bearer tokens: `{"squad": {"key": "token"}}` |

## Development

```bash
pip install -e '.[dev]'
pytest
```

## Architecture

- **Backend:** SQLite via Python stdlib `sqlite3` — no ORM, no external DB
- **Framework:** FastMCP
- **Transport:** stdio (default) + `--http` / `--sse` for multi-agent LAN access
- **Schema:** `tasks`, `task_deps`, `activity_log`, `webhooks` + FTS5 virtual table — created/migrated at startup

```
open-project-manager-mcp/
├── src/open_project_manager_mcp/
│   ├── __main__.py   # CLI entry, transport config
│   └── server.py     # create_server(db_path) factory, all tools as closures
└── tests/
    ├── test_tools.py
    └── test_config.py
```
