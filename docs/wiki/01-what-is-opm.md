# What is OPM?

Open Project Manager (OPM) is a **SQLite-backed, persistent task queue designed for AI agent squads**.

## Core Purpose

OPM gives agent teams a **local-first, fast, dependency-aware task management system** without external databases or APIs. Tasks are prioritized, tracked, searchable, and can be subscribed to in real-time via Server-Sent Events (SSE).

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  MCP Clients (AI agents, tools, CLI)                   │
│  • Claude, local tools, squad members                  │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
    Stdio (single)            HTTP (multi-client)
    • Local tools              • 192.168.1.178:8765
    • Fast, low latency       • Shared LAN server
                              • SSE stream
                              • REST API
        │                             │
        └──────────────┬──────────────┘
                       │
    ┌──────────────────▼──────────────────┐
    │  FastMCP Server (Python)             │
    │  • Task CRUD (create, update, list)  │
    │  • Dependency tracking              │
    │  • Team status & events             │
    │  • Webhooks & subscriptions         │
    └──────────────────┬──────────────────┘
                       │
    ┌──────────────────▼──────────────────┐
    │  SQLite Database (opm.db)           │
    │  • tasks table                      │
    │  • task_deps (dependencies)         │
    │  • activity_log (audit trail)       │
    │  • team_status (squad coordination) │
    │  • team_events (messaging)          │
    │  • webhooks (event subscriptions)   │
    └─────────────────────────────────────┘
```

## Key Concepts

### Task Queue

- **Local-first:** SQLite database, no external services
- **Persistent:** Data survives server restarts
- **Prioritized:** `critical` → `high` → `medium` → `low`
- **Searchable:** Full-text search (FTS5) across title, description, tags
- **Audited:** Every change logged with actor, timestamp, old/new values

### Dependency Tracking

Tasks can block each other. Use `list_ready_tasks` to get all tasks with no unresolved blockers — safe to execute without manual dependency resolution.

### Multi-Transport

| Transport | Use case | Clients |
|-----------|----------|---------|
| **Stdio** | Local, single client | CLI tools, local dev |
| **HTTP** | Shared LAN server | Multiple squads, remote agents |

### MCP Protocol

OPM implements the **Model Context Protocol (MCP)**, the Anthropic standard for tool servers. Clients call tools via:

- **Stdio:** Direct subprocess communication
- **HTTP:** JSON-RPC requests over HTTP Bearer token auth

### Team Coordination

Beyond task management, OPM enables **cross-squad coordination**:

- **Team Status:** Each squad broadcasts `online`, `offline`, `busy`, or `degraded`
- **Team Events:** Custom events (milestones, errors, deployments) from each squad
- **SSE Stream:** Real-time notifications of task + team changes
- **Subscriptions:** Webhooks for periodic event delivery

## Why SQLite?

- **Zero dependencies:** Built-in to Python
- **ACID transactions:** Safe multi-client access
- **Fast FTS5 search:** Index thousands of tasks instantly
- **Portable:** Single file, no service to manage
- **Production-ready:** Used by millions of applications

## How OPM Fits Into Multi-Squad LAN Setup

Imagine a LAN with 5 agent squads:

```
mrrobot         westworld        fsociety        coordinator    ralph
  ◆ Squad        ◆ Squad          ◆ Squad          ◆ Squad      ◆ Squad
  │              │                │                │             │
  └──────────────┴────────────────┴────────────────┴─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │ OPM Server (skitterphuger) │
                    │ 192.168.1.178:8765        │
                    │ • HTTP+SSE                │
                    │ • Bearer tokens           │
                    │ • Multi-tenant            │
                    └──────────────────────────┘
```

Each squad:
1. Gets a unique **bearer token** from OPM admin
2. Adds OPM to their `mcp-config.json` with that token
3. Connects to `http://192.168.1.178:8765/mcp`
4. Uses OPM tools: `create_task`, `list_ready_tasks`, `set_team_status`, etc.
5. Receives real-time events via `GET /api/v1/events` (SSE stream)

All squads share the same task database, see each other's status, and can subscribe to cross-squad events.

## Transport Details

### Stdio (Local)

```bash
# In .mcp-config.json
{
  "mcpServers": {
    "local-opm": {
      "command": "uvx",
      "args": ["open-project-manager-mcp"]
    }
  }
}
```

Runs OPM as a subprocess on your machine. No network access, no auth needed.

### HTTP (LAN Server)

```bash
# Start server
OPM_TENANT_KEYS='{"squad":{"key":"token"}}' \
  open-project-manager-mcp --http --host 0.0.0.0 --port 8765
```

OPM listens on port 8765. Clients authenticate with Bearer tokens and connect via:

```json
{
  "mcpServers": {
    "shared-opm": {
      "url": "http://192.168.1.178:8765/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

### REST API + SSE

When `--rest-api` is enabled, HTTP clients can also:

- **REST:** `GET /api/v1/tasks`, `POST /api/v1/tasks`, etc.
- **SSE Stream:** `GET /api/v1/events` for real-time task + team events
- **Team API:** Set status, push events, manage subscriptions

## Tools at a Glance

| Category | Tools |
|----------|-------|
| **Task CRUD** | `create_task`, `update_task`, `get_task`, `list_tasks`, `delete_task`, `complete_task` |
| **Dependencies** | `add_dependency`, `remove_dependency`, `list_ready_tasks` |
| **Bulk ops** | `create_tasks`, `update_tasks`, `complete_tasks` |
| **Search** | `search_tasks` |
| **Due dates** | `list_overdue_tasks`, `list_due_soon_tasks` |
| **Projects** | `list_projects`, `get_project_summary` |
| **Activity** | `get_task_activity`, `get_activity_log` |
| **Export/Import** | `export_all_tasks`, `import_tasks` |
| **Webhooks** | `register_webhook`, `list_webhooks`, `delete_webhook` |
| **Team Status** | `set_team_status`, `get_team_status` |
| **Team Events** | `post_team_event`, `get_team_events` |
| **Subscriptions** | `subscribe_events`, `list_subscriptions`, `unsubscribe_events` |
| **Stats** | `get_server_stats`, `get_stats` |

See [MCP Tools Reference](03-mcp-tools-reference.md) for complete details on all tools.

## Zero External Dependencies

OPM requires only:
- Python 3.9+
- `mcp>=1.0` (for MCP protocol)
- `platformdirs>=3.0` (for data dir)
- *(Optional)* `fastapi`, `uvicorn`, `httpx` for HTTP/SSE/webhooks

No PostgreSQL, no Redis, no external message queue. All data lives in SQLite.

---

**Next:** [Quickstart](02-quickstart.md) — Connect in 5 minutes.
