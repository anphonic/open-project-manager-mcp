# Elliot — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Lead & Architect. I own design decisions and ensure consistency with squad-knowledge-mcp patterns.

## Key Learning: asyncio.Lock Starvation Root Cause

**Date:** 2026-04-02

P1 bug root cause confirmed: **asyncio.Lock starvation**, NOT SQLite write lock.

- Single `_lock = asyncio.Lock()` guards ALL 23 write operations (server.py line 231)
- When session reaper terminates abandoned sessions, if a task held the lock, Python does NOT auto-release it
- Lock remains acquired indefinitely
- All subsequent write operations block indefinitely
- Reads work (don't use lock); writes hang (wait on lock)

**Design decision:** 4-part fix:
1. WAL + busy_timeout pragmas (defense-in-depth)
2. 30s timeout wrapper on all 23 write ops
3. Lock reset in session_reaper after terminating sessions
4. Raise timeout_keep_alive from 5s → 30s

This root cause analysis confirmed the app-level lock contention was the real problem, not database-level issues.

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

### 2026-04-02 — Session Reaper Design & Implementation (Orphaned Session Bug)

**Task:** Diagnose and design fix for P1 bug where abruptly killed MCP clients leave orphaned sessions that block all subsequent requests. Delivered complete implementation with testing.

**Root Cause Analysis:**
- SKS coordinator reported: client killed with SIGKILL, server hung, required restart
- `ConnectionTimeoutMiddleware` kills HTTP connections but NOT the underlying FastMCP session
- Sessions persist in `StreamableHTTPSessionManager._server_instances` indefinitely
- `run_server()` task blocks forever on `self.app.run()` waiting on dead streams
- Task accumulation + `_session_creation_lock` contention starves new requests

**Key Finding:** The bug is in FastMCP's session manager design, NOT our middleware:
- `mcp/server/streamable_http_manager.py` lines 243-271: `run_server()` has no timeout
- `mcp/server/streamable_http.py` lines 989-1047: `message_router()` blocks indefinitely
- Neither has keepalive or inactivity detection

**Design Decision:** Hybrid approach (session-level inactivity timeout + periodic reaper task)
- `SessionActivityTracker` class tracks last-activity timestamp per session
- `SessionActivityMiddleware` updates tracker on every HTTP request with session ID
- `session_reaper()` background task terminates stale sessions every 30s
- Default session timeout: 120 seconds of inactivity

**Deliverables:** 
- `.squad/decisions.md` merged entry — full design, root cause, 5-phase implementation plan, testing requirements
- Orchestration log: `.squad/orchestration-log/20260402T205710Z-session-reaper.md`
- Session log: `.squad/log/20260402T205710Z-session-reaper-fix.md`

**Status:** COMPLETE — Implementation delivered by Darlene, 12 tests passing (330 total)

## Learnings

### FastMCP StreamableHTTP session lifecycle
- `StreamableHTTPSessionManager.run()` creates a task group that spawns per-session tasks
- Each session's `run_server()` task blocks on `self.app.run()` until cancelled
- Sessions stored in `_server_instances` dict, keyed by session ID
- No built-in inactivity timeout or keepalive mechanism
- `terminate()` method exists but must be called explicitly

### ASGI middleware layering for session management
- HTTP connection timeout (ConnectionTimeoutMiddleware) ≠ session timeout
- Session activity must be tracked separately at the application layer
- MCP session ID in `mcp-session-id` header, accessible in ASGI scope
- Middleware order matters: outer middleware sees requests first

### Diagnosing event loop saturation
- Stuck tasks accumulate in anyio TaskGroup, increasing scheduling overhead
- Lock contention (`_session_creation_lock`) creates cascading timeouts
- Server process appears healthy (running, logging stopped) but unresponsive
- Symptom: new requests timeout with no log entries = task starvation

### 2026-04-02 — asyncio.Lock write starvation bug (P1)

**Task:** Root cause analysis of POST hang reported by SKS team.

**Symptom:** GET `/api/v1/stats` works; POST `/api/v1/tasks` hangs with 0 bytes received. Orphaned session `3ad3f83ae79f46668996fd3a8a94e1b0` from killed Python client suspected.

**Root cause:** NOT SQLite write lock — it's **asyncio.Lock starvation**:
- Single `_lock = asyncio.Lock()` guards all 23 write operations (line 231)
- Session reaper terminates asyncio task but does NOT release held lock
- `asyncio.Lock` does NOT auto-release on task cancellation
- Result: `_lock` stays acquired forever, all writes hang

**Key finding:** SQLite connection management is fine:
- `check_same_thread=False` allows event loop sharing
- All writes have `conn.commit()`
- No WAL or busy_timeout set (but not the root cause)

**Fix designed:**
1. Add 30s timeout to all `async with _lock:` blocks
2. Expose lock via `get_write_lock()`, reset in reaper
3. Add WAL + busy_timeout as defense-in-depth
4. Raise `timeout_keep_alive` 5s → 30s (reaper makes aggressive timeout unnecessary)

**Deliverable:** `.squad/decisions/inbox/elliot-sqlite-writelock-fix.md`

**Immediate mitigation:** Server restart releases orphaned lock

## Learnings

### asyncio.Lock cancellation semantics
- `asyncio.Lock` does NOT auto-release when holding task is cancelled
- Unlike threading.Lock (which releases on thread death), asyncio locks persist
- Must explicitly release or timeout when task cancellation is possible
- Pattern: always use `async with asyncio.timeout(N):` around lock acquisition in cancellable contexts

### Distinguishing SQLite vs app-level locks
- SQLite implicit transactions auto-rollback on connection close
- SQLite WAL allows concurrent reads but serializes writes
- App-level asyncio.Lock is independent — can block even when SQLite is free
- Symptom analysis: if SELECTs work but INSERTs hang, check app locks first

### 2026-04-03 — Telemetry + Push Notifications Architecture

**Task:** Design per-tenant telemetry and push notification system per Andrew's request.

**Deliverable:** `.squad/decisions/inbox/elliot-telemetry-notifications-design.md`

**Key design decisions:**

1. **Telemetry schema:** `telemetry_metrics` table with hourly buckets (squad, metric_type, project, count, period_start). Bounded growth (24 rows/day per metric), efficient aggregation at query time.

2. **Push notification 3-tier model:**
   - Tier 1: SSE (real-time for connected clients)
   - Tier 2: Webhooks (guaranteed delivery, requires HTTP server)
   - Tier 3: Message queue (persistent, polling-based for offline coordinators)

3. **Client registry:** `sse_clients` table tracks WHO is connected to SSE, not just that connections exist. Changed `_event_bus_clients` from list to dict keyed by client_id with squad metadata.

4. **Scope split:** v0.3.0 gets telemetry + registry + pending notifications. v0.3.1 gets routing logic, pruning, retries.

**Build order:** BO-11 through BO-15 (5 build orders for v0.3.0).

## Learnings

### SSE client identity tracking
- FastMCP's `_event_bus_clients` is a list of anonymous asyncio.Queues
- No metadata about which tenant owns which connection
- Must explicitly track tenant identity in separate data structure (DB + in-memory dict)
- DB-backed registry survives restart, supports admin queries

### Notification delivery guarantees
- SSE: instant but ephemeral (must be connected)
- Webhooks: reliable but requires coordinator to run HTTP server
- Message queue: persistent but requires polling
- Hybrid model covers all coordinator deployment scenarios

### Telemetry aggregation strategies
- Per-event logging (activity_log) is unbounded
- Hourly buckets provide bounded growth with aggregation flexibility
- Unique index on (squad, metric, project, period_start) enables efficient upsert

### 2026-04-03 — P0 Event Loop Blocking Fix Delivered

**Status:** COMPLETE — Darlene fully implemented P0 concurrency bug fix per `elliot-concurrency-fix-design.md`

**Root Cause:** All 100+ sqlite3 operations (reads AND writes) are synchronous, blocking entire asyncio event loop. HTTP GET requests hang when MCP clients perform writes.

**Solution Implemented:**
- Async database helpers: `_db_execute()`, `_db_execute_one()` using `asyncio.to_thread()`
- Updated `_locked_write()` to offload write functions to thread pool
- Converted 28 MCP tools to `async def`
- Updated 14 REST API handlers to use async helpers
- Made `_verify_bearer()` async for bearer token lookups

**Results:**
- 344/344 tests passing (all existing + new concurrency tests)
- HTTP GET returns immediately during write operations
- No curl timeouts under concurrent load
- Bulk import no longer blocks SSE connections
- Event loop remains responsive under sustained load

**Decision merged to decisions.md:** Full P0 fix entry with root cause, chosen approach, implementation plan, verification criteria

**Next Steps:** Deploy to production; monitor SKS team for server stability; proceed with v0.3.0 feature work

## Learnings

### Event loop blocking with synchronous IO
- Synchronous sqlite3 calls inside async functions STILL block the event loop
- The function being async doesn't help if it calls sync blocking code
- Need explicit `asyncio.to_thread()` or `run_in_executor()` to yield control
- This is a fundamental asyncio principle often missed in initial implementations

### Thread pool overhead trade-off
- `asyncio.to_thread()` adds ~1-2ms per call (thread pool round-trip)
- Worthwhile when unblocking event loop for concurrent requests
- Better 2ms latency + responsive server than instant latency + stalled event loop
- Measurable improvement in throughput under SKS/Westworld production load
