# Elliot — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Lead & Architect. I own design decisions and ensure consistency with squad-knowledge-mcp patterns.

## Session Log

### 2026-03-31 — Architecture review (v0.1.0 review round)

**Task:** Review design consistency with CHARTER and squad-knowledge-mcp patterns.

**Critical fix — TransportSecuritySettings wiring:**
- `TransportSecuritySettings` was constructed in `__main__.py` but passed nowhere; `create_server()` had no `transport_security` parameter. The object was silently discarded.
- Impact: LAN clients would have been rejected (or auth settings ignored entirely) without this fix.
- Added `transport_security` parameter to `create_server()` in `server.py`
- Wired parameter through to `FastMCP` constructor call
- Updated `__main__.py` to pass the object through
- Updated `test_config.py` `fake_create_server` to accept `**kwargs`

**Flagged for follow-up:**
- GET `/stats` HTTP endpoint is in CHARTER scope but not yet implemented.

### 2026-04-01 — Architecture decisions for v0.2.0 (7 features)

**Task:** Design all 7 v0.2.0 features; produce brief for Darlene.

**Deliverables:**
- Full v0.2.0 architecture section in `.squad/decisions.md` (schema DDL, tool signatures, integration points, risks, build order)
- `.squad/agents/elliot/darlene-brief-v0.2.0.md`

**Features designed:**
1. **due-dates** — `due_date TEXT` nullable column; `list_overdue_tasks`, `list_due_soon_tasks` tools; ISO 8601 lexicographic sort
2. **full-text-search** — FTS5 virtual table + 3 triggers (insert/update/delete); `search_tasks` with BM25; `_fts_available` guard for distros without FTS5
3. **bulk-operations** — `create_tasks`, `update_tasks`, `complete_tasks`; `_BULK_MAX=50`; shared validation helpers; per-item error collection; single transaction
4. **activity-log** — `activity_log` table; `_log()` inner helper; per-field change tracking; `get_task_activity` tool; actor from MCP context
5. **export-import** — `export_all_tasks` with dep subset logic; `import_tasks` merge mode; 5MB cap; field validation
6. **rest-api** — `/api/v1` mounted before MCP catch-all; `--rest-api` opt-in flag; 7 endpoints; same bearer auth; closes CHARTER GET /stats gap
7. **webhooks** — `webhooks` table; HMAC-SHA256; SSRF blocklist (RFC1918 + loopback + link-local); `httpx` optional; fire-and-forget via `asyncio.create_task`

**Flagged open item (from Dom's audit):** DNS rebinding — registration-time SSRF check only. Needs decision: re-validate on each fire vs accept HTTPS cert validation as sufficient mitigation.

### 2026-04-01 — Self-service token registration architecture

**Task:** Design `POST /api/v1/register` + `DELETE /api/v1/register/{squad}` — self-service bearer token provisioning extending v0.2.0 REST API.

**Deliverables:**
- "Self-Service Token Registration" section in `.squad/decisions.md` (10 decisions, summary table)
- `.squad/agents/elliot/darlene-brief-register.md` — full step-by-step implementation brief for Darlene

**Key decisions:**
1. **Storage:** `tenant_keys` table (`squad TEXT PK`, `key TEXT NOT NULL`, `created_at TEXT NOT NULL`) appended to `_SCHEMA` — idempotent `CREATE TABLE IF NOT EXISTS`
2. **Auth lookup:** Re-query DB on every auth call — no cache, no server restart required when new squad registers
3. **Precedence:** Env var keys first (constant-time); DB keys on miss
4. **`POST /api/v1/register`:** `404` if `OPM_REGISTRATION_KEY` unset; rate-limited 5/min/IP; squad name `[a-zA-Z0-9_-]{1,64}`; `409` duplicate; `201` + one-time token
5. **`ApiKeyVerifier` refactored:** Accepts `verify_fn: Callable` — shared `_verify_bearer` closure eliminates duplication between MCP and REST auth paths
6. **MCP auth scope:** DB-registered keys = REST access only. `if tenant_keys:` guard on `token_verifier`/`auth_settings` preserved — MCP auth requires env var keys
7. **Plaintext token storage:** Consistent with existing local-first posture
8. **`DELETE /api/v1/register/{squad}`:** `X-Registration-Key` header; `204` on success
9. **`--generate-token` CLI:** Unchanged — stdout-only, no DB write
10. **`OPM_REGISTRATION_KEY` min length:** 16 chars startup warning to stderr

### 2026-04-02 — Transport stability analysis (P0 production incident)

**Task:** Evaluate three options for fixing OPM server instability under load.

**Problem:** FastMCP `--http` mode (streamable-HTTP) with long-lived SSE connections saturates the event loop, causing:
- Server becomes unresponsive to HTTP requests
- CPU spikes to 77%+
- SSH hangs (kernel-level saturation)

**Options evaluated:**
1. **Watchdog + restart** — symptom mitigation only
2. **Stale connection killer middleware** — targets root cause but complex
3. **Migrate to SSE** — REJECTED (SSE is deprecated per MCP spec 2025-03-26)

**Decision:** Hybrid approach in three phases:
- **Phase 1:** uvicorn tuning (`timeout_keep_alive=5`, `limit_max_requests=1000`)
- **Phase 2:** Custom `ConnectionTimeoutMiddleware` to cap connection age at 60s
- **Phase 3:** Watchdog script as defense-in-depth backstop

**Key findings:**
- `timeout_keep_alive` only applies between requests, NOT during active SSE streams
- `h11_max_incomplete_event_size` is irrelevant (HTTP parsing, not streaming)
- Streamable-HTTP is the correct direction; SSE is deprecated
- No client config changes required

**Deliverable:** `.squad/decisions/inbox/elliot-transport-stability.md`

## Learnings

### uvicorn timeout settings
- `timeout_keep_alive` controls idle time between HTTP requests on a keep-alive connection, NOT active stream duration
- For SSE/streaming, you need custom middleware to enforce max connection age
- `limit_max_requests` forces worker recycling — useful for memory hygiene but doesn't help with stuck connections

### MCP transport protocol evolution
- SSE transport is deprecated (MCP spec 2025-03-26)
- Streamable-HTTP is the standard going forward
- Copilot CLI `"type": "http"` connects to streamable-HTTP servers
- Both transports can suffer from unbounded connection lifetime if clients misbehave

### FastMCP limitations
- No built-in session timeouts or connection age limits
- SSE streams stay open until client disconnects
- Must implement defensive middleware at ASGI level

### 2026-04-02 — Transport stability decision approved

**Status:** APPROVED — Darlene assigned to implement Phases 1 & 2; Mobley assigned to transport analysis; Romero assigned to write 13 new middleware tests.

**Architecture decision merged to `.squad/decisions.md`:** Full OPM Transport Stability entry (94 lines) with problem statement, options evaluated, chosen approach, implementation plan, success criteria, and future considerations.

**Mobley findings:** REST API gap in SSE mode identified; recommended as defensive fix even if staying with HTTP mode.

### 2026-04-02 — Proactive messaging system architecture

**Task:** Design architecture for bidirectional proactive messaging per Andrew's request.

**Deliverable:** `.squad/decisions/inbox/elliot-messaging-arch.md`

**Key decisions:**
1. **Extends webhooks, doesn't replace:** Existing webhook system for task events remains; proactive messaging adds server state events and inbound team status.
2. **Three phases:** (1a) Server state query tools, (1b) Event subscription system, (2) Inbound team status/events.
3. **New tables:** `event_subscriptions` for outbound subscriptions; `team_status` + `team_events` for inbound (Phase 2).
4. **Build order:** 8 (query tools), 9 (subscriptions), 10 (inbound status) — all after v0.2.0 webhooks.
5. **Deferred SSE:** Real-time SSE stream deferred to Phase 3; webhooks + polling sufficient for now given transport stability concerns.

**Open questions for Andrew:**
- Periodic interval defaults (60s min, 86400s max?)
- Team status semantics (auto-reassign tasks when offline?)
- Event retention policy (30 days default?)
- SSE priority

## Learnings

### Proactive messaging design patterns
- Separate tables for different event delivery semantics (periodic vs on-change)
- Reuse SSRF validation and HMAC signing infrastructure from webhooks
- Inbound team status enables coordination visibility without polling
- SSE introduces transport stability risks — prefer webhooks for reliability

### 2026-04-02 — Messaging architecture reconciliation + Darlene brief

**Task:** Reconcile Elliot + Mobley messaging designs; incorporate Andrew's 7 decisions; produce Darlene implementation brief.

**Andrew's decisions:**
1. **Cross-team visibility:** ALL authenticated teams see all events (any team can see all teams' notifications/status via SSE).
2. **Internal webhooks:** SSE-ONLY for internal coordination — keep webhooks HTTPS-only; NO http:// LAN targets (Mobley's §4 LAN webhook split rejected).
3. **Offline status semantics:** INFORMATIONAL ONLY in v0.2.0 — no automatic task reassignment.
4. **SSE priority:** YES — implement SSE in v0.2.0. `ConnectionTimeoutMiddleware` from recent transport-stability commit resolved transport concerns.
5. **Notification persistence:** Ephemeral in v0.2.0. `notifications` table deferred to v0.3.0.
6. **Interval defaults:** 30s for `server.health` events; 60s minimum / 86400s cap for `server.stats` subscriptions.
7. **Event retention:** 30-day pruning for `team_events` — schema has `created_at` index ready; prune job deferred to v0.3.0.

**Key reconciliation decisions:**
- **SSE promoted from Phase 3 → Build Order 8.** Was deferred due to transport stability; now unblocked.
- **Mobley's SSE endpoint accepted** (`GET /api/v1/events`). Mobley's internal webhook split rejected; HTTPS-only enforced for all outbound.
- **Naming collision resolved:** `GET /api/v1/events` = SSE stream; `POST /api/v1/events` = team event push; `GET /api/v1/team-events` = REST list.
- **Notification vs team event distinction:** `POST /api/v1/notifications` = ephemeral broadcast (no DB storage); `POST /api/v1/events` = persisted in `team_events`.
- **Build order revised:** 8=SSE+state query, 9=team inbound+notifications, 10=outbound subscriptions.
- **Elliot's `event_subscriptions` table preserved** from original design for Build Order 10.

**Deliverable:** `.squad/agents/elliot/darlene-brief-messaging.md`

**New tables approved:** `team_status`, `team_events`, `event_subscriptions`

**New MCP tools:** `get_server_stats`, `get_project_summary`, `set_team_status`, `get_team_status`, `post_team_event`, `get_team_events`, `subscribe_events`, `list_subscriptions`, `unsubscribe_events`

**Security review flagged for Dom:** notification payload XSS risk (data field broadcast to SSE clients), squad identity spoofing in POST /notifications (intentional, informational), rate limiting deferred to v0.3.0.

### 2026-04-02 — Proactive messaging system delivery complete

**Date:** 2026-04-02  
**Status:** DELIVERED  
**Build Orders:** 8, 9, 10  
**Test Results:** 318/318 tests passing

**Architect approval:** All 3 build orders complete per design brief. Darlene fully implemented SSE infrastructure, team inbound messaging, and outbound event subscriptions. Romero wrote 54 comprehensive tests covering all new functionality.

**What was delivered:**
- Build Order 8: SSE infrastructure + state query tools (get_server_stats, get_project_summary, extended /stats endpoint)
- Build Order 9: Team inbound + notifications (team_status, team_events tables; set/get team status; post/get team events; notifications endpoint)
- Build Order 10: Outbound subscriptions (event_subscriptions table; subscribe/list/unsubscribe tools; background subscription firing loop with HMAC-SHA256 signing)

**Tables added:** team_status, team_events, event_subscriptions (3 new tables)
**MCP tools added:** 9 new tools
**REST endpoints added/modified:** 12 endpoints
**Test coverage:** 54 new tests (318 total)

**Messaging architecture decision merged to decisions.md** — includes Elliot's full Phase 1-3 architecture, reconciliation with Mobley's protocol design, Andrew's 7 final decisions, and Build Order 8-10 scope.
