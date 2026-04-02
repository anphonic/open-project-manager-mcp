# Proactive Messaging System Architecture

**Author:** Elliot (Lead & Architect)  
**Date:** 2026-04-02  
**Status:** DRAFT — Awaiting Andrew's feedback  
**Requested by:** Andrew  
**Related:** v0.2.0 webhooks (build order 7), activity-log (build order 4)

---

## 1. Scope & Relationship to Existing Systems

### 1.1 What "Proactive Messaging" Means

Andrew requested: *"a proactive messaging system — start with server state updates, both push and get requests to/from the different teams."*

**Interpretation:** A bidirectional notification system where:
- **OPM → Teams (Push):** OPM proactively pushes state changes to registered team endpoints
- **Teams → OPM (Get):** Teams can poll/subscribe to OPM state (REST API + optionally SSE stream)
- **Teams → OPM (Push):** Teams can proactively send status updates to OPM

### 1.2 Relationship to Existing Webhooks (Build Order 7)

| Aspect | Existing Webhooks | Proactive Messaging |
|--------|-------------------|---------------------|
| **Direction** | OPM → Teams (push only) | Bidirectional |
| **Events** | Task CRUD only (4 events) | Task events + server state + team status |
| **Delivery** | Fire-and-forget HTTP POST | Fire-and-forget + optional delivery tracking |
| **Subscription** | Per-project webhook registration | Extends webhooks + adds new event categories |

**Decision:** Proactive messaging **extends** the existing webhook infrastructure. It does NOT replace webhooks — it adds:
1. New event categories beyond task CRUD
2. Inbound status reporting from teams
3. (Phase 2) Real-time SSE subscription endpoint

---

## 2. Phase 1 — Server State Push (MVP)

### 2.1 New Event Categories

Extend `VALID_WEBHOOK_EVENTS` with server state events:

```python
VALID_WEBHOOK_EVENTS = {
    # Existing task events
    "task.created", "task.updated", "task.completed", "task.deleted",
    # Phase 1: Server state events
    "server.stats",           # Periodic summary: queue depth, task counts by status
    "server.health",          # Server health check (startup, shutdown, degraded)
    # Phase 1: Aggregate events
    "project.summary",        # Daily/triggered project rollup
}
```

### 2.2 Schema Extension — `event_subscriptions` Table

Separate from webhooks because:
- Different delivery semantics (periodic vs. on-change)
- Different payload shapes (aggregate stats vs. individual task)
- Allows different auth/rate-limit rules

```sql
CREATE TABLE IF NOT EXISTS event_subscriptions (
    id             TEXT    PRIMARY KEY,
    subscriber     TEXT    NOT NULL,       -- squad/team identifier
    url            TEXT    NOT NULL,       -- HTTPS endpoint (same SSRF rules as webhooks)
    event_type     TEXT    NOT NULL,       -- 'server.stats' | 'server.health' | 'project.summary'
    project        TEXT,                   -- NULL = all projects (for project.summary)
    interval_sec   INTEGER,                -- for periodic events; NULL = on-change
    enabled        INTEGER NOT NULL DEFAULT 1,
    last_fired_at  TEXT,                   -- ISO timestamp of last successful delivery
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS event_sub_type_idx ON event_subscriptions(event_type);
```

### 2.3 New MCP Tools (Phase 1)

```python
subscribe_events(
    id: str,
    subscriber: str,           # team/squad identifier
    url: str,                  # HTTPS endpoint
    event_type: str,           # server.stats | server.health | project.summary
    project: Optional[str] = None,
    interval_sec: Optional[int] = None  # for periodic; min 60, max 86400
) -> str
# Same SSRF validation as register_webhook
# Returns: {"id": id, "event_type": event_type, "subscriber": subscriber}

list_subscriptions(subscriber: Optional[str] = None) -> str
# Returns: {"subscriptions": [...]}

unsubscribe_events(id: str, human_approval: bool = False) -> str
# Requires human_approval=True (consistent with delete patterns)

# Server state query tools (GET side for teams)
get_server_stats() -> str
# Returns: {"queue_depth": N, "by_status": {...}, "by_project": {...}, "uptime_sec": N}

get_project_summary(project: str) -> str
# Returns: {"project": str, "total": N, "pending": N, "in_progress": N, "done": N, "blocked": N, "overdue": N}
```

### 2.4 REST API Extensions (Phase 1)

Add to `/api/v1`:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stats` | Already exists — enhanced with messaging stats |
| GET | `/projects/{project}/summary` | Project summary (same as MCP tool) |
| POST | `/subscriptions` | Subscribe to events |
| GET | `/subscriptions` | List subscriptions (filtered by `subscriber` query param) |
| DELETE | `/subscriptions/{id}` | Unsubscribe |

### 2.5 Delivery Mechanism

Reuse `_fire_webhooks` pattern with new `_fire_event_subscriptions`:

```python
async def _fire_event_subscriptions(event_type: str, payload: dict) -> None:
    """Fire event to all matching subscriptions. Fire-and-forget."""
    # Same httpx pattern as _fire_webhooks
    # Uses HMAC-SHA256 signing if subscriber has registered a secret
    # No retries in Phase 1
```

**Periodic Events:**
- `server.stats` and `project.summary` with `interval_sec` set are fired by a background task
- Use `asyncio.create_task` with a simple loop (started in server lifespan)
- Check `last_fired_at + interval_sec < now()` to determine eligibility

### 2.6 Payload Shapes

**server.stats:**
```json
{
    "event": "server.stats",
    "timestamp": "2026-04-02T12:00:00Z",
    "data": {
        "queue_depth": 47,
        "by_status": {"pending": 20, "in_progress": 15, "done": 100, "blocked": 12},
        "by_project": {"opm": 30, "squad-knowledge": 17},
        "uptime_sec": 86400
    }
}
```

**server.health:**
```json
{
    "event": "server.health",
    "timestamp": "2026-04-02T12:00:00Z",
    "data": {
        "status": "healthy",  // healthy | degraded | shutting_down
        "message": "Server started successfully"
    }
}
```

**project.summary:**
```json
{
    "event": "project.summary",
    "timestamp": "2026-04-02T12:00:00Z",
    "data": {
        "project": "opm",
        "total": 50,
        "pending": 20,
        "in_progress": 10,
        "done": 15,
        "blocked": 5,
        "overdue": 3
    }
}
```

---

## 3. Phase 2 — Bidirectional Messaging

### 3.1 Teams → OPM Inbound Status (Push)

Allow teams to push status updates to OPM. Use cases:
- "Our server is down" (affects task assignment decisions)
- "We completed milestone X" (for coordination visibility)
- "Agent offline/busy" (capacity signaling)

**New table:**

```sql
CREATE TABLE IF NOT EXISTS team_status (
    squad          TEXT    PRIMARY KEY,
    status         TEXT    NOT NULL,      -- 'online' | 'offline' | 'busy' | 'degraded'
    message        TEXT,                  -- optional human-readable
    updated_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS team_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    squad          TEXT    NOT NULL,
    event_type     TEXT    NOT NULL,      -- 'milestone.completed' | 'error' | 'status_change'
    data           TEXT,                  -- JSON payload
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS team_events_squad_idx ON team_events(squad);
CREATE INDEX IF NOT EXISTS team_events_created_idx ON team_events(created_at DESC);
```

**New REST endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/api/v1/status` | Team sets its own status (bearer auth identifies team) |
| GET | `/api/v1/status` | Get all team statuses (for coordinator visibility) |
| GET | `/api/v1/status/{squad}` | Get specific team status |
| POST | `/api/v1/events` | Team pushes an event (milestone, error, etc.) |
| GET | `/api/v1/events` | List recent team events (filterable by squad) |

**New MCP tools:**

```python
set_team_status(status: str, message: Optional[str] = None) -> str
# status must be: online | offline | busy | degraded
# Actor from auth context = squad name

get_team_status(squad: Optional[str] = None) -> str
# Returns single team or all teams

post_team_event(event_type: str, data: Optional[dict] = None) -> str
# event_type: milestone.completed | error | custom
# Actor from auth context = squad name

get_team_events(squad: Optional[str] = None, limit: int = 50) -> str
# List recent events, newest first
```

### 3.2 Real-Time SSE Stream (Optional)

If teams need real-time updates without polling, add an SSE endpoint:

```
GET /api/v1/events/stream?event_types=task.created,server.stats
Accept: text/event-stream
```

**Considerations:**
- Same transport stability concerns as the current `--http` mode
- Apply `ConnectionTimeoutMiddleware` (max 60s connection age)
- Use for low-latency needs only; webhooks are preferred for reliability

**Decision:** Defer SSE to Phase 3. Webhooks + polling sufficient for Phase 1-2.

---

## 4. Integration with Existing Systems

### 4.1 Activity Log

Both inbound and outbound messaging events SHOULD be logged:
- Outbound subscription fires: Add action `subscription.fired` to activity_log
- Inbound team status: Add action `team.status_changed` to activity_log

**Schema addition:**

```sql
-- activity_log already has flexible action field; no schema change needed
-- Actions: subscription.fired, team.status_changed, team.event_received
```

### 4.2 Webhooks

**Phase 1 coexistence:**
- `webhooks` table remains for task events
- `event_subscriptions` table handles server/project events
- Both use same `_check_ssrf()` validation
- Both use same HMAC-SHA256 signing pattern

**Future consolidation (v0.4.0):**
- Consider merging tables if distinction becomes cumbersome
- Task events could become just another `event_type` in unified subscriptions

---

## 5. Build Order

### Placement in v0.2.0 Sequence

Current v0.2.0 build order:
1. due-dates ✓
2. full-text-search ✓
3. bulk-operations
4. activity-log
5. export-import
6. rest-api
7. webhooks

**Messaging phases:**

| Phase | Name | Build Order | Depends On |
|-------|------|-------------|------------|
| 1a | Server state query tools | **8** | rest-api (for REST endpoints) |
| 1b | Event subscriptions | **9** | webhooks (reuses SSRF, HMAC patterns) |
| 2 | Inbound team status | **10** | rest-api |

**Rationale:**
- Phase 1a (query tools) can start immediately after rest-api is stable
- Phase 1b (subscriptions) must follow webhooks to reuse infrastructure
- Phase 2 (inbound) is independent but should follow Phase 1 for consistency

---

## 6. Open Questions for Andrew

1. **Periodic intervals:** What granularity for `server.stats` push? Default 60s, cap at 86400s (daily)?

2. **Team status semantics:** Should `offline` teams automatically have their tasks reassigned, or is this purely informational?

3. **Event retention:** How long to keep `team_events` history? Default 30 days with pruning?

4. **SSE priority:** Is real-time SSE needed for Phase 2, or is webhook + polling sufficient?

5. **Cross-project visibility:** Can any authenticated team see all teams' status, or should there be project-level isolation?

---

## 7. Implementation Notes for Darlene

### Phase 1a Checklist
- [ ] Add `get_server_stats()` MCP tool (query task counts, uptime)
- [ ] Add `get_project_summary(project)` MCP tool
- [ ] Add `/api/v1/projects/{project}/summary` REST endpoint
- [ ] Enhance `/api/v1/stats` with subscription counts

### Phase 1b Checklist
- [ ] Add `event_subscriptions` table to `_SCHEMA`
- [ ] Implement `subscribe_events`, `list_subscriptions`, `unsubscribe_events` MCP tools
- [ ] Add REST endpoints for subscriptions
- [ ] Implement `_fire_event_subscriptions()` (copy pattern from `_fire_webhooks`)
- [ ] Add periodic task loop in server lifespan for interval-based events
- [ ] Fire `server.health` on startup/shutdown

### Phase 2 Checklist
- [ ] Add `team_status` and `team_events` tables
- [ ] Implement team status MCP tools and REST endpoints
- [ ] Log inbound events to activity_log

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Periodic events overwhelm subscribers | Medium | Min interval 60s; rate-limit per subscriber |
| Inbound status spam from malicious teams | Low | Rate-limit inbound endpoints (5/min/squad) |
| team_events unbounded growth | Medium | Prune events older than 30 days (v0.3.0 scope) |
| SSE connections exhaust server | High | Defer SSE; use webhooks; apply ConnectionTimeoutMiddleware |

---

## 9. Summary

**Phase 1 (Build orders 8-9):**
- Add query tools for server stats and project summaries
- Add event subscription system extending webhooks pattern
- Push `server.stats`, `server.health`, `project.summary` events

**Phase 2 (Build order 10):**
- Teams can push status updates and events to OPM
- Enables coordination visibility (who's online, milestones completed)

**Phase 3 (Future):**
- Optional SSE real-time stream
- Webhook/subscription table consolidation

This design extends rather than replaces the existing webhook infrastructure, maintains consistency with the `create_server()` closure pattern, and provides clear phasing for incremental delivery.
