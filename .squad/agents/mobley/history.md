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
