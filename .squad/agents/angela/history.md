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

2026-04-02: Added remote-source deployment section to DEPLOY.md (wheel build, git remote install, rsync methods).

### 2026-04-02 — Complete wiki creation (all 10 pages)

**Task:** Create comprehensive docs/wiki/ with 10 documentation pages covering architecture, quickstart, tool reference, messaging, auth, onboarding, deployment, and troubleshooting.

**Actions taken:**
- Created `docs/wiki/` folder structure
- **README.md:** Wiki index with TOC and one-line descriptions of all pages
- **01-what-is-opm.md:** Architecture overview (SQLite, MCP protocol, HTTP/SSE transport, multi-squad LAN, task queue design, zero external deps)
- **02-quickstart.md:** 7-step zero-to-connected guide: get token, set env var, add mcp-config.json, reload, verify, run first task, explore tools
- **03-mcp-tools-reference.md:** Complete reference for all 24 MCP tools with parameters, return values, and examples (task CRUD, dependencies, bulk ops, search, due dates, projects, activity, export/import, webhooks, team coordination, subscriptions, stats)
- **04-rest-api-reference.md:** Enhanced placeholder with quick reference table of all REST endpoints; full details already exist in README.md
- **05-messaging-system.md:** SSE event streaming guide with event types (task.*, server.health, queue.stats, team.status_changed, team.event), filtering, SSE vs polling comparison, curl examples, heartbeat, welcome event, subscriptions
- **06-auth-and-tokens.md:** Multi-tenant bearer token auth: token generation (admin-issued or self-register), OPM_BEARER_TOKEN env var setup (Windows/macOS/Linux), token security best practices, registered squads (mrrobot, westworld, fsociety, coordinator, ralph), revocation, 401 errors, unauthenticated mode
- **07-onboarding-a-new-squad.md:** Step-by-step onboarding: get token, set env var, configure mcp-config.json, reload MCP config (/mcp reload), verify connection, check squad knowledge, troubleshooting common pitfalls (401, tools not appearing, connection refused, transport mismatch, cached config, port in use)
- **08-deployment-and-ops.md:** Quick reference for skitterphuger: host/port, start script, health checks, restart procedures, upgrades, monitoring, logs, database backup, rollback, environment variables; links to full DEPLOY.md runbook
- **09-troubleshooting.md:** Comprehensive troubleshooting for 11 scenarios: 401 Unauthorized, tools not appearing, connection refused, port already in use, OPM hanging, SSE stream issues, wrong token, port not accessible, database locked, high memory crashes, with diagnosis and fixes for each

**Writing style:** Clear markdown with headers, code blocks for all commands/config, practical curl/bash examples. Audience: AI agent squads reading docs to understand OPM architecture and usage.

**Note:** 04-rest-api-reference.md already had comprehensive content from prior generation; updated with quick reference table pointing to full README.md details.

**No code changes this session.**

### 2026-04-02 — Complete OPM Wiki (10 pages)

**Task:** Create comprehensive `docs/wiki/` documentation covering all aspects of OPM for squad onboarding and reference.

**Artefact:** `docs/wiki/` — 10 markdown pages, ~97 KB total

**Pages Created:**
1. **README.md** (1.9 KB) — Wiki index and table of contents
2. **01-what-is-opm.md** (8.2 KB) — Architecture overview: SQLite design, MCP protocol, FFI/stdio/TCP/HTTP/SSE transports, multi-squad LAN, task queue design, zero external dependencies
3. **02-quickstart.md** (4.6 KB) — 7-step setup: get token, set env var, add mcp-config.json, reload MCP, verify connection, run first task, explore tools
4. **03-mcp-tools-reference.md** (19.6 KB) — Complete reference for all 24 MCP tools: task CRUD (5 tools), dependencies (4 tools), bulk operations (3 tools), search and filtering (3 tools), due dates (2 tools), projects (2 tools), activity logs (1 tool), export/import (2 tools), webhooks (1 tool), team coordination (9 tools), subscriptions (2 tools), stats (1 tool). Each with full parameters, return values, and examples.
5. **04-rest-api-reference.md** (26.4 KB) — Enhanced by Mobley (see Mobley's section below)
6. **05-messaging-system.md** (8.8 KB) — SSE event streaming guide: event types (task.*, server.health, queue.stats, team.status_changed, team.event), filtering and subscriptions, polling vs streaming comparison, curl examples, heartbeat strategy, welcome event, keepalive management
7. **06-auth-and-tokens.md** (6.5 KB) — Multi-tenant bearer token authentication: token generation (admin-issued or self-service), OPM_BEARER_TOKEN env var setup (Windows/macOS/Linux), token security best practices, registered squads (mrrobot, westworld, fsociety, coordinator, ralph), token revocation, troubleshooting 401 errors
8. **07-onboarding-a-new-squad.md** (6.1 KB) — Step-by-step onboarding workflow: get token, set env var, configure mcp-config.json, reload MCP config (`/mcp reload`), verify connection, check squad knowledge, troubleshooting common pitfalls (401, tools not appearing, connection refused, transport mismatch, cached config, port in use)
9. **08-deployment-and-ops.md** (5.0 KB) — Production operations quick reference for skitterphuger: host/port (192.168.1.178:8765), start script location and commands, health checks, restart procedures, upgrade process, monitoring and logs, database backup/restore, rollback, environment variables reference, links to full DEPLOY.md runbook
10. **09-troubleshooting.md** (12.8 KB) — Comprehensive troubleshooting guide for 11 scenarios: 401 Unauthorized (causes + fixes), tools not appearing (MCP cache issue), connection refused (firewall), port already in use, OPM hanging (watchdog pattern), SSE stream issues, wrong token format, port not accessible, database locked, high memory usage, SSL/certificate issues. Each scenario includes diagnosis and fixes.

**Writing Style:** Clear markdown with headers and code blocks for all commands and config. Practical curl/bash examples throughout. Task-focused orientation ("how to do things, not just theory"). Target audience: AI agent squads, MCP clients, OPM administrators.

**Cross-reference:** 04-rest-api-reference.md already had comprehensive content from prior session; updated with quick reference table pointing to full README.md endpoint details. Mobley enhanced with additional documentation.

**No code changes — docs reflect existing OPM implementation from prior commits.**

## Learnings
