# Decisions

## 2026-03-31: Project bootstrapped

**Decision:** Build open-project-manager-mcp as a standalone SQLite-backed FastMCP server.
**Rationale:** squad-knowledge-mcp uses ChromaDB (wrong fit for ordered mutable task state). SQLite is the right tool for a task queue.
**Patterns to follow:** Mirror squad-knowledge-mcp's `create_server(db_path)` factory pattern, closure-based tools, stdio+TCP transport.

## 2026-03-31: Caller-supplied task IDs

**Decision:** Task IDs are caller-supplied strings (e.g., "auth-login-ui"), not auto-generated UUIDs.
**Rationale:** Agent-friendly — meaningful IDs are easier to reference in tool calls than opaque UUIDs.

## 2026-03-31: Architecture patterns confirmed (from Elliot)

*Merged from inbox: elliot-architecture-confirmed.md*

**By:** Elliot (via cross-squad query to squad-knowledge-mcp team)

**Findings:**

1. **`create_server(db_path)` factory + closures** — All tools live as nested closures inside `create_server()`, capturing shared state (DB connection, locks) with no module-level globals. Our version returns `FastMCP` directly.
2. **Tool registration** — Bare `@mcp.tool()` decorator; type annotations drive MCP schema automatically.
3. **Transport layer** — stdio default; `--http` flag enables HTTP streamable. `_FixArgumentsMiddleware` is essential for HTTP mode. FastMCP v1.26+ requires `TransportSecuritySettings(enable_dns_rebinding_protection=False)` for LAN access.
4. **Lifespan gotcha** — Starlette Mount does NOT propagate lifespan to sub-apps; must wrap manually via `_make_lifespan()`.
5. **Test pattern** — `server._tool_manager._tools["tool_name"].fn` confirmed. `_sync_wrap()` helper auto-awaits coroutines.
6. **pyproject.toml** — Deps: `mcp>=1.0,<2.0`, `platformdirs>=3.0,<5.0`. Dev: `pytest>=7.0`, `pytest-mock>=3.0`, `anyio[trio]>=3.0`.
7. **`human_approval=True`** — Apply to `delete_task`.
8. **`list_ready_tasks`** — Default `n_results=10`, cap `MAX_N_RESULTS=100`.

**Decision:** Darlene cleared to begin implementation on core scaffold. (Superseded: coordinator built full implementation this session.)
