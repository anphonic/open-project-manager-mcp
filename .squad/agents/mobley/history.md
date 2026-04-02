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
