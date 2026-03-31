# open-project-manager-mcp

SQLite-backed project management MCP server for AI agent squads.

## What it does

Gives agent squads a persistent, prioritized task queue with dependency tracking — local-first, fast, and zero external dependencies beyond Python + FastMCP.

**11 tools:**

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

## Configuration

| CLI flag | Env var | Default |
|----------|---------|---------|
| `--db-path` | `OPM_DB_PATH` | Platform data dir |
| `--host` | `OPM_HOST` | `127.0.0.1` |
| `--port` | `OPM_PORT` | `8765` |
| `--max-connections` | `OPM_MAX_CONNECTIONS` | `100` |

## Development

```bash
pip install -e '.[dev]'
pytest
```

## Architecture

- **Backend:** SQLite via Python stdlib `sqlite3` — no ORM, no external DB
- **Framework:** FastMCP
- **Transport:** stdio (default) + `--http` / `--sse` for multi-agent LAN access
- **Schema:** Two tables — `tasks` and `task_deps` — created at startup

```
open-project-manager-mcp/
├── src/open_project_manager_mcp/
│   ├── __main__.py   # CLI entry, transport config
│   └── server.py     # create_server(db_path) factory, all tools as closures
└── tests/
    ├── test_tools.py
    └── test_config.py
```
