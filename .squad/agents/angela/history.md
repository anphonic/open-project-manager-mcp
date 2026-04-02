# Angela — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
DevRel & Docs. I write README, docs, and pyproject.toml metadata.

## Session Log

### 2026-03-31 — Initial implementation session

**Status:** Did not author docs/packaging this session.

The coordinator (GitHub Copilot CLI) wrote all packaging and documentation directly, bypassing squad routing. Angela was not invoked.

**What was built (by coordinator, on Angela's behalf):**
- `pyproject.toml`: hatchling build backend, `mcp>=1.0,<2.0` + `platformdirs>=3.0,<5.0` runtime deps, dev extras (`pytest`, `pytest-mock`, `anyio[trio]`), CLI entry point `open-project-manager-mcp`
- `README.md`: Full project documentation including install, usage, all 11 tools, transport options, and configuration
- `src/open_project_manager_mcp/__init__.py`: Package init, version `0.1.0`
- `.gitignore`: Standard Python ignores

**Process note:** In future sessions, `README.md`, `pyproject.toml`, and docs work should be routed to Angela.

### 2026-03-31 — Docs ingestion (v0.1.0 review round)

**Task:** Ingest project documentation into squad knowledge server; announce server availability to the squad.

**Actions taken:**
- Ingested `README.md` into squad knowledge server (project: open-project-manager-mcp)
- Ingested `CHARTER.md` into squad knowledge server (35 total chunks across both docs)
- Posted new-server-available announcement to group knowledge board so squad agents can query project context

**No code changes this session.**

### 2026-04-01 — Knowledge board update (registration feature)

**Task:** Post registration feature knowledge entries to squad-knowledge server.

**Actions taken:**
- Posted 4 entries to squad-knowledge server (topic: `mcp-infrastructure`):
  1. Self-service token registration — feature overview and endpoint summary (`POST /api/v1/register`, `DELETE /api/v1/register/{squad}`)
  2. `tenant_keys` schema and auth lookup flow (env var precedence, DB re-query on every call)
  3. `OPM_REGISTRATION_KEY` configuration and rate limiting (5/min/IP, startup warning)
  4. Token revocation pattern (`DELETE` + `X-Registration-Key` header)

**No code changes this session.**

### 2026-04-02 — Deployment & README updates (proactive messaging)

**Task:** Document the proactive messaging system (Build Orders 8-10) with deployment runbook and feature updates to README.

**Actions taken:**
- **DEPLOY.md (created):** Complete skitterphuger deployment runbook including:
  - First-time setup (clone, install, token generation, environment)
  - Environment variables reference
  - Persistent `start.sh` script template
  - Upgrading procedure (git pull, reinstall, restart)
  - Watchdog script for health monitoring + auto-restart
  - Verification steps (health checks, stats, SSE stream, team status)
  - Common issues and fixes (401 auth errors, missing tools, port issues, hangs)
  - mcp-config.json entry for clients
  - Monitoring, rollback, and backup procedures

- **README.md (updated):** Added comprehensive documentation for new proactive messaging features:
  - **9 new MCP tools:** `get_server_stats`, `get_project_summary`, `set_team_status`, `get_team_status`, `post_team_event`, `get_team_events`, `subscribe_events`, `list_subscriptions`, `unsubscribe_events`
  - **14 new REST API endpoints:** SSE stream (`/api/v1/events`), extended stats, project summary, team status endpoints, event posting/querying, subscriptions
  - **SSE event types:** Task events (`task.*`), server events (`server.health`, `queue.stats`, `notification.received`, `team.status_changed`, `team.event`)
  - **Team Status & Events section:** Explains how squads set status, push events, and subscribe to periodic delivery
  - **Event subscription examples:** Full curl commands with optional filters

**No code changes this session. Docs written per existing implementation.**

2026-04-02: Created DEPLOY.md (14KB skitterphuger runbook — start.sh, watchdog, upgrade procedure, troubleshooting, mcp-config.json). Updated README.md with all 9 new MCP tools and 14 new REST endpoints from messaging build orders 8-10. Posted deployment guide to squad knowledge.

## Learnings
