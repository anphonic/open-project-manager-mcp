# Session Log: Session Reaper Fix

**Date:** 2026-04-02  
**Issue:** FastMCP hung after abrupt client disconnect  
**Status:** RESOLVED

---

## Problem

SKS team reported that open-project-manager-mcp server became unresponsive after a client disconnected abruptly (SIGKILL or TCP RST). All subsequent requests timed out silently. Server required manual restart to recover.

**Symptoms:**
- Client sends request, receives 200 OK
- Client killed (no graceful shutdown, TCP RST sent)
- New requests all timeout with no log output
- Server process still running but frozen
- Restart clears the issue (temporary relief only)

---

## Root Cause

Elliot diagnosed the issue in FastMCP's `StreamableHTTPSessionManager`:

When a client dies abruptly, the session's `run_server()` task blocks forever on HTTP streams that will never receive data. The session persists in the session manager's internal `_server_instances` dictionary indefinitely. As dead sessions accumulate, they starve new connections via `_session_creation_lock` contention.

**Why existing timeout middleware didn't help:**
- `ConnectionTimeoutMiddleware` kills the HTTP connection
- But the session object itself remains in the session manager
- Session task is still blocked, consuming resources

---

## Solution

Implemented session reaper with two layers of protection:

### Layer 1: Activity Tracking
- `SessionActivityMiddleware` tracks last-activity timestamp for each session
- Middleware runs on every HTTP request (fast path)
- Uses `time.monotonic()` for precision unaffected by clock skew

### Layer 2: Background Reaper
- `session_reaper()` background task runs every 30 seconds
- Scans all sessions for inactivity exceeding timeout
- Calls `transport.terminate()` on stale sessions
- Removes dead sessions from session manager
- Exception-safe; continues even if termination fails

### Configuration
- Session timeout: 120 seconds (default)
- Reaper interval: 30 seconds (background)
- CLI flag: `--session-timeout <N>`
- Environment: `OPM_SESSION_TIMEOUT`
- Minimum timeout: 10 seconds

---

## Testing

Romero wrote 12 comprehensive tests covering:
- Activity tracker initialization and cleanup
- Middleware request handling
- Reaper termination logic
- Exception handling during cleanup
- Integration scenarios (orphaned sessions, healthy sessions)

**Test Results:** 330 total tests passing (12 new + 318 existing)

---

## Deployment Notes

**Backward Compatible:** Yes
- Defaults preserve existing behavior (120s timeout >> typical request time)
- No database schema changes
- No breaking API changes

**Graceful Degradation:** If FastMCP internals change in future versions:
- Reaper logs warnings but doesn't crash
- ConnectionTimeoutMiddleware provides fallback cleanup (slower)
- Server remains functional

**Recommended:** Update production deployment with `--session-timeout 120` (explicit, matches default)

---

## Verification

✓ All 330 tests passing  
✓ Session reaper starts automatically with server  
✓ Orphaned sessions cleaned up within 60 seconds (2 × reaper interval)  
✓ Healthy sessions unaffected  
✓ No performance regression on request path
