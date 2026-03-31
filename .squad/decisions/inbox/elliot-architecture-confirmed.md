### 2026-03-31: Architecture patterns confirmed via cross-squad query

**By:** Elliot (via cross-squad query to squad-knowledge-mcp team)

**What:** Queried the Westworld squad's knowledge base (192.168.1.178:8766) and read the squad-knowledge-mcp codebase directly to confirm build patterns before Darlene starts.

**Findings:**

1. **`create_server(db_path)` factory + closures** — Confirmed as the right pattern. All tools live as nested closures inside `create_server()`, capturing shared state (DB connection, locks) with no module-level globals. Our version returns `FastMCP` directly (no stats collector needed initially).

2. **Tool registration** — Bare `@mcp.tool()` decorator with no parameters. Type annotations drive the MCP schema automatically.

3. **Transport layer** — stdio is default; `--tcp` / `--http` flag enables HTTP streamable. `_FixArgumentsMiddleware` is essential for HTTP mode to fix non-compliant clients sending `arguments: []` instead of `{}`. FastMCP v1.26+ requires `TransportSecuritySettings(enable_dns_rebinding_protection=False)` for LAN access.

4. **Lifespan gotcha** — Starlette Mount does NOT propagate lifespan to sub-apps; must wrap manually via `_make_lifespan()`. Critical for HTTP mode startup.

5. **Test pattern** — `server._tool_manager._tools["tool_name"].fn` confirmed in squad-knowledge-mcp tests. Patch DB layer at module import level. `_sync_wrap()` helper auto-awaits coroutines. Class-per-tool organization.

6. **pyproject.toml** — Our deps: `mcp>=1.0,<2.0`, `platformdirs>=3.0,<5.0` (stdlib sqlite3, no ChromaDB). Dev: `pytest>=7.0`, `pytest-mock>=3.0`, `anyio[trio]>=3.0`.

7. **`human_approval=True`** — Confirmed pattern; apply to `delete_task` and `delete_project`.

8. **`n_results`** — squad uses 5 for search, 10 for agent/group. Cap at `MAX_N_RESULTS = 100`. `list_ready_tasks` default TBD pending board answer (tentatively 10).

**Open questions on board:**

| # | Question ID | Topic |
|---|-------------|-------|
| Q1 | `Q::Elliot (open-project-manager-mcp)::20260331T221331Z` | `create_server` factory gotchas + `_FixArgumentsMiddleware` for HTTP |
| Q2 | `Q::Elliot (open-project-manager-mcp)::20260331T221331Z` | Test tool access via `_tool_manager._tools` — still valid in current FastMCP? |
| Q3 | `Q::Elliot (open-project-manager-mcp)::20260331T221332Z` | `list_ready_tasks` token-efficient format + `n_results` default |

**Decision:** Darlene is **cleared to begin implementation** on the core scaffold (create_server, tool stubs, sqlite3 setup, stdio transport, pyproject.toml). She should **hold** on `list_ready_tasks` response format and HTTP transport details until Q1 and Q3 are answered. Q2 is low-risk — the `_tool_manager._tools` pattern is confirmed in current tests; if it breaks with a FastMCP upgrade we'll fix it then.
