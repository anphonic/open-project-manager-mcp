# Romero — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Tester. I write pytest tests for all MCP tools.

## Session Log

### 2026-03-31 — Initial implementation session

**Status:** Did not author tests this session.

The coordinator (GitHub Copilot CLI) wrote the full test suite directly, bypassing squad routing. Romero was not invoked.

**What was built (by coordinator, on Romero's behalf):**
- `tests/test_tools.py`: 44 tests using real SQLite via `pytest` `tmp_path` fixture
- `tests/test_config.py`: 16 tests covering transport, auth, and `_FixArgumentsMiddleware`
- Test access pattern: `server._tool_manager._tools["tool_name"].fn` with `_sync_wrap()` helper
- 60/60 tests passing

**Process note:** In future sessions, all test authoring should be routed to Romero.

### 2026-03-31 — Test review (v0.1.0 review round)

**Task:** Review and extend the test suite; identify coverage gaps.

**New tests added (+10, 60 → 70):**
1. `update_task` — tags field update
2. `update_task` — assignee field update
3. `list_tasks` — assignee filter
4. `list_tasks` — priority filter
5. Dependency error when first task is missing
6. `list_ready_tasks` — assignee filter
7. `get_stats` — oldest_open non-null branch
8–10. `TestSseTransport` — 3 new SSE transport tests

**Cleanup:**
- Removed dead middleware assignment in `test_coerces_empty_list_to_empty_dict`

**Result:** 70/70 tests passing.

### 2026-04-01 — Gap analysis + 36 new tests for v0.2.0 (81 → 224 total)

**Task:** Review all 7 v0.2.0 feature implementations for coverage gaps; add missing tests.

**New tests added (+36, 188 → 224):**

Coverage added for:
- `list_overdue_tasks` / `list_due_soon_tasks` edge cases (no due date set, boundary day, `days` cap enforcement)
- FTS5 unavailability path (`_fts_available=False` — `search_tasks` returns graceful error)
- Bulk partial-failure scenarios (mixed valid/invalid items in single call)
- `get_task_activity` orphan read (activity entries accessible after task deletion)
- `export_all_tasks` dep-subset filtering (deps excluded when one task is outside project filter)
- `import_tasks` merge mode (skip existing) and conflict mode (abort with conflict list)
- REST API auth enforcement (401 on missing/invalid token for all endpoints)
- REST endpoint coverage (all 7 endpoints: happy path + error cases)
- Webhook SSRF guard (RFC1918, loopback, IPv4-mapped IPv6)
- Webhook GC-safe task reference (`_background_tasks` set populated and cleaned)
- `task.deleted` payload capture (data present in webhook payload despite row being deleted)

**All 224 tests passing.**

## Learnings
