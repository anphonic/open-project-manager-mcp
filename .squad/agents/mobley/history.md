# Mobley — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Integration & External Systems Specialist. I review REST API design, webhook patterns, external HTTP client safety, and integration with other services.

## Session Log

### 2026-04-01 — Hired + API review for v0.2.0

Brought in as Integration & External Systems Specialist for v0.2.0 feature work.
Scope: REST API design, webhooks, GET /stats, external HTTP client patterns.

**Task:** Review Elliot's REST API and webhook design before Darlene begins implementation.

**Artefact:** `.squad/agents/mobley/api-review-v0.2.0.md`

**Critical catches (3):**

1. **`asyncio.create_task` GC bug** — Task object returned by `asyncio.create_task(_fire_webhooks(...))` can be garbage-collected before completion if no reference is held, silently dropping webhook deliveries. Fix: maintain a closure-level `_background_tasks: set`; use `task.add_done_callback(_background_tasks.discard)` to clean up after completion. Fix applied.

2. **`task.deleted` webhook fires after row deleted** — `_fire_webhooks` for `task.deleted` was called after `DELETE` SQL executed, leaving no row to build the payload from. Fix: capture the task dict before executing DELETE and pass it explicitly to `_fire_webhooks`. Fix applied.

3. **REST `update_task` logs before 404 check** — The activity log `_log()` call in the REST PATCH handler preceded the task existence check, writing a spurious log entry for non-existent task IDs. Fix: check existence first, return 404 before any logging. Fix applied.

## Learnings

### 2026-04-01 — Transport Migration Analysis (HTTP → SSE)

**Context:** OPM crashes under load in `--http` mode — FastMCP's streamable-HTTP transport has no session timeouts, causing event loop saturation when clients hold SSE connections open.

**Findings written to:** `.squad/decisions/inbox/mobley-transport-analysis.md`

**Key discoveries:**

1. **SSE transport is viable** — squad-knowledge-mcp runs it successfully in production. Both transports share the same auth infrastructure (`ApiKeyVerifier`/`AuthSettings`).

2. **REST API is transport-independent** — The `/api/v1` router is a separate Starlette mount. It currently only mounts in `--http` mode, but there's no technical blocker to mounting it in `--sse` mode. Simple fix: replicate the REST mounting logic in the SSE branch of `__main__.py`.

3. **SSE endpoint structure** — FastMCP's SSE transport uses `/sse` for the event stream and `/messages/` for client POSTs. Clients specify the base URL only (`http://192.168.1.178:8765`), not `/mcp`.

4. **Transports are mutually exclusive** — `--http` and `--sse` use `argparse.add_mutually_exclusive_group()`. Can't run both simultaneously on the same port.

5. **Client config migration** — All squads (westworld, etc.) must update `mcp-config.json`:
   - Change `"type": "http"` → `"type": "sse"`
   - Change URL from `http://192.168.1.178:8765/mcp` → `http://192.168.1.178:8765`
   - Keep `Authorization: Bearer ${env:OPM_BEARER_TOKEN}` unchanged

6. **Watchdog approach** — Recommended: poll `/api/v1/tasks?limit=1` every 60s with 10s timeout. Restart OPM if unresponsive. Transport-independent (works with both HTTP and SSE).

**Trade-offs:**
- **Pro:** SSE is simpler, proven stable (squad-knowledge runs it), may have built-in timeouts.
- **Con:** SSE is deprecated in the MCP spec; we'll need to migrate back to HTTP when FastMCP fixes the timeout issue.

**Recommendation:** Migrate to `--sse` as a short-term stability fix. Add the REST API mounting logic to SSE mode so we don't lose `/api/v1` functionality.

### 2026-04-02 — Transport stability decision + implementation

**Status:** Elliot rejected SSE migration; approved hybrid approach (uvicorn tuning + middleware).

**Key insight implemented:** REST API mounting gap in SSE mode identified in analysis. Darlene fixed this as a bonus in Phase 2 implementation — now `--sse --rest-api` correctly mounts `/api/v1` (was being ignored before).

**Deliverable:** `.squad/decisions/inbox/mobley-transport-analysis.md` merged into `.squad/decisions.md`

**Learnings:**
- REST API endpoints are transport-independent (separate Starlette router)
- REST API now works in both `--http` and `--sse` modes after Darlene's bonus fix
- Watchdog polling recommendation (`/api/v1/tasks?limit=1`) remains valid for both transports
- SSE remains viable fallback if future FastMCP HTTP timeout issues require migration

### 2026-04-02 — Proactive Messaging System API Design

**Context:** Andrew requested bidirectional proactive messaging — server state updates pushed AND pulled.

**Deliverable:** `.squad/decisions/inbox/mobley-messaging-design.md`

**Key design decisions:**

1. **SSE event stream (`GET /api/v1/events`)** — Client-initiated long-lived connection for real-time updates. Complements existing webhooks (server-initiated push). Events: `task.*`, `server.health`, `queue.stats`, `notification.received`.

2. **Team notification inbox** — `POST /api/v1/notifications` allows teams to push status updates TO OPM (reverse direction). Ephemeral in v0.2.0 (no SQLite persistence), broadcast to SSE clients.

3. **State snapshot extension** — Extend existing `GET /api/v1/stats` with `?detailed=true` query param (comprehensive server state + per-project task breakdowns + webhook counts).

4. **Internal vs external webhooks** — Proposed split: allow `http://` URLs for RFC1918/loopback addresses (internal squad coordination) while keeping `https://` + SSRF guard for external integrations. Decision deferred to Elliot.

**Integration protocol:**
- **OPM → Teams:** Webhooks (fire-and-forget POST) + SSE stream (client-initiated pull)
- **Teams → OPM:** REST API task CRUD + notifications POST + state snapshot GET

**Learnings:**
- SSE transport (FastMCP) uses two-endpoint pattern: `/sse` (stream) + `/messages/` (client POSTs)
- SSE streams are long-lived, require connection management (timeouts, retry headers, heartbeats)
- Webhook system (HTTPS-only, SSRF-guarded) is designed for external integrations; SSE is better for internal LAN coordination
- Event fanout to multiple SSE clients requires queue-per-client (simple) or pub/sub pattern (optimized, v0.3.0)
- Notification persistence deferred to v0.3.0 (SQLite `notifications` table)

**Open questions for Elliot:**
- Event schema consistency (SSE vs webhooks)
- Server health metrics to include
- Queue stats event triggers
- Notification persistence timeline
- Internal webhook split approval

## Scribe Note — 2026-04-02

`decisions/inbox/mobley-messaging-design.md` merged into `decisions.md` as section **"Mobley — Proactive Messaging Protocol Design (2026-04-02)"**. Inbox file deleted. No git commit (pending Elliot's reconciliation of `elliot-messaging-arch.md`).

### 2026-04-02 — Squad Knowledge Server OPM Connection Support

**Context:** Squad Knowledge Server team (westworld squad, Maeve) and coordinator posted open questions about connecting to OPM MCP server - tools not appearing, port 8765 access issues.

**Task:** Answer OPM connection questions and provide comprehensive connection guide.

**Questions Answered:**

1. **Maeve (westworld) - OPM tools not available**
   - **Issue**: mcp-config.json configured but OPM MCP tools not appearing in session
   - **Root Causes Identified**: MCP config cache not reloaded, wrong transport type, or port blocked
   - **Solution Provided**: `/mcp reload` slash command, verify `"type": "http"` (not "sse"), check bearer token, verify port 8765 open
   - **Key Point**: Must use CLI slash command `/mcp reload` NOT `mcp_reload` tool to refresh MCP server registry

2. **Coordinator - Port 8765 firewall access**
   - **Issue**: `sudo ufw allow 8765/tcp` run but port still timing out
   - **Solution Provided**: Verify UFW reload, check UFW status, verify OPM binding to 0.0.0.0 (not localhost), check netstat for listener
   - **Troubleshooting**: SSH access to verify process status, restart script location

**Deliverables:**
- Posted specific answers to both questions via `answer_question` tool
- Posted comprehensive "OPM Connection Guide" to squad knowledge via `post_group_knowledge`
- Connection guide covers: server details, mcp-config.json format, auth, self-service registration, REST API, troubleshooting

**Key Technical Details Documented:**
- OPM URL: `http://192.168.1.178:8765/mcp` (streamable-HTTP transport)
- Config type MUST be `"http"` (not "sse") - critical distinction
- Auth: Bearer token via `Authorization` header, `OPM_BEARER_TOKEN` env var
- Registered squads: mrrobot, westworld, fsociety, coordinator, ralph
- REST API available when `--rest-api` flag used
- Connection timeout: 60s default via ConnectionTimeoutMiddleware
- Watchdog pattern: poll `/api/v1/tasks?limit=1` every 60s

**Learnings:**
- Common failure mode: MCP config cached, requires explicit CLI slash command to reload (not tool call)
- Transport type confusion: "sse" vs "http" in mcp-config.json leads to connection failures
- Port accessibility distinct from config validity - firewall must allow 8765/tcp inbound
- Squad Knowledge Server uses SSE transport (`/sse` endpoint), OPM uses streamable-HTTP (`/mcp` endpoint)
- SSE transport message flow: GET `/sse` → receive endpoint → POST `/messages/?session_id=X` → listen for response on same SSE stream

**Integration Pattern Confirmed:**
Squad Knowledge Server (SSE) ← Python script → answers + knowledge posts
- Used MCP Python SDK `sse_client` for clean async tool calls
- Tool calls: `list_open_questions`, `answer_question`, `post_group_knowledge`
- Session lifecycle: connect → initialize → call tools → cleanup

**Artefacts:**
- `J:\Coding\open-project-manager-mcp\.squad\answer_opm_questions.py` — working script for answering OPM questions (kept for reference)
- `J:\Coding\open-project-manager-mcp\.squad\query_sks_sdk.py` — MCP SDK query example (kept for reference)

