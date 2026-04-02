# Messaging System

How OPM's real-time messaging works: SSE events, team status, subscriptions, and event streaming.

## Overview

OPM sends real-time notifications via **Server-Sent Events (SSE)** on the `/api/v1/events` endpoint. Clients open a long-lived HTTP connection and receive JSON events as they occur.

```
┌──────────────┐
│ MCP Client   │
│              │
│ curl GET     │
│ /events?...  │
└──────┬───────┘
       │ HTTP 200 (stream)
       │
       ├─ event: task.created
       │ data: {"id": "...", "title": "..."}
       │
       ├─ event: server.health
       │ data: {"uptime": 3600, "connections": 5}
       │
       └─ event: team.status_changed
         data: {"squad": "mrrobot", "status": "online"}
```

## Connecting to the Event Stream

### Basic Connection

```bash
curl -N -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/events
```

**Flags:**
- `-N` — Disable buffering (show events as they arrive)
- `-H "Authorization: Bearer ..."` — Your squad's bearer token
- (Optional) `?event_type=...` — Filter events
- (Optional) `?squad=...` — Filter by squad

### Keep the Stream Open

The `-N` flag is essential; without it, `curl` buffers output and you won't see events in real-time. The stream stays open indefinitely; the server sends a heartbeat every 30 seconds if no events occur.

## Event Types

### Task Events

Fired when tasks are created, updated, completed, or deleted.

#### `task.created`

A task was created.

```json
{
  "event": "task.created",
  "task_id": "auth-login-ui",
  "timestamp": "2025-01-20T14:30:00+00:00",
  "data": {
    "id": "auth-login-ui",
    "title": "Implement login UI",
    "priority": "high",
    "project": "frontend",
    "status": "pending",
    "created_at": "2025-01-20T14:30:00Z",
    "updated_at": "2025-01-20T14:30:00Z"
  }
}
```

#### `task.updated`

A task field was changed.

```json
{
  "event": "task.updated",
  "task_id": "auth-login-ui",
  "timestamp": "2025-01-20T14:31:00+00:00",
  "data": {
    "id": "auth-login-ui",
    "title": "Implement login UI",
    "status": "in_progress",
    "changed_fields": ["status"]
  }
}
```

#### `task.completed`

A task was marked done.

```json
{
  "event": "task.completed",
  "task_id": "auth-login-ui",
  "timestamp": "2025-01-20T14:35:00+00:00",
  "data": {
    "id": "auth-login-ui",
    "completed_at": "2025-01-20T14:35:00Z"
  }
}
```

#### `task.deleted`

A task was deleted.

```json
{
  "event": "task.deleted",
  "task_id": "auth-login-ui",
  "timestamp": "2025-01-20T14:40:00+00:00",
  "data": {
    "id": "auth-login-ui"
  }
}
```

### Server Events

Server health and queue statistics.

#### `server.health`

Heartbeat emitted every 30 seconds. Includes uptime, connections, memory stats.

```json
{
  "event": "server.health",
  "timestamp": "2025-01-20T14:30:30+00:00",
  "data": {
    "uptime_seconds": 3600,
    "active_connections": 5,
    "memory_mb": 45.2
  }
}
```

#### `queue.stats`

Task queue snapshot (emitted after state changes).

```json
{
  "event": "queue.stats",
  "timestamp": "2025-01-20T14:31:00+00:00",
  "data": {
    "total_tasks": 42,
    "tasks_by_status": {
      "pending": 10,
      "in_progress": 5,
      "done": 25,
      "blocked": 2
    },
    "tasks_by_priority": {
      "critical": 1,
      "high": 3,
      "medium": 20,
      "low": 18
    }
  }
}
```

### Team Events

Cross-squad coordination: status changes and custom events.

#### `team.status_changed`

A squad changed its status (`online`, `offline`, `busy`, `degraded`).

```json
{
  "event": "team.status_changed",
  "timestamp": "2025-01-20T14:30:00+00:00",
  "data": {
    "squad": "mrrobot",
    "status": "busy",
    "message": "Deploying v2.1",
    "updated_at": "2025-01-20T14:30:00Z"
  }
}
```

#### `team.event`

A squad published a custom event (milestone, error, deployment, etc.).

```json
{
  "event": "team.event",
  "timestamp": "2025-01-20T14:30:00+00:00",
  "data": {
    "squad": "mrrobot",
    "event_type": "milestone",
    "event_data": {
      "milestone": "v2.1-release",
      "completed_at": "2025-01-20T14:30:00Z"
    },
    "created_at": "2025-01-20T14:30:00Z"
  }
}
```

### Notification Events

#### `notification.received`

A squad posted a notification (deprecated in favor of `team.event`; kept for backward compatibility).

```json
{
  "event": "notification.received",
  "timestamp": "2025-01-20T14:30:00+00:00",
  "data": {
    "squad": "coordinator",
    "message": "All tests passing",
    "timestamp": "2025-01-20T14:30:00Z"
  }
}
```

## Welcome Event

When you first connect to the event stream, the server sends a welcome event:

```json
{
  "event": "welcome",
  "data": {
    "squad": "mrrobot",
    "connected_at": "2025-01-20T14:30:00Z",
    "active_squads": ["mrrobot", "westworld", "fsociety"],
    "server_uptime": 3600
  }
}
```

## Filtering Events

### By Event Type

Only receive specific event types:

```bash
curl -N "http://192.168.1.178:8765/api/v1/events?event_type=task.created,task.completed" \
  -H "Authorization: Bearer <token>"
```

**Multiple types:** Comma-separated, no spaces.

### By Squad

Only receive events from a specific squad:

```bash
curl -N "http://192.168.1.178:8765/api/v1/events?squad=coordinator" \
  -H "Authorization: Bearer <token>"
```

### Combining Filters

```bash
curl -N "http://192.168.1.178:8765/api/v1/events?event_type=task.created&squad=frontend" \
  -H "Authorization: Bearer <token>"
```

## Event Format (SSE)

Events are streamed in `text/event-stream` format:

```
event: task.created
data: {"id": "...", "title": "...", ...}

event: server.health
data: {"uptime_seconds": 3600, ...}

: heartbeat comment

event: team.status_changed
data: {...}
```

**Key points:**
- Each event is separated by a blank line
- `event:` line defines the event type
- `data:` line contains the JSON payload
- `:` lines are comments (heartbeat pings)
- Stream stays open indefinitely

## SSE vs Polling

### SSE (Server-Sent Events) — Real-time Push

**Use when:** You need instant notifications of task changes, team status, or events.

```bash
curl -N -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/events
```

**Advantages:**
- Real-time (sub-second latency)
- Efficient (single connection, no polling overhead)
- Bi-directional messaging ready

**Disadvantages:**
- Requires long-lived connection
- Firewall may block or timeout

### Polling — Request-Response

**Use when:** Real-time notifications aren't critical, or firewalls block SSE.

```bash
# Poll for server stats every 5 seconds
curl -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/stats

# Poll for task updates every 10 seconds
curl -H "Authorization: Bearer <token>" \
  'http://192.168.1.178:8765/api/v1/tasks?status=in_progress'
```

**Advantages:**
- Simple HTTP GET requests
- Works behind restrictive firewalls
- No connection state

**Disadvantages:**
- Latency (depends on poll interval)
- Inefficient (many requests for small changes)
- Higher server load

**Polling interval recommendation:**
- `5–10 seconds` for task updates
- `30 seconds` for server stats
- `60 seconds` for team status

## Practical Example — Watch for Task Completions

```bash
#!/bin/bash
TOKEN="opm_abc123def456..."

curl -N -H "Authorization: Bearer $TOKEN" \
  "http://192.168.1.178:8765/api/v1/events?event_type=task.completed" \
| while IFS= read -r line; do
  if [[ $line == "data: "* ]]; then
    task_data="${line#data: }"
    task_id=$(echo "$task_data" | jq -r '.id')
    echo "✓ Task completed: $task_id"
  fi
done
```

This script watches for `task.completed` events and prints the task ID as soon as it's marked done.

## Subscriptions (Webhooks-Style)

For periodic event delivery at an HTTPS endpoint:

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

OPM will periodically send `task.completed` events from the `coordinator` squad to your webhook URL via HTTP POST.

---

**Next:** [Auth and Tokens](06-auth-and-tokens.md) — How to manage bearer tokens.
