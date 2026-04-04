# Orchestration Log — Wiki Completion (Angela & Mobley)

**Date:** 2026-04-02  
**Agents:** Angela (DevRel & Docs), Mobley (Integration & External Systems)  
**Session:** OPM Wiki Creation — 10 comprehensive reference pages  

---

## Summary

Angela and Mobley completed a comprehensive OPM (open-project-manager-mcp) wiki — 10 markdown pages covering architecture, deployment, API reference, and troubleshooting.

**Output:** `docs/wiki/` — 100.3 KB total documentation

---

## Angela — Wiki Content Pages (9 pages, ~80 KB)

### Pages Created

1. **README.md** (1.9 KB) — Wiki index and table of contents
2. **01-what-is-opm.md** (8.2 KB) — Architecture overview, SQLite design, MCP protocol, transport options
3. **02-quickstart.md** (4.6 KB) — 7-step setup: generate token, set env var, configure mcp-config.json, reload, verify
4. **03-mcp-tools-reference.md** (19.6 KB) — Complete reference for all 24 MCP tools with parameters, return values, and examples
5. **04-rest-api-reference.md** (26.4 KB) — Enhanced by Mobley (see below)
6. **05-messaging-system.md** (8.8 KB) — SSE event architecture, event types, filtering, heartbeat, curl examples
7. **06-auth-and-tokens.md** (6.5 KB) — Bearer token format, generation, environment variables, security best practices
8. **07-onboarding-a-new-squad.md** (6.1 KB) — Onboarding procedures and common pitfalls
9. **08-deployment-and-ops.md** (5.0 KB) — Ops quick reference for skitterphuger: start, health checks, restart, upgrade
10. **09-troubleshooting.md** (12.8 KB) — 11 diagnostic scenarios with diagnosis and fixes

### Writing Style

- Clear markdown with headers, code blocks, and tables
- Practical curl/bash examples throughout
- Task-focused ("how to do things, not just theory")
- Comprehensive parameter/return documentation for all tools
- Audience: AI agent squads, MCP clients, OPM administrators

### Target Audience

- AI agent squads (mrrobot, westworld, fsociety, coordinator, ralph)
- MCP clients and tools using OPM
- OPM system administrators and operators

---

## Mobley — REST API Reference Wiki (1 page, ~26 KB)

### Artefact

**File:** `docs/wiki/04-rest-api-reference.md`  
**Status:** Complete — serves as source of truth for REST API design

### Endpoints Documented (18 total)

#### Health & Stats
- `GET /api/v1/stats` (with `?detailed=true` variant)

#### Tasks (CRUD)
- `GET /api/v1/tasks` (project/assignee/status/priority/limit/offset filters)
- `POST /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- `PATCH /api/v1/tasks/{task_id}`
- `DELETE /api/v1/tasks/{task_id}` (requires `?confirm=true`)

#### Projects
- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_id}/summary`

#### Team Status
- `GET /api/v1/status`
- `GET /api/v1/status/{squad}`
- `PUT /api/v1/status/{squad}`

#### Team Events
- `POST /api/v1/events`
- `GET /api/v1/team/events` (squad/event_type/limit filters)

#### Notifications
- `POST /api/v1/notifications`

#### Event Subscriptions
- `GET /api/v1/subscriptions` (subscriber/event_type filters)
- `POST /api/v1/subscriptions`
- `GET /api/v1/subscriptions/{id}`
- `DELETE /api/v1/subscriptions/{id}` (requires `?confirm=true`)

#### Registration (Self-Service Tokens)
- `POST /api/v1/register` (rate limited 5 req/min per IP)
- `DELETE /api/v1/register/{squad}`

#### Real-Time Events (SSE)
- `GET /api/v1/events` (long-lived stream with event_type/squad filtering)

### Documentation Coverage

1. **Authentication** — Bearer token format, header inclusion
2. **Error Handling** — Consistent response format, HTTP status codes (200, 201, 204, 400, 401, 404, 405, 409, 413, 429, 500)
3. **Request/Response Schemas** — Parameter tables with types, required fields, validation rules
4. **curl Examples** — Every endpoint with realistic curl commands (http://192.168.1.178:8765)
5. **Complete Workflows** — Task creation flow (create → get → update → delete) and real-time event monitoring
6. **SSE Connection Management** — Event type descriptions, payload formats, keepalive strategy (30s timeout, `: keepalive\n\n`)
7. **Rate Limiting & Security** — Registration rate limit (5/min/IP), SSRF protections, HTTPS best practices, token storage security
8. **Troubleshooting** — Common errors (401, 404, 409, 413, 429, 500) with diagnostic steps

### Technical Decisions Preserved

- **Request body cap:** 1 MiB (prevent OOM DoS attacks)
- **Squad name validation:** regex `^[a-zA-Z0-9_-]{1,64}$`
- **Registration rate limiting:** In-memory window (60s), opportunistic stale key eviction
- **SSE keepalive:** 30-second timeout with `: keepalive\n\n` messages
- **Subscription event types:** server.stats, server.health, project.summary
- **Notification types:** squad.status, squad.alert, squad.heartbeat
- **Task statuses:** pending, in_progress, done, blocked
- **Team statuses:** online, offline, busy, degraded
- **Priorities:** critical, high, medium, low
- **Pagination:** limit 1–500 (default 20), offset-based

### Source & Validation

- Extracted from `src/open_project_manager_mcp/server.py` REST router implementation
- Validated against actual OPM server behavior at http://192.168.1.178:8765
- No external dependencies or assumptions

---

## Handoff & Next Steps

1. **Knowledge Ingestion** — Post wiki pages to squad-knowledge server for cross-project discovery (optional)
2. **Cross-Squad Feedback** — Squads should report any unclear sections or missing details
3. **Documentation Links** — Add wiki links from main README.md (optional)
4. **Periodic Updates** — Maintain documentation when new endpoints are added

---

## Notes

- `04-rest-api-reference.md` already had detailed content from prior generation; enhanced with quick-ref table by Mobley
- No code changes; docs reflect existing OPM implementation
- All docs follow MCP and deployment conventions from existing README.md and DEPLOY.md
- Total output: ~97 KB documentation (10 pages)
