# MCP Tools Reference

Complete documentation for all 24 OPM MCP tools.

---

## Task Management

### create_task

Create a new task with a caller-supplied ID.

**Parameters:**
- `id` (string, required) — Unique task identifier (slug, e.g., `auth-login-ui`)
- `title` (string, required) — Short title
- `description` (string) — Detailed description (up to 50,000 chars)
- `priority` (string) — `critical`, `high`, `medium`, `low` (default: `medium`)
- `project` (string) — Project name (default: `default`)
- `assignee` (string) — Assigned to (person/team name)
- `tags` (array of strings) — Labels for organization
- `due_date` (string) — ISO 8601 or `YYYY-MM-DD` format

**Returns:**
```json
{
  "id": "auth-login-ui",
  "title": "Implement login UI",
  "description": "...",
  "priority": "high",
  "project": "frontend",
  "status": "pending",
  "assignee": "alice",
  "tags": ["ui", "auth"],
  "due_date": "2025-02-01",
  "created_at": "2025-01-20T14:30:00Z",
  "updated_at": "2025-01-20T14:30:00Z"
}
```

**Example:**
```bash
create_task(
  id="auth-login-ui",
  title="Implement login UI",
  priority="high",
  project="frontend",
  assignee="alice",
  tags=["ui", "auth"],
  due_date="2025-02-01"
)
```

---

### update_task

Update one or more fields of an existing task.

**Parameters:**
- `task_id` (string, required) — ID of task to update
- `title` (string) — New title
- `description` (string) — New description
- `priority` (string) — New priority
- `project` (string) — New project
- `status` (string) — `pending`, `in_progress`, `done`, `blocked`
- `assignee` (string) — New assignee
- `tags` (array) — New tags
- `due_date` (string) — New due date

**Returns:**
```json
{
  "id": "auth-login-ui",
  "title": "...",
  "status": "in_progress",
  "updated_at": "2025-01-20T14:31:00Z"
}
```

**Example:**
```bash
update_task(
  task_id="auth-login-ui",
  status="in_progress",
  priority="critical"
)
```

---

### get_task

Fetch a single task by ID, including dependency info.

**Parameters:**
- `id` (string, required) — Task ID

**Returns:**
```json
{
  "id": "auth-login-ui",
  "title": "...",
  "status": "pending",
  "priority": "high",
  "project": "frontend",
  "depends_on": ["auth-api"],
  "blocked_by": ["db-schema-ready"],
  "created_at": "2025-01-20T14:30:00Z",
  "updated_at": "2025-01-20T14:30:00Z"
}
```

**Example:**
```bash
get_task(id="auth-login-ui")
```

---

### list_tasks

List all tasks with optional filtering and pagination.

**Parameters:**
- `project` (string) — Filter by project
- `assignee` (string) — Filter by assignee
- `status` (string) — Filter by status
- `priority` (string) — Filter by priority
- `limit` (int) — Page size (1–500, default 20)
- `offset` (int) — Pagination offset (default 0)

**Returns:**
```json
{
  "tasks": [
    { "id": "auth-login-ui", "title": "...", "status": "pending", "priority": "high" },
    { "id": "db-schema-ready", "title": "...", "status": "done", "priority": "medium" }
  ],
  "has_more": false
}
```

**Example:**
```bash
list_tasks(
  project="frontend",
  status="pending",
  priority="high",
  limit=50
)
```

---

### complete_task

Mark a task as done.

**Parameters:**
- `id` (string, required) — Task ID

**Returns:**
```json
{
  "id": "auth-login-ui",
  "status": "done",
  "completed_at": "2025-01-20T14:31:00Z"
}
```

**Example:**
```bash
complete_task(id="auth-login-ui")
```

---

### delete_task

Delete a task permanently.

**Parameters:**
- `id` (string, required) — Task ID
- `human_approval` (boolean) — Must be `true` to confirm deletion

**Returns:**
```json
{
  "id": "auth-login-ui",
  "deleted": true
}
```

**Example:**
```bash
delete_task(id="auth-login-ui", human_approval=true)
```

---

### fail_task

Mark a task as failed (blocked or error state).

**Parameters:**
- `id` (string, required) — Task ID
- `reason` (string) — Reason for failure

**Returns:**
```json
{
  "id": "auth-login-ui",
  "status": "blocked",
  "reason": "Database migration failed"
}
```

**Example:**
```bash
fail_task(id="auth-login-ui", reason="Database migration failed")
```

---

### block_task

Block a task (mark as blocked).

**Parameters:**
- `id` (string, required) — Task ID
- `reason` (string) — Reason for blocking

**Returns:**
```json
{
  "id": "auth-login-ui",
  "status": "blocked",
  "reason": "Waiting for API design review"
}
```

**Example:**
```bash
block_task(id="auth-login-ui", reason="Waiting for API design review")
```

---

## Dependency Management

### add_dependency

Mark task A as blocked by task B.

**Parameters:**
- `task_id` (string, required) — Task that will be blocked
- `depends_on` (string, required) — Task that must complete first

**Returns:**
```json
{
  "task_id": "auth-login-ui",
  "depends_on": "db-schema-ready",
  "added": true
}
```

**Example:**
```bash
add_dependency(task_id="auth-login-ui", depends_on="db-schema-ready")
```

---

### remove_dependency

Remove a dependency edge.

**Parameters:**
- `task_id` (string, required) — Task ID
- `depends_on` (string, required) — Dependency to remove

**Returns:**
```json
{
  "task_id": "auth-login-ui",
  "depends_on": "db-schema-ready",
  "removed": true
}
```

**Example:**
```bash
remove_dependency(task_id="auth-login-ui", depends_on="db-schema-ready")
```

---

### list_ready_tasks

List tasks with no unresolved blockers — safe to execute.

**Parameters:**
- `limit` (int) — Maximum results (default 20)
- `offset` (int) — Pagination offset (default 0)

**Returns:**
```json
{
  "tasks": [
    { "id": "auth-setup", "title": "Set up auth", "status": "pending" },
    { "id": "tests-pass", "title": "All tests passing", "status": "pending" }
  ],
  "has_more": false
}
```

**Example:**
```bash
list_ready_tasks(limit=50)
```

---

## Search

### search_tasks

Full-text search across task title, description, and tags.

**Parameters:**
- `query` (string, required) — Search query
- `project` (string) — Filter by project
- `status` (string) — Filter by status
- `limit` (int) — Results per page (default 20)

**Returns:**
```json
{
  "results": [
    { "id": "auth-login-ui", "title": "...", "rank": 0.95 },
    { "id": "auth-api", "title": "...", "rank": 0.87 }
  ],
  "count": 2
}
```

**Example:**
```bash
search_tasks(query="auth", project="frontend", limit=50)
```

---

## Bulk Operations

### create_tasks

Bulk-create up to 50 tasks in one transaction.

**Parameters:**
- `tasks` (array of task objects, required) — Array of tasks (same shape as `create_task`)

**Returns:**
```json
{
  "created": [
    { "id": "task1", "title": "...", "status": "pending" },
    { "id": "task2", "title": "...", "status": "pending" }
  ],
  "errors": []
}
```

**Example:**
```bash
create_tasks(tasks=[
  { "id": "task1", "title": "Task 1", "priority": "high" },
  { "id": "task2", "title": "Task 2", "priority": "medium" }
])
```

---

### update_tasks

Bulk-update up to 50 tasks in one transaction.

**Parameters:**
- `updates` (array of update objects, required) — Each with required `task_id` + optional fields

**Returns:**
```json
{
  "updated": [
    { "id": "task1", "status": "in_progress" },
    { "id": "task2", "status": "done" }
  ],
  "errors": []
}
```

**Example:**
```bash
update_tasks(updates=[
  { "task_id": "task1", "status": "in_progress" },
  { "task_id": "task2", "status": "done" }
])
```

---

### complete_tasks

Bulk-mark up to 50 tasks as done.

**Parameters:**
- `ids` (array of strings, required) — Task IDs to complete

**Returns:**
```json
{
  "completed": ["task1", "task2"],
  "errors": []
}
```

**Example:**
```bash
complete_tasks(ids=["task1", "task2", "task3"])
```

---

## Due Dates

### list_overdue_tasks

Tasks past their `due_date` that are not done.

**Parameters:**
- `project` (string) — Filter by project
- `assignee` (string) — Filter by assignee
- `limit` (int) — Maximum results (default 20)

**Returns:**
```json
{
  "tasks": [
    { "id": "auth-login-ui", "title": "...", "due_date": "2025-01-15", "days_overdue": 5 }
  ],
  "count": 1
}
```

**Example:**
```bash
list_overdue_tasks(project="frontend", limit=50)
```

---

### list_due_soon_tasks

Tasks due within the next N days.

**Parameters:**
- `days` (int) — Lookahead (1–365, default 7)
- `project` (string) — Filter by project
- `assignee` (string) — Filter by assignee
- `limit` (int) — Maximum results (default 20)

**Returns:**
```json
{
  "tasks": [
    { "id": "auth-login-ui", "title": "...", "due_date": "2025-01-25", "days_remaining": 5 }
  ],
  "count": 1
}
```

**Example:**
```bash
list_due_soon_tasks(days=14, project="frontend", limit=50)
```

---

## Projects

### list_projects

All projects with open/total task counts.

**Parameters:** (none)

**Returns:**
```json
{
  "projects": [
    { "name": "frontend", "open": 5, "total": 12 },
    { "name": "backend", "open": 3, "total": 8 }
  ]
}
```

**Example:**
```bash
list_projects()
```

---

### get_project_summary

Per-project task summary with counts and oldest open item.

**Parameters:**
- `project` (string, required) — Project name

**Returns:**
```json
{
  "project": "frontend",
  "total": 12,
  "pending": 5,
  "in_progress": 2,
  "done": 4,
  "blocked": 1,
  "overdue": 1,
  "oldest_open_age": "3d 5h"
}
```

**Example:**
```bash
get_project_summary(project="frontend")
```

---

## Activity

### get_task_activity

Audit trail for a single task.

**Parameters:**
- `task_id` (string, required) — Task ID
- `limit` (int) — Maximum results (default 50, max 200)

**Returns:**
```json
{
  "entries": [
    {
      "id": 1,
      "action": "created",
      "field": null,
      "old_value": null,
      "new_value": null,
      "actor": "alice",
      "created_at": "2025-01-20T14:30:00Z"
    },
    {
      "id": 2,
      "action": "updated",
      "field": "status",
      "old_value": "pending",
      "new_value": "in_progress",
      "actor": "bob",
      "created_at": "2025-01-20T14:35:00Z"
    }
  ]
}
```

**Example:**
```bash
get_task_activity(task_id="auth-login-ui", limit=100)
```

---

### get_activity_log

Recent activity across all tasks or a single project.

**Parameters:**
- `project` (string) — Filter by project
- `limit` (int) — Maximum results (default 50, max 200)

**Returns:**
```json
{
  "entries": [
    { "id": 5, "task_id": "auth-login-ui", "action": "updated", "field": "status", "old_value": "pending", "new_value": "in_progress", "actor": "alice", "created_at": "2025-01-20T14:40:00Z" },
    { "id": 4, "task_id": "db-schema-ready", "action": "completed", "field": null, "old_value": null, "new_value": null, "actor": "bob", "created_at": "2025-01-20T14:35:00Z" }
  ]
}
```

**Example:**
```bash
get_activity_log(project="frontend", limit=100)
```

---

## Export / Import

### export_all_tasks

Export all tasks + dependency edges as a portable JSON string.

**Parameters:**
- `project` (string) — Filter to a specific project

**Returns:**
```json
{
  "tasks": [
    { "id": "auth-login-ui", "title": "...", "priority": "high", "status": "pending" }
  ],
  "dependencies": [
    { "task_id": "auth-login-ui", "depends_on": "db-schema-ready" }
  ]
}
```

**Example:**
```bash
export_all_tasks(project="frontend")
```

---

### import_tasks

Import tasks from `export_all_tasks` output.

**Parameters:**
- `data` (string, required) — JSON string from `export_all_tasks`
- `merge` (boolean) — `true` to skip existing IDs, `false` to abort on conflict (default: false)

**Returns:**
```json
{
  "imported": 5,
  "skipped": 0,
  "errors": []
}
```

**Example:**
```bash
import_tasks(data='{"tasks": [...], "dependencies": [...]}', merge=false)
```

---

## Webhooks

### register_webhook

Register an HTTPS webhook to receive event notifications.

**Parameters:**
- `id` (string, required) — Unique webhook ID
- `url` (string, required) — HTTPS endpoint (public IP, no RFC-1918)
- `events` (array, required) — `["task.created", "task.completed"]` etc.
- `project` (string) — Restrict to a specific project (optional)
- `secret` (string) — HMAC secret for signature verification (optional but recommended)

**Returns:**
```json
{
  "id": "my-webhook",
  "url": "https://hooks.example.com/opm",
  "events": ["task.created", "task.completed"],
  "project": "frontend",
  "created_at": "2025-01-20T14:30:00Z"
}
```

**Example:**
```bash
register_webhook(
  id="my-webhook",
  url="https://hooks.example.com/opm",
  events=["task.created", "task.completed"],
  project="frontend",
  secret="s3cr3t"
)
```

---

### list_webhooks

List all webhooks (secrets never returned).

**Parameters:**
- `project` (string) — Filter by project

**Returns:**
```json
{
  "webhooks": [
    { "id": "my-webhook", "url": "https://...", "events": [...], "project": "frontend", "created_at": "..." }
  ]
}
```

**Example:**
```bash
list_webhooks(project="frontend")
```

---

### delete_webhook

Delete a webhook registration.

**Parameters:**
- `id` (string, required) — Webhook ID
- `human_approval` (boolean, required) — Must be `true` to confirm deletion

**Returns:**
```json
{
  "id": "my-webhook",
  "deleted": true
}
```

**Example:**
```bash
delete_webhook(id="my-webhook", human_approval=true)
```

---

## Team Status & Coordination

### set_team_status

Set your squad's status for cross-team visibility.

**Parameters:**
- `status` (string, required) — `online`, `offline`, `busy`, or `degraded`
- `message` (string) — Optional status message (e.g., "Deploying v2.1")

**Returns:**
```json
{
  "squad": "mrrobot",
  "status": "busy",
  "message": "Deploying v2.1",
  "updated_at": "2025-01-20T14:30:00Z"
}
```

**Example:**
```bash
set_team_status(status="busy", message="Deploying v2.1")
```

---

### get_team_status

Get all teams' status or a specific team's status.

**Parameters:**
- `squad` (string) — Optional; if provided, fetch specific squad's status

**Returns:**
```json
{
  "squads": {
    "mrrobot": { "status": "busy", "message": "Deploying v2.1", "updated_at": "2025-01-20T14:30:00Z" },
    "westworld": { "status": "online", "message": "", "updated_at": "2025-01-20T14:25:00Z" }
  }
}
```

**Example:**
```bash
# All teams
get_team_status()

# Specific team
get_team_status(squad="mrrobot")
```

---

### post_team_event

Push a custom event from your squad.

**Parameters:**
- `event_type` (string, required) — Event type (e.g., `milestone`, `error`, `deployment`)
- `data` (object) — Event-specific data

**Returns:**
```json
{
  "squad": "mrrobot",
  "event_type": "milestone",
  "data": { "milestone": "v2.1-release" },
  "created_at": "2025-01-20T14:30:00Z"
}
```

**Example:**
```bash
post_team_event(
  event_type="milestone",
  data={"milestone": "v2.1-release", "completed_at": "2025-01-20T14:30:00Z"}
)
```

---

### get_team_events

Query team events with optional filters.

**Parameters:**
- `squad` (string) — Filter by squad
- `event_type` (string) — Filter by event type
- `since` (string) — ISO 8601 timestamp; return events after this time
- `limit` (int) — Maximum results (default 100)

**Returns:**
```json
{
  "events": [
    { "squad": "mrrobot", "event_type": "milestone", "data": {...}, "created_at": "2025-01-20T14:30:00Z" }
  ]
}
```

**Example:**
```bash
get_team_events(squad="mrrobot", event_type="milestone", limit=50)
```

---

## Event Subscriptions

### subscribe_events

Subscribe to periodic event delivery at an HTTPS endpoint.

**Parameters:**
- `id` (string, required) — Subscription ID
- `subscriber` (string, required) — Name of subscribing service
- `url` (string, required) — HTTPS endpoint for callbacks
- `event_type` (string, required) — Event type to subscribe to
- `squad` (string) — Filter to specific squad (optional)

**Returns:**
```json
{
  "id": "my-sub-1",
  "subscriber": "my-service",
  "url": "https://webhooks.example.com/opm-events",
  "event_type": "task.completed",
  "squad": "coordinator",
  "created_at": "2025-01-20T14:30:00Z"
}
```

**Example:**
```bash
subscribe_events(
  id="my-sub-1",
  subscriber="my-service",
  url="https://webhooks.example.com/opm-events",
  event_type="task.completed",
  squad="coordinator"
)
```

---

### list_subscriptions

List active event subscriptions.

**Parameters:**
- `subscriber` (string) — Filter by subscriber name

**Returns:**
```json
{
  "subscriptions": [
    { "id": "my-sub-1", "subscriber": "my-service", "url": "https://...", "event_type": "task.completed", "squad": "coordinator", "created_at": "..." }
  ]
}
```

**Example:**
```bash
list_subscriptions(subscriber="my-service")
```

---

### unsubscribe_events

Remove a subscription.

**Parameters:**
- `id` (string, required) — Subscription ID
- `human_approval` (boolean, required) — Must be `true` to confirm

**Returns:**
```json
{
  "id": "my-sub-1",
  "unsubscribed": true
}
```

**Example:**
```bash
unsubscribe_events(id="my-sub-1", human_approval=true)
```

---

## Server Stats

### get_server_stats

Fetch server statistics: task counts, uptime, active SSE connections.

**Parameters:** (none)

**Returns:**
```json
{
  "uptime_seconds": 3600,
  "active_connections": 5,
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
```

**Example:**
```bash
get_server_stats()
```

---

### get_stats

Alias for `get_server_stats`.

**Example:**
```bash
get_stats()
```

---

## Summary

| Category | Tool Count | Tools |
|----------|------------|-------|
| Task CRUD | 6 | `create_task`, `update_task`, `get_task`, `list_tasks`, `complete_task`, `delete_task` |
| Task State | 2 | `fail_task`, `block_task` |
| Dependencies | 3 | `add_dependency`, `remove_dependency`, `list_ready_tasks` |
| Search | 1 | `search_tasks` |
| Bulk Ops | 3 | `create_tasks`, `update_tasks`, `complete_tasks` |
| Due Dates | 2 | `list_overdue_tasks`, `list_due_soon_tasks` |
| Projects | 2 | `list_projects`, `get_project_summary` |
| Activity | 2 | `get_task_activity`, `get_activity_log` |
| Export/Import | 2 | `export_all_tasks`, `import_tasks` |
| Webhooks | 3 | `register_webhook`, `list_webhooks`, `delete_webhook` |
| Team Status | 2 | `set_team_status`, `get_team_status` |
| Team Events | 2 | `post_team_event`, `get_team_events` |
| Subscriptions | 3 | `subscribe_events`, `list_subscriptions`, `unsubscribe_events` |
| Server Stats | 2 | `get_server_stats`, `get_stats` |
| **Total** | **34** | — |

---

**Next:** [REST API Reference](04-rest-api-reference.md) (Mobley).
