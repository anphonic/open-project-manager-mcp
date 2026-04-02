# Session Reaper Implementation — Orchestration Log

**Session:** 2026-04-02 20:57 UTC  
**Topic:** FastMCP orphaned session cleanup (5-phase hybrid fix)

---

## Summary

Fixed critical bug where abrupt client disconnects (SIGKILL, TCP RST) leave FastMCP session manager blocked indefinitely. All subsequent requests timeout silently until server restart. Implemented hybrid session reaper with inactivity timeout + periodic background cleanup.

---

## Elliot (Lead Architect)

**Tasks:**
- Diagnosed root cause: `StreamableHTTPSessionManager.run_server()` blocks forever on dead client streams
- Designed 5-phase hybrid fix: activity tracking + periodic reaper + CLI config
- Created decision document with risk analysis and testing requirements

**Deliverables:**
- `.squad/decisions/inbox/elliot-session-reaper.md` (design, root cause analysis, options evaluated, implementation brief)

**Key findings:**
- ConnectionTimeoutMiddleware only kills HTTP connection, not session in `_server_instances` dict
- Session tasks accumulate and starve new connections via `_session_creation_lock` contention
- Hybrid approach (inactivity timeout + reaper task) provides defense-in-depth without FastMCP modifications

---

## Darlene (Backend Implementation)

**Tasks:**
- Implemented `SessionActivityTracker` — per-session last-activity timestamps
- Implemented `SessionActivityMiddleware` — ASGI middleware, updates tracker on every request
- Implemented `session_reaper()` — background task runs every 30s, terminates stale sessions
- Added `--session-timeout` CLI argument, env `OPM_SESSION_TIMEOUT` (default 120s, minimum 10s)
- Wired middleware order: `ConnectionTimeoutMiddleware` → `SessionActivityMiddleware` → `_FixArgumentsMiddleware` → Starlette app
- Investigated FastMCP internals; confirmed session manager access via `mcp._session_manager._server_instances`

**Deliverables:**
- `.squad/decisions/inbox/darlene-session-reaper.md` (FastMCP internals, access patterns, implementation notes)
- Production code in `src/open_project_manager_mcp/__main__.py`

**Access strategy:**
- Direct attribute access to `session_manager._server_instances` (private API but stable in FastMCP 1.x)
- Exception-safe termination with graceful degradation fallback to ConnectionTimeoutMiddleware

---

## Romero (QA / Testing)

**Tasks:**
- Wrote 12 comprehensive tests in `tests/test_session_reaper.py`
- All tests passing; full suite runs to 330 total tests (all passing)

**Test coverage:**
1. `SessionActivityTracker` — initialization, touch(), remove(), get_stale_sessions()
2. `SessionActivityMiddleware` — session ID extraction, tracker callback on requests
3. `session_reaper()` — termination calls, exception handling, tracker cleanup
4. Integration tests — orphaned session cleanup, healthy session preservation, failure recovery

**Test results:**
- 12 new tests: PASSED
- 318 existing tests: PASSED
- 330 total: PASSED

---

## Impact

**Fixes:**
- Abrupt TCP RST no longer leaves FastMCP session manager blocked indefinitely
- Stale sessions cleaned up automatically every 30 seconds
- New clients can always connect; no server restart required

**Configuration:**
- Session timeout: 120s default (configurable via `--session-timeout` or `OPM_SESSION_TIMEOUT`)
- Reaper interval: 30s (background task, no user config)
- Connection timeout: 60s (existing, unchanged)

**Risk mitigation:**
- Graceful degradation if FastMCP internals change
- Exception handling on session termination
- Conservative default (120s) prevents false positive cleanup
- Redundant protection (middleware + reaper task)

---

## Sign-off

- ✓ Elliot — Design approved
- ✓ Darlene — Implementation complete
- ✓ Romero — All tests passing (330 total)
