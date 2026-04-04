# Session Log: Transport Stability Fix

**Timestamp:** 2026-04-02T05:36:12Z  
**Topic:** Connection timeout middleware + uvicorn tuning for OPM production stability

---

## What Was Fixed

OPM running in `--http` mode on skitterphuger (192.168.1.178:8765) was becoming unresponsive within minutes under sustained multi-agent load. Root cause: FastMCP has no session timeouts — MCP clients hold SSE connections open indefinitely, saturating the event loop until the server completely hangs (CPU 77%+, SSH lockup).

**Solution implemented:**

1. **uvicorn tuning (Phase 1):** Reduced connection and worker recycling timeouts
   - `timeout_keep_alive=5` (was 30s)
   - `limit_max_requests=1000` (was 10000)
   - `timeout_graceful_shutdown=10` (was 30s)

2. **ConnectionTimeoutMiddleware (Phase 2):** New ASGI middleware that tracks connection age and forcibly closes connections older than a configurable threshold (default 60s).
   - Tracks via `time.monotonic()` to avoid clock skew
   - Only applies to HTTP connections (WebSocket/lifespan bypass)
   - Logs WARNING when connection killed
   - CLI flag: `--connection-timeout` (seconds), env var `OPM_CONNECTION_TIMEOUT`, min 5s

3. **Bonus fix:** REST API (`/api/v1`) now correctly mounts in SSE mode (was only in HTTP mode).

---

## Why This Matters

**Before:** Server locks up under load, requires manual restart. Unacceptable for production.

**After:** Connections automatically recycled after 60 seconds. Event loop stays responsive. Server self-recovers under sustained load.

**Deployment:** Restart OPM with `--connection-timeout 60` on skitterphuger.

**Expected behavior:** Connection timeout WARNING messages appear in logs (indicating middleware is working), but server stays responsive. CPU stays <50%, SSH remains accessible.

---

## Test Results

- 13 new tests for ConnectionTimeoutMiddleware (all passing)
- 264 total tests passing
- Coverage: middleware initialization, connection tracking, timeout behavior, response handling, edge cases

---

**Session:** 2026-04-02T05:36:12Z  
**Agents:** Elliot (architecture), Mobley (transport analysis), Darlene (implementation), Romero (tests)
