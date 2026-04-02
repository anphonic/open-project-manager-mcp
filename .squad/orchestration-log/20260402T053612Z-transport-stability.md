# Orchestration Log: Transport Stability Fix

**Timestamp:** 2026-04-02T05:36:12Z  
**Topic:** OPM connection timeout and uvicorn tuning for production stability on skitterphuger

---

## Agent Contributions

### Elliot (Architecture Decision)

**Decision Document:** `.squad/decisions/inbox/elliot-transport-stability.md`

**Role:** Lead architect; defined root cause analysis and approved hybrid solution.

**Key Decisions:**
- Root cause: FastMCP lacks session timeouts; MCP clients hold connections open indefinitely, saturating event loop
- Evaluated three options: Watchdog (insufficient), Middleware (complex but correct), SSE migration (deprecated, same problem)
- Approved three-phase approach: Aggressive uvicorn tuning (Phase 1), Connection Timeout Middleware (Phase 2), Watchdog script (Phase 3)
- Set requirements: `--connection-timeout` CLI argument (default 60s, min 5s), configurable via env `OPM_CONNECTION_TIMEOUT`

**Phase 1 rationale:** Aggressive TCP and worker recycling mitigates event loop saturation  
**Phase 2 rationale:** Explicit connection age tracking + forced disconnect for connections >60s  
**Phase 3 rationale:** Defense-in-depth; last-resort polling script as backstop

**Success Criteria:**
- OPM responsive under sustained multi-agent load for 24+ hours
- No SSH lockups on skitterphuger
- Connection timeout warnings in logs; service stays up
- Watchdog reports zero restarts after stabilization

---

### Mobley (Transport Analysis)

**Analysis Document:** `.squad/decisions/inbox/mobley-transport-analysis.md`

**Role:** Transport layer expert; validated REST API behavior and identified SSE mounting gap.

**Key Findings:**
1. **REST API independence:** REST API (`/api/v1`) is transport-independent; currently only mounted in `--http` mode; can be mounted in SSE mode too
2. **Auth consistency:** Both `--http` and `--sse` transports respect same `ApiKeyVerifier`/`AuthSettings` auth infrastructure
3. **Transports mutually exclusive:** Only one can run per instance (argparse `add_mutually_exclusive_group()`)
4. **SSE viability:** Viable but deprecated; squad-knowledge-mcp runs successfully; same fundamental problem exists
5. **Watchdog approach:** Poll transport-independent REST API endpoint (`/api/v1/tasks?limit=1`) for health checks

**Recommendation:** Stick with `--http` + Connection Timeout Middleware (Elliot's Phase 1+2). Implement REST API mounting in SSE mode as defensive preparation for future migration if FastMCP fixes HTTP timeout issues.

---

### Darlene (Implementation)

**Implementation Document:** `.squad/decisions/inbox/darlene-transport-fix.md`

**Role:** Backend developer; executed Phases 1 and 2 of stability fix.

**Phase 1 Changes (uvicorn tuning):**
- `timeout_keep_alive=5` (was 30)
- `limit_max_requests=1000` (was 10000)
- `timeout_graceful_shutdown=10` (was 30)

**Phase 2 Changes (ConnectionTimeoutMiddleware):**
- New ASGI middleware class (lines 83-132)
- Tracks connection age via `time.monotonic()`
- Wraps `receive()` to inject `http.disconnect` on timeout
- Wraps `send()` with `response_started` flag to prevent double-response errors
- Logs WARNING when connection killed
- Only applies to HTTP scope
- CLI argument: `--connection-timeout` (int, default 60, env `OPM_CONNECTION_TIMEOUT`), validates ≥5s

**Bonus Fix:**
- Mounted REST API in SSE mode (addressing Mobley's gap)
- Before: `--sse --rest-api` ignored REST API flag
- After: `--sse --rest-api` correctly mounts `/api/v1` router

**Files Modified:** `src/open_project_manager_mcp/__main__.py`
- Line 9: Added `import time`
- Lines 83-132: `ConnectionTimeoutMiddleware` class
- Lines 257-260: `--connection-timeout` CLI argument
- Lines 295-311: Parsing and validation
- Lines 380-402: Middleware applied to both modes; REST API mounting in SSE
- Lines 404-413: Updated uvicorn parameters

**Deployment:** OPM now restarts with `--connection-timeout 60` on skitterphuger

**Testing Strategy:**
1. Manual: Start locally with `--http --connection-timeout 10`, idle stream 12+ seconds, verify WARNING log and connection closes
2. Production: Monitor `/var/log/opm.log` for timeout warnings; watch CPU <50%; verify SSH remains responsive

---

### Romero (Tests)

**Role:** QA/testing; wrote integration tests for ConnectionTimeoutMiddleware.

**Test Coverage (13 new tests, 264 total passing):**

1. **Middleware initialization:**
   - Default timeout: 60 seconds
   - Custom timeout via constructor
   - Invalid timeout (<5s) raises ValueError on init

2. **Connection age tracking:**
   - Connection age increments correctly via `time.monotonic()`
   - Receive wrapped correctly
   - Send wrapped correctly

3. **HTTP scope handling:**
   - HTTP connections tracked
   - Non-HTTP scopes (WebSocket, lifespan) bypass middleware

4. **Timeout behavior:**
   - Connection allowed if age < threshold
   - Connection killed if age ≥ threshold (injects `http.disconnect`)
   - WARNING logged on kill

5. **Response handling:**
   - `response_started` flag prevents double-response errors
   - 408 response sent if timeout fires before response started
   - Response body sent correctly

6. **Edge cases:**
   - Rapid request/response cycles
   - Long-lived SSE streams (killed gracefully)
   - Concurrent connections (each tracked independently)

**Test Command:**
```bash
pytest tests/test_connection_timeout_middleware.py -v
```

**Coverage:** All middleware paths exercised; edge cases validated.

---

## Summary

**Objective:** Fix OPM production stability on skitterphuger (192.168.1.178:8765).

**Problem:** FastMCP has no session timeouts; MCP clients hold SSE connections indefinitely, saturating event loop; server becomes unresponsive within minutes under load.

**Solution Delivered:**
1. **Phase 1 (Darlene):** Aggressive uvicorn tuning — reduce `timeout_keep_alive` to 5s, `limit_max_requests` to 1000
2. **Phase 2 (Darlene):** ASGI middleware — explicit connection age tracking, forcibly close connections >60s (configurable)
3. **Bonus (Darlene):** Fixed REST API mounting in SSE mode (Mobley's gap identification)
4. **Tests (Romero):** 13 new middleware tests, all passing

**Deployment Readiness:**
- Code compiles, server starts without errors
- All 264 tests passing (13 new for middleware)
- CLI argument `--connection-timeout` operational (default 60s, configurable)
- Both `--http` and `--sse` modes supported
- Phase 3 watchdog script deferred to ops (not implemented in this PR)

**Next Steps:**
1. Deploy to skitterphuger with `--connection-timeout 60`
2. Monitor logs for `[ConnectionTimeoutMiddleware] Killed connection` warnings
3. Track CPU usage, SSH responsiveness, watchdog restart count over 24 hours
4. If stabilized: Phase 3 watchdog script deployment (ops task)
5. Long-term: File issue on FastMCP repo for native session timeout support

---

**Generated by:** Scribe  
**Session:** 2026-04-02T05:36:12Z
