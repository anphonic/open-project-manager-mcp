# Trenton — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Database & Data Pipeline Specialist. I review schema DDL, migration patterns, index design, FTS, and data pipeline tools (export/import).

## Session Log

### 2026-04-01 — Hired + Schema review for v0.2.0

Brought in as Database & Data Pipeline Specialist for v0.2.0 feature work.
Scope: FTS5, due-dates schema, bulk operations, export/import, activity-log DDL.

**Task:** Review Elliot's schema decisions before Darlene begins implementation.

**Artefact:** `.squad/agents/trenton/schema-review-v0.2.0.md`

**Critical catches (4):**

1. **Orphan-safe activity log** — `get_task_activity` must not require task existence. Deleted-task audit trails need to remain accessible. Task existence check removed.

2. **FTS5 trigger validation** — Startup smoke-test (`SELECT fts5(1)` in try/except) recommended to confirm FTS5 availability before creating the virtual table. Prevents silent schema corruption on FTS5-less SQLite builds.

3. **Composite index recommendation** — `activity_log(task_id, created_at DESC)` composite index covers the primary query pattern better than two separate single-column indexes. Recommendation adopted.

4. **Blocking DNS in async SSRF check** — `socket.getaddrinfo()` is synchronous and blocks the event loop. Recommended wrapping in `asyncio.get_event_loop().run_in_executor(None, ...)` for the async webhook registration path. Fix applied by Darlene; Dom reinforced the same finding independently.

## Learnings
