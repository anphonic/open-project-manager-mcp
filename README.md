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

### Proactive Messaging (Team Status & Events)

**9 new tools** for real-time squad coordination via team status, event publishing, and SSE subscriptions.

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_server_stats` | — | Server statistics: task counts, uptime, active SSE connections |
| `get_project_summary` | `project` | Per-project task summary with overdue count |
| `set_team_status` | `status`, `message?` | Set your team's status (`online`/`offline`/`busy`/`degraded`) |
| `get_team_status` | `squad?` | Get all teams' status or specific team |
| `post_team_event` | `event_type`, `data?` | Push a team event (milestone, error, health report, etc.) |
| `get_team_events` | `squad?`, `event_type?`, `since?`, `limit?` | Query team events with optional filters |
| `subscribe_events` | `id`, `subscriber`, `url`, `event_type`, `squad?` | Subscribe to periodic server events (HTTPS URL only) |
| `list_subscriptions` | `subscriber?` | List event subscriptions |
| `unsubscribe_events` | `id`, `human_approval` | Remove subscription |

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

## Server Setup

Complete first-time admin bootstrap for a shared HTTP server.

### Step 1 — Install

```bash
pip install 'open-project-manager-mcp[http]'
```

### Step 2 — Generate an admin token

```bash
open-project-manager-mcp --generate-token admin
```

Save the output token — it won't be shown again.

### Step 3 — Configure environment

**Linux / macOS (`start.sh`)**

```bash
#!/usr/bin/env bash
export OPM_TENANT_KEYS='{"admin":{"key":"<admin-token>"}}'

# Optional: set this to let remote squads self-register via POST /api/v1/register.
# Omit to manually issue all tokens.
export OPM_REGISTRATION_KEY='<registration-secret>'

export OPM_DB_PATH='/var/data/opm/opm.db'

exec open-project-manager-mcp --http --rest-api --host 0.0.0.0 --port 8765
```

```bash
chmod 600 start.sh   # contains secrets — restrict permissions
```

**Windows (PowerShell)**

```powershell
$env:OPM_TENANT_KEYS = '{"admin":{"key":"<admin-token>"}}'
$env:OPM_REGISTRATION_KEY = '<registration-secret>'
$env:OPM_DB_PATH = 'C:\data\opm\opm.db'

open-project-manager-mcp --http --rest-api --host 0.0.0.0 --port 8765
```

### Step 4 — Start the server

```bash
./start.sh
```

Verify it's running:

```bash
# Should return 401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/mcp

# Should return 200 (or MCP handshake response)
curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/mcp \
  -H "Authorization: Bearer <admin-token>"
```

PowerShell equivalent:

```powershell
# Should return 401
(Invoke-WebRequest http://localhost:8765/mcp -SkipHttpErrorCheck).StatusCode

# Should return 200
(Invoke-WebRequest http://localhost:8765/mcp `
  -Headers @{ Authorization = "Bearer <admin-token>" } `
  -SkipHttpErrorCheck).StatusCode
```

> **Unauthenticated mode:** If neither `OPM_TENANT_KEYS` nor `OPM_REGISTRATION_KEY` is set, the server runs without auth. Suitable for local stdio use only — do not expose on a network.

---

## Client / MCP Setup

How connecting clients (squad members, AI tools) get a token and configure their MCP client.

### Option A — Admin-issued token

The admin generates a token and adds it to `OPM_TENANT_KEYS`, then restarts the server:

```bash
open-project-manager-mcp --generate-token my-team
# Add {"my-team": {"key": "<token>"}} to OPM_TENANT_KEYS and restart
```

Share the token out of band.

### Option B — Self-service registration

Requires `OPM_REGISTRATION_KEY` to be set and `--rest-api` to be active.

```bash
curl -X POST http://<host>:8765/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{"squad": "my-team", "registration_key": "<OPM_REGISTRATION_KEY>"}'
```

Returns `{"squad": "my-team", "token": "<generated>"}`. Save the token — rate-limited to 5 req/min per IP.

### Setting `OPM_BEARER_TOKEN`

**Windows** (persistent, all apps):

```cmd
setx OPM_BEARER_TOKEN "your-token-here"
```

Restart any running tools to pick it up.

**macOS** (persistent, all terminal-launched tools):

```bash
echo 'export OPM_BEARER_TOKEN="your-token-here"' >> ~/.zshenv
```

For GUI-launched apps (e.g. Claude Desktop), also run:

```bash
launchctl setenv OPM_BEARER_TOKEN "your-token-here"
```

This resets on reboot; add to a LaunchAgent plist for persistence.

**Linux** (persistent, all apps via systemd user session):

```bash
mkdir -p ~/.config/environment.d
echo 'OPM_BEARER_TOKEN=your-token-here' >> ~/.config/environment.d/mcp-tokens.conf
```

For non-systemd / terminal only, add to `~/.profile`:

```bash
export OPM_BEARER_TOKEN="your-token-here"
```

### MCP config

`${env:OPM_BEARER_TOKEN}` is resolved by the MCP client — the same snippet works on all platforms:

```json
{
  "mcpServers": {
    "open-project-manager": {
      "url": "http://<host>:8765/mcp",
      "headers": {
        "Authorization": "Bearer ${env:OPM_BEARER_TOKEN}"
      }
    }
  }
}
```

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
| `GET` | `/api/v1/events` | Real-time SSE stream (task + server events) |
| `GET` | `/api/v1/stats?detailed=true` | Extended server state snapshot |
| `GET` | `/api/v1/projects/{project}/summary` | Project summary with task counts |
| `PUT` | `/api/v1/status` | Set your team's status (online/offline/busy/degraded) |
| `GET` | `/api/v1/status` | Get all teams' status |
| `GET` | `/api/v1/status/{squad}` | Get specific team's status |
| `POST` | `/api/v1/events` | Push a team event (persisted) |
| `GET` | `/api/v1/team-events` | Query team events with optional filters |
| `POST` | `/api/v1/subscriptions` | Create event subscription |
| `GET` | `/api/v1/subscriptions` | List subscriptions |
| `DELETE` | `/api/v1/subscriptions/{id}` | Remove subscription |

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

### Server-Sent Events (SSE)

Access real-time task and server events via `GET /api/v1/events`:

```bash
curl -N -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/events
```

Events are streamed in `text/event-stream` format. Each event has a `data` field (JSON-encoded).

**Task Events:**
- `task.created` — A task was created. `data` includes full task object.
- `task.updated` — A task was updated. `data` includes full task object and changed fields.
- `task.completed` — A task was marked done. `data` includes task `id` and completion time.
- `task.deleted` — A task was deleted. `data` includes task `id`.

**Server Events:**
- `server.health` — Server heartbeat (emitted every 30 seconds). `data` includes uptime, active connections, memory.
- `queue.stats` — Task queue snapshot (emitted after state changes). `data` includes counts by status/priority.
- `notification.received` — A team posted a notification. `data` includes squad, message, timestamp.
- `team.status_changed` — A team changed status. `data` includes squad, new status, timestamp.
- `team.event` — A team published a custom event. `data` includes squad, event type, event data.

**Filtering:**

```bash
# Only task.created and task.completed
curl -N "http://192.168.1.178:8765/api/v1/events?event_type=task.created,task.completed" \
  -H "Authorization: Bearer <token>"

# Only events from a specific squad
curl -N "http://192.168.1.178:8765/api/v1/events?squad=coordinator" \
  -H "Authorization: Bearer <token>"
```

---

## Team Status & Events

Squads can publish status and custom events for cross-team coordination (separate from tasks).

### Team Status

Set your squad's current status (`online`, `offline`, `busy`, `degraded`):

```bash
curl -X PUT http://192.168.1.178:8765/api/v1/status \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"status": "online", "message": "All systems nominal"}'
```

View all squads' status:

```bash
curl -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/status
```

Returns: `{ squads: { "mrrobot": { status: "online", message: "...", updated_at: "..." }, ... } }`

### Team Events

Push a custom event from your squad (e.g., "milestone reached", "deployment complete", "degradation"):

```bash
curl -X POST http://192.168.1.178:8765/api/v1/events \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "milestone",
    "data": { "milestone": "v1.0-release", "completed_at": "2025-01-20T14:30:00Z" }
  }'
```

Query all team events (optional filters):

```bash
# All events from all squads, last 100
curl -H "Authorization: Bearer <token>" \
  "http://192.168.1.178:8765/api/v1/team-events?limit=100"

# Only events from 'coordinator' squad
curl -H "Authorization: Bearer <token>" \
  "http://192.168.1.178:8765/api/v1/team-events?squad=coordinator"

# Only 'error' events
curl -H "Authorization: Bearer <token>" \
  "http://192.168.1.178:8765/api/v1/team-events?event_type=error"

# Events since a specific timestamp
curl -H "Authorization: Bearer <token>" \
  "http://192.168.1.178:8765/api/v1/team-events?since=2025-01-20T00:00:00Z"
```

Returns: `{ events: [ { squad, event_type, data, created_at }, ... ] }`

### Event Subscriptions

Subscribe to periodic event delivery at an HTTPS endpoint (webhooks-style, but for SSE events).

```bash
curl -X POST http://192.168.1.178:8765/api/v1/subscriptions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-sub-1",
    "subscriber": "my-service",
    "url": "https://webhooks.example.com/opm-events",
    "event_type": "task.completed",
    "squad": "coordinator"
  }'
```

List active subscriptions:

```bash
curl -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/subscriptions
```

Unsubscribe:

```bash
curl -X DELETE http://192.168.1.178:8765/api/v1/subscriptions/my-sub-1 \
  -H "Authorization: Bearer <token>"
```

---

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
| — | `OPM_REGISTRATION_KEY` | unset | Shared secret for `POST /api/v1/register` self-service token registration. If unset, registration endpoint returns 404. Requires `--rest-api`. |

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
