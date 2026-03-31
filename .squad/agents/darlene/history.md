# Darlene — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Backend Dev. I implement server.py and all MCP tools.

## Session Log

### 2026-03-31 — Initial implementation session

**Status:** Did not author code this session.

The coordinator (GitHub Copilot CLI) built `server.py` and all 11 tools directly, bypassing squad routing. Darlene was not invoked.

**What was built (by coordinator, on Darlene's behalf):**
- `src/open_project_manager_mcp/server.py`: `create_server(db_path)` factory, 11 tools as closures, `asyncio.Lock` for writes, SQLite via stdlib `sqlite3`
- Tools: `create_task`, `update_task`, `complete_task`, `delete_task`, `get_task`, `list_tasks`, `add_dependency`, `remove_dependency`, `list_ready_tasks`, `list_projects`, `get_stats`
- `human_approval=True` on `delete_task`
- Priority sort: critical > high > medium > low
- All 44 tool tests pass

**Process note:** In future sessions, `server.py` and tool work should be routed to Darlene.

### 2026-03-31 — Backend review (v0.1.0 review round)

**Task:** Review `server.py` and all tool implementations against CHARTER design principles.

**Fixes made:**
- `list_tasks`: was returning full row dicts → fixed to compact payload `(id, title, priority, status, assignee)` per CHARTER list-endpoint design principle
- `list_ready_tasks`: same full-row return issue → same compact fix applied
- `limit=0`: accepted and forwarded to SQLite `LIMIT 0`, silently returning no results → clamped to minimum 1
- Updated docstrings on both list tools to document the compact payload shape

### 2026-03-31 — Multi-tenant bearer token auth

**Task:** Implement OPM_TENANT_KEYS support (mirrors squad-knowledge-mcp pattern).

**Work completed:**
- `_load_tenant_keys()` in `__main__.py` — reads `OPM_TENANT_KEYS` env var, normalizes old/new formats into `{squad: token}` dict
- `ApiKeyVerifier` class in `server.py` — constant-time `hmac.compare_digest` Bearer token validation
- `AuthSettings` wired into `FastMCP` when `tenant_keys` provided
- `create_server()` updated to accept `tenant_keys: dict[str, str] | None` and `server_url: str | None`
- `--generate-token SQUAD_NAME` CLI flag — prints cryptographically secure token + setup instructions, exits
- `_check_network_auth()` updated with `tenant_keys` parameter for context-aware warnings

**Test results:** +11 tests → 81/81 passing.

## Learnings
