# ralph history

## Learnings

### 2026-04-04 Session Startup Work-Check

**Server Status:**
- OPM (8765): TCP reachable but MCP `list_ready_tasks` requests hang indefinitely (no response after 60s)
- Squad Knowledge (8768): HTTP 404 on root endpoint, SSE endpoint not found
- Python process (PID 8424) is running but appears unresponsive

**Local State Findings:**
- `.squad/agents/ralph/history.md`: Fresh start (2026-04-03)
- `.squad/decisions.md`: Squad initialized 2026-04-04, no active directives yet
- `.squad/decisions/inbox/`: Empty (no pending decisions)
- Project DB: Only `tests/_rt_test.db` present (61KB, test fixture)

**Actions Taken:**
- Verified TCP connectivity to 192.168.1.178:8765
- Attempted MCP RPC call with valid Bearer token (OPM_BEARER_TOKEN set)
- Attempted Squad Knowledge query
- Both requests timed out or failed

**Recommendation:**
Cannot complete primary OPM work-check due to server unresponsiveness. Escalate to Elliot (Lead & Architect) to verify server health and restart if needed.

_(Fresh start — 2026-04-03)_

### 2026-04-04 Revised Work-Check Attempt

**Key Finding:** OPM software is operational and working correctly.

**Verification:**
- Created 6 test tasks locally (various priorities, 1 with dependency, 1 marked done)
- Called `list_ready_tasks()` directly via Python async/await
- Result: ✅ Correctly identified 4 ready tasks (t1 critical, t2 high, t3 medium, t4 low)
- Verified: Blocked tasks correctly excluded, done tasks excluded
- Priority ordering: Critical > High > Medium > Low (working as specified)

**Remote Deployment Issue:**
- HTTP POST to 192.168.1.178:8765/mcp returns MCP protocol errors
- First attempt: 406 Not Acceptable (Accept header mismatch)
- Second attempt: 400 Bad Request (missing MCP session ID)
- TCP port open and responding, but protocol handshake incomplete
- Inference: Server process likely stale or hung during startup

**Learnings:**
1. OPM tools work correctly — dependency graph logic, priority sorting, ready-task filtering all validated
2. Ready-task query format: Returns JSON with ["tasks"] array and count
3. Blocked tasks correctly excluded (dependency on non-done task)
4. Done tasks correctly excluded
5. Remote server health check needed before next work-check cycle
