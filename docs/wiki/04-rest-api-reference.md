# REST API Reference

The OPM (Open Project Manager) REST API provides HTTP access to all task management, project coordination, and team status features. The API is mounted at `/api/v1` and requires the server to be started with the `--rest-api` flag.

## Getting Started

### Base URL

```
http://192.168.1.178:8765/api/v1
```

### Server Startup

Enable the REST API by starting the OPM server with both `--http` and `--rest-api` flags:

```bash
OPM_TENANT_KEYS='{"my-squad":{"key":"<token>"}}' \
  open-project-manager-mcp --http --rest-api --host 0.0.0.0 --port 8765
```

### Authentication

All endpoints require Bearer token authentication (unless the server is in unauthenticated mode).

Include the token in the `Authorization` header:

```bash
Authorization: Bearer <token>
```

**Example:**

```bash
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/tasks
```

### Error Response Format

All endpoints return errors in a consistent format:

```json
{
  "error": "Error: <description>"
}
```

Common HTTP status codes:

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 201 | Created |
| 204 | No Content (success, no response body) |
| 400 | Bad Request (validation error) |
| 401 | Unauthorized (missing or invalid token) |
| 404 | Not Found (resource does not exist) |
| 405 | Method Not Allowed |
| 409 | Conflict (e.g., duplicate ID) |
| 413 | Request Entity Too Large (request body exceeds 1 MiB) |
| 429 | Too Many Requests (rate limited) |
| 500 | Internal Server Error (database error) |

---

## API Endpoints

### Health & Stats

#### GET /api/v1/stats

Get task queue statistics (counts by status/priority, oldest open item age).

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `detailed` | boolean | If `true`, return extended state snapshot including per-project breakdowns and uptime |

**Response (Basic):**

```json
{
  "by_status": {
    "pending": 10,
    "in_progress": 5,
    "done": 20,
    "blocked": 2
  },
  "by_priority": {
    "critical": 2,
    "high": 5,
    "medium": 8,
    "low": 2
  },
  "oldest_open": "2025-01-15T10:30:00+00:00"
}
```

**Response (Detailed):**

```json
{
  "by_status": {
    "pending": 10,
    "in_progress": 5,
    "done": 20,
    "blocked": 2
  },
  "by_priority": {
    "critical": 2,
    "high": 5,
    "medium": 8,
    "low": 2
  },
  "oldest_open": "2025-01-15T10:30:00+00:00",
  "uptime_sec": 86400,
  "active_sse_clients": 3,
  "by_project": {
    "frontend": {
      "pending": 3,
      "in_progress": 2,
      "done": 5,
      "blocked": 1
    },
    "backend": {
      "pending": 7,
      "in_progress": 3,
      "done": 15,
      "blocked": 1
    }
  }
}
```

**curl Example:**

```bash
# Basic stats
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/stats

# Detailed stats
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/stats?detailed=true"
```

---

### Tasks

#### GET /api/v1/tasks

List tasks with optional filtering and pagination.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `project` | string | Filter by project name |
| `assignee` | string | Filter by assignee name |
| `status` | string | Filter by status (`pending`, `in_progress`, `done`, `blocked`) |
| `priority` | string | Filter by priority (`critical`, `high`, `medium`, `low`) |
| `limit` | integer | Page size (1–500, default 20) |
| `offset` | integer | Pagination offset (default 0) |

**Response:**

```json
{
  "tasks": [
    {
      "id": "auth-login-ui",
      "title": "Implement login form",
      "priority": "high",
      "status": "in_progress",
      "assignee": "alice"
    }
  ],
  "has_more": true,
  "offset": 0
}
```

**curl Example:**

```bash
# List all tasks
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/tasks

# Filter by status and limit
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/tasks?status=pending&limit=10"

# Filter by project and assignee
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/tasks?project=frontend&assignee=bob"
```

---

#### POST /api/v1/tasks

Create a new task.

**Request Body:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | ✓ | Unique task identifier (caller-supplied slug) |
| `title` | string | ✓ | Task title (max 500 chars) |
| `description` | string | | Task description (max 50,000 chars) |
| `priority` | string | | `critical`, `high`, `medium`, `low` (default: `medium`) |
| `project` | string | | Project name (default: `default`) |
| `assignee` | string | | Team member name |
| `tags` | array | | Array of strings (max 50 tags, each max 100 chars) |
| `due_date` | string | | ISO 8601 datetime or `YYYY-MM-DD` |

**Response:**

```json
{
  "id": "auth-login-ui",
  "status": "pending",
  "priority": "high",
  "project": "frontend"
}
```

**curl Example:**

```bash
curl -X POST http://192.168.1.178:8765/api/v1/tasks \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "auth-login-ui",
    "title": "Implement login form",
    "priority": "high",
    "project": "frontend",
    "assignee": "alice",
    "tags": ["ui", "authentication"],
    "due_date": "2025-02-15"
  }'
```

---

#### GET /api/v1/tasks/{task_id}

Get a single task with full details including dependencies.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `task_id` | string | Task ID |

**Response:**

```json
{
  "id": "auth-login-ui",
  "title": "Implement login form",
  "description": "Create a responsive login form with...",
  "priority": "high",
  "status": "in_progress",
  "project": "frontend",
  "assignee": "alice",
  "tags": ["ui", "authentication"],
  "due_date": "2025-02-15",
  "created_at": "2025-01-15T10:30:00+00:00",
  "updated_at": "2025-01-16T14:20:00+00:00",
  "depends_on": ["db-schema-design"],
  "blocked_by": []
}
```

**curl Example:**

```bash
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/tasks/auth-login-ui
```

---

#### PATCH /api/v1/tasks/{task_id}

Partially update a task. Only include fields that should be changed.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `task_id` | string | Task ID |

**Request Body (all optional):**

| Field | Type | Notes |
|-------|------|-------|
| `title` | string | Max 500 chars |
| `description` | string | Max 50,000 chars |
| `priority` | string | `critical`, `high`, `medium`, `low` |
| `status` | string | `pending`, `in_progress`, `done`, `blocked` |
| `project` | string | Project name |
| `assignee` | string | Team member name |
| `tags` | array | Array of strings |
| `due_date` | string | ISO 8601 datetime or `YYYY-MM-DD` |

**Response:**

```json
{
  "id": "auth-login-ui",
  "updated": ["status", "assignee"]
}
```

**curl Example:**

```bash
curl -X PATCH http://192.168.1.178:8765/api/v1/tasks/auth-login-ui \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "done",
    "assignee": "bob"
  }'
```

---

#### DELETE /api/v1/tasks/{task_id}

Delete a task. Requires explicit confirmation via query parameter.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `task_id` | string | Task ID |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `confirm` | boolean | ✓ | Must be `true` to confirm deletion |

**Response:**

```json
{
  "id": "auth-login-ui",
  "deleted": true
}
```

**curl Example:**

```bash
curl -X DELETE "http://192.168.1.178:8765/api/v1/tasks/auth-login-ui?confirm=true" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

---

### Projects

#### GET /api/v1/projects

List all projects with task counts (open and total).

**Response:**

```json
{
  "projects": [
    {
      "project": "frontend",
      "open": 5,
      "total": 20
    },
    {
      "project": "backend",
      "open": 10,
      "total": 30
    }
  ]
}
```

**curl Example:**

```bash
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/projects
```

---

#### GET /api/v1/projects/{project_id}/summary

Get a summary of a specific project including task counts and status breakdown.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `project_id` | string | Project name |

**Response:**

```json
{
  "project": "frontend",
  "total": 20,
  "pending": 3,
  "in_progress": 2,
  "done": 15,
  "blocked": 0,
  "overdue": 1
}
```

**curl Example:**

```bash
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/projects/frontend/summary
```

---

### Team Status

#### GET /api/v1/status

Get all teams' current status (online/offline/busy/degraded).

**Response:**

```json
{
  "squads": [
    {
      "squad": "mrrobot",
      "status": "online",
      "message": "All systems nominal",
      "updated_at": "2025-01-20T14:30:00+00:00"
    },
    {
      "squad": "westworld",
      "status": "busy",
      "message": "Heavy deployment in progress",
      "updated_at": "2025-01-20T14:25:00+00:00"
    }
  ]
}
```

**curl Example:**

```bash
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/status
```

---

#### GET /api/v1/status/{squad}

Get a specific team's current status.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `squad` | string | Squad name |

**Response:**

```json
{
  "squad": "mrrobot",
  "status": "online",
  "message": "All systems nominal",
  "updated_at": "2025-01-20T14:30:00+00:00"
}
```

**curl Example:**

```bash
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/status/mrrobot
```

---

#### PUT /api/v1/status/{squad}

Set your team's status (online/offline/busy/degraded). This automatically creates or updates the team's status entry.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `squad` | string | Squad name |

**Request Body:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `status` | string | ✓ | One of: `online`, `offline`, `busy`, `degraded` |
| `message` | string | | Optional status message (e.g., "Deployment in progress") |

**Response:**

```json
{
  "squad": "mrrobot",
  "status": "online",
  "message": "All systems nominal",
  "updated_at": "2025-01-20T14:30:00+00:00"
}
```

**curl Example:**

```bash
curl -X PUT http://192.168.1.178:8765/api/v1/status/mrrobot \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "online",
    "message": "All systems nominal"
  }'
```

---

### Team Events

#### POST /api/v1/events

Publish a team event (e.g., "milestone reached", "deployment complete", "degradation detected"). Events are persisted and broadcast to all SSE clients.

**Request Body:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `squad` | string | ✓ | Squad name |
| `event_type` | string | ✓ | One of: `squad.status`, `squad.alert`, `squad.heartbeat` |
| `data` | object | | Custom data payload (optional) |

**Response:**

```json
{
  "squad": "mrrobot",
  "event_type": "squad.alert",
  "created_at": "2025-01-20T14:30:00+00:00"
}
```

**curl Example:**

```bash
curl -X POST http://192.168.1.178:8765/api/v1/events \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "squad": "mrrobot",
    "event_type": "squad.alert",
    "data": {
      "alert_type": "high_memory_usage",
      "threshold": 85,
      "current": 92
    }
  }'
```

---

#### GET /api/v1/team/events

Query team events with optional filtering.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `squad` | string | Filter by squad name |
| `event_type` | string | Filter by event type |
| `limit` | integer | Maximum results to return (1–200, default 50) |

**Response:**

```json
{
  "events": [
    {
      "id": 42,
      "squad": "mrrobot",
      "event_type": "squad.alert",
      "data": "{\"alert_type\": \"high_memory_usage\"}",
      "created_at": "2025-01-20T14:30:00+00:00"
    }
  ],
  "count": 1
}
```

**curl Example:**

```bash
# All events from a squad
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/team/events?squad=mrrobot"

# Only alert events
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/team/events?event_type=squad.alert"

# Limit results
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/team/events?limit=10"
```

---

### Notifications

#### POST /api/v1/notifications

Post a notification from your team. Broadcasts to all SSE clients.

**Request Body:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `squad` | string | ✓ | Squad name |
| `event_type` | string | ✓ | One of: `squad.status`, `squad.alert`, `squad.heartbeat` |
| `data` | object | | Custom notification payload |

**Response:**

```json
{
  "squad": "mrrobot",
  "event_type": "squad.heartbeat",
  "created_at": "2025-01-20T14:30:00+00:00"
}
```

**curl Example:**

```bash
curl -X POST http://192.168.1.178:8765/api/v1/notifications \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "squad": "mrrobot",
    "event_type": "squad.heartbeat",
    "data": {
      "agents_active": 5,
      "tasks_completed": 12
    }
  }'
```

---

### Subscriptions (Event Webhooks)

#### GET /api/v1/subscriptions

List all event subscriptions, optionally filtered by subscriber or event type.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `subscriber` | string | Filter by subscriber name |
| `event_type` | string | Filter by event type |

**Response:**

```json
{
  "subscriptions": [
    {
      "id": "my-sub-1",
      "subscriber": "my-service",
      "url": "https://webhooks.example.com/opm-events",
      "event_type": "server.stats",
      "project": null,
      "interval_sec": 300,
      "enabled": 1,
      "last_fired_at": "2025-01-20T14:30:00+00:00",
      "created_at": "2025-01-15T10:00:00+00:00"
    }
  ]
}
```

**curl Example:**

```bash
# List all subscriptions
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/subscriptions

# Filter by subscriber
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/subscriptions?subscriber=my-service"
```

---

#### POST /api/v1/subscriptions

Create a new event subscription to receive periodic HTTP POST notifications at a webhook URL.

**Request Body:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | ✓ | Unique subscription ID |
| `subscriber` | string | ✓ | Subscriber/service name |
| `url` | string | ✓ | HTTPS webhook URL (must be publicly accessible) |
| `event_type` | string | ✓ | One of: `server.stats`, `server.health`, `project.summary` |
| `project` | string | | Optional: filter subscriptions to a specific project |
| `interval_sec` | integer | | Delivery interval in seconds (60–86400, default 300) |

**Response:**

```json
{
  "id": "my-sub-1",
  "subscriber": "my-service",
  "event_type": "server.stats",
  "project": null,
  "interval_sec": 300
}
```

**curl Example:**

```bash
curl -X POST http://192.168.1.178:8765/api/v1/subscriptions \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-sub-1",
    "subscriber": "my-service",
    "url": "https://webhooks.example.com/opm-events",
    "event_type": "server.stats",
    "interval_sec": 300
  }'
```

---

#### GET /api/v1/subscriptions/{id}

Get a single subscription.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Subscription ID |

**Response:**

```json
{
  "id": "my-sub-1",
  "subscriber": "my-service",
  "url": "https://webhooks.example.com/opm-events",
  "event_type": "server.stats",
  "project": null,
  "interval_sec": 300,
  "enabled": 1,
  "last_fired_at": "2025-01-20T14:30:00+00:00",
  "created_at": "2025-01-15T10:00:00+00:00"
}
```

**curl Example:**

```bash
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/subscriptions/my-sub-1
```

---

#### DELETE /api/v1/subscriptions/{id}

Delete a subscription. Requires explicit confirmation.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Subscription ID |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `confirm` | boolean | ✓ | Must be `true` to confirm deletion |

**Response:**

```json
{
  "id": "my-sub-1",
  "deleted": true
}
```

**curl Example:**

```bash
curl -X DELETE "http://192.168.1.178:8765/api/v1/subscriptions/my-sub-1?confirm=true" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

---

### Registration (Self-Service Token Management)

#### POST /api/v1/register

Self-service squad registration. Generate a new bearer token for a squad. Requires `OPM_REGISTRATION_KEY` to be set on the server.

**Rate Limit:** 5 requests per minute per IP address.

**Request Body:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `squad` | string | ✓ | Squad name (1–64 chars: letters, digits, hyphens, underscores) |
| `registration_key` | string | ✓ | Registration secret (must match `OPM_REGISTRATION_KEY` env var) |

**Response:**

```json
{
  "squad": "my-team",
  "token": "rL5T_cP9xK2mQ8nJvW3dY6aB4sE1fG7hI0",
  "note": "Store this token — it will not be shown again. Use it as a Bearer token in the Authorization header..."
}
```

**curl Example:**

```bash
curl -X POST http://192.168.1.178:8765/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{
    "squad": "my-team",
    "registration_key": "my-registration-secret"
  }'
```

---

#### DELETE /api/v1/register/{squad}

Revoke a squad's token and remove it from the system. Requires the registration key in the `X-Registration-Key` header.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `squad` | string | Squad name to deregister |

**Headers:**

| Header | Required | Notes |
|--------|----------|-------|
| `X-Registration-Key` | ✓ | Must match `OPM_REGISTRATION_KEY` env var |

**Response:**

HTTP 204 No Content (on success)

**curl Example:**

```bash
curl -X DELETE http://192.168.1.178:8765/api/v1/register/my-team \
  -H "X-Registration-Key: my-registration-secret"
```

---

### Server-Sent Events (SSE) Stream

#### GET /api/v1/events

Long-lived Server-Sent Events (SSE) connection for real-time task and server updates. Clients maintain this connection to receive events as they happen.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | string | Comma-separated list of event types to receive (optional; receives all if omitted) |
| `squad` | string | Filter events to a specific squad (optional) |

**Response:** `text/event-stream` (continuous stream)

Each event is formatted as:

```
data: {"type":"<event_type>","payload":{...}}\n\n
```

**Event Types:**

**Task Events:**
- `task.created` — A task was created
- `task.updated` — A task field was updated
- `task.completed` — A task was marked done
- `task.deleted` — A task was deleted

**Server Events:**
- `server.health` — Server heartbeat (emitted every 30 seconds)
- `queue.stats` — Task queue snapshot (emitted after changes)
- `squad.status` — A squad changed status
- `squad.alert` — A squad posted an alert
- `squad.heartbeat` — A squad posted a heartbeat

**Event Payload Structure:**

Task event example:
```json
{
  "type": "task.created",
  "payload": {
    "id": "auth-login-ui",
    "title": "Implement login form",
    "priority": "high",
    "status": "pending",
    "project": "frontend"
  }
}
```

Server health event example:
```json
{
  "type": "server.health",
  "payload": {
    "uptime_sec": 3600,
    "active_connections": 5,
    "memory_mb": 128
  }
}
```

**curl Example:**

```bash
# Subscribe to all events
curl -N -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/events

# Subscribe to specific events
curl -N -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/events?event_type=task.created,task.completed"

# Filter to a specific squad
curl -N -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  "http://192.168.1.178:8765/api/v1/events?squad=mrrobot"
```

**Connection Management:**

- **Timeout:** Client must send at least one event request per 30 seconds to maintain the connection. The server sends keepalive messages (`: keepalive\n\n`) if no events are available.
- **Reconnection:** If the connection drops, clients should reconnect with exponential backoff.
- **Buffering:** The server maintains a per-client event queue with a max of 100 events. Dropping a client connection forfeits undelivered events.

---

## Request/Response Examples

### Complete Task Creation Flow

```bash
# 1. Create a task
curl -X POST http://192.168.1.178:8765/api/v1/tasks \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "feature-x",
    "title": "Implement feature X",
    "priority": "high",
    "project": "backend",
    "assignee": "alice",
    "due_date": "2025-02-01"
  }'
# Response: {"id":"feature-x","status":"pending","priority":"high","project":"backend"}

# 2. Get the task
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/tasks/feature-x
# Response: {"id":"feature-x","title":"...","status":"pending",...}

# 3. Update task status
curl -X PATCH http://192.168.1.178:8765/api/v1/tasks/feature-x \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"status":"in_progress"}'
# Response: {"id":"feature-x","updated":["status"]}

# 4. Mark as complete
curl -X PATCH http://192.168.1.178:8765/api/v1/tasks/feature-x \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"status":"done"}'
# Response: {"id":"feature-x","updated":["status"]}

# 5. Delete the task
curl -X DELETE "http://192.168.1.178:8765/api/v1/tasks/feature-x?confirm=true" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
# Response: {"id":"feature-x","deleted":true}
```

### Real-Time Event Monitoring

```bash
# Subscribe to real-time events in one terminal
curl -N -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://192.168.1.178:8765/api/v1/events

# In another terminal, create a task and watch the SSE stream receive it
curl -X POST http://192.168.1.178:8765/api/v1/tasks \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"id":"test-1","title":"Test task","priority":"medium"}'

# The SSE stream will output:
# data: {"type":"task.created","payload":{"id":"test-1",...}}
# 
# data: {"type":"queue.stats","payload":{"by_status":{...}}}
```

---

## API Rate Limits

- **Registration endpoint** (`POST /api/v1/register`): 5 requests per minute per IP address
- **SSE connections:** Server supports up to `--max-connections` concurrent SSE clients (default 100)
- **Request body size:** All endpoints accept up to 1 MiB (1,048,576 bytes) request bodies

---

## Security Considerations

- **HTTPS:** In production, always transmit over TLS/HTTPS to prevent token interception
- **Token Storage:** Store tokens securely (e.g., environment variables, secure vaults). Never commit tokens to version control.
- **SSRF Protection:** Subscription webhook URLs are validated against RFC1918 and loopback address ranges. Public HTTPS URLs required for webhooks.
- **Signature Verification:** When using webhooks (via the MCP `register_webhook` tool), verify the `X-Hub-Signature-256` header to ensure payloads originated from OPM.

---

## Appendix: Troubleshooting

**401 Unauthorized**
- Verify `Authorization: Bearer <token>` header is present
- Confirm the token is valid and hasn't expired
- Check that the server was started with `OPM_TENANT_KEYS` or `OPM_REGISTRATION_KEY` set

**404 Not Found**
- REST API requires `--rest-api` flag at server startup
- Verify endpoint path and HTTP method are correct
- For resources (task, subscription), verify the resource ID exists

**409 Conflict**
- Task ID or subscription ID already exists
- Use `PATCH` to update an existing resource instead of `POST`

**413 Request Entity Too Large**
- Request body exceeds 1 MiB
- Reduce payload size or split into multiple requests

**429 Too Many Requests**
- Registration endpoint rate limited to 5 requests/min per IP
- Wait 60 seconds before retrying

**500 Internal Server Error**
- Check server logs for database errors
- Verify SQLite database file is accessible and not corrupted

