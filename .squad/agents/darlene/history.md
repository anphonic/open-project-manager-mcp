# Darlene — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Backend Dev. I implement server.py and all MCP tools.

## Session Log

### 2026-03-31 — Initial implementation session

**Status:** Did not author code this session.

The coordinator (GitHub Copilot CLI) built `server.py` and all 11 tools directly, bypassing squad routing. Darlene was not invoked.

**What was built (by coordinator, on Darlene's behalf):**
- `src/open_project_manager_mcp/server.py`: `create_server(db_path)` factory, 11 tools as closures, `asyncio.Lock` for writes, SQLite via stdlib `sqlite3`
- Tools: `create_task`, `update_task`, `complete_task`, `delete_task`, `get_task`, `list_tasks`, `add_dependency`, `remove_dependency`, `list_ready_tasks`, `list_projects`, `get_stats`
- `human_approval=True` on `delete_task`
- Priority sort: critical > high > medium > low
- All 44 tool tests pass

**Process note:** In future sessions, `server.py` and tool work should be routed to Darlene.

### 2026-03-31 — Backend review (v0.1.0 review round)

**Task:** Review `server.py` and all tool implementations against CHARTER design principles.

**Fixes made:**
- `list_tasks`: was returning full row dicts → fixed to compact payload `(id, title, priority, status, assignee)` per CHARTER list-endpoint design principle
- `list_ready_tasks`: same full-row return issue → same compact fix applied
- `limit=0`: accepted and forwarded to SQLite `LIMIT 0`, silently returning no results → clamped to minimum 1
- Updated docstrings on both list tools to document the compact payload shape

### 2026-03-31 — Multi-tenant bearer token auth

**Task:** Implement OPM_TENANT_KEYS support (mirrors squad-knowledge-mcp pattern).

**Work completed:**
- `_load_tenant_keys()` in `__main__.py` — reads `OPM_TENANT_KEYS` env var, normalizes old/new formats into `{squad: token}` dict
- `ApiKeyVerifier` class in `server.py` — constant-time `hmac.compare_digest` Bearer token validation
- `AuthSettings` wired into `FastMCP` when `tenant_keys` provided
- `create_server()` updated to accept `tenant_keys: dict[str, str] | None` and `server_url: str | None`
- `--generate-token SQUAD_NAME` CLI flag — prints cryptographically secure token + setup instructions, exits
- `_check_network_auth()` updated with `tenant_keys` parameter for context-aware warnings

**Test results:** +11 tests → 81/81 passing.

### 2026-04-01 — Implemented all 7 v0.2.0 features

**Task:** Implement all features per Elliot's architecture brief, incorporating Trenton (schema) and Mobley (API/webhook) review notes.

**Features implemented:**

1. **due-dates:** `due_date` column migration (try/except OperationalError); ISO 8601 validation; `list_overdue_tasks`, `list_due_soon_tasks` tools
2. **full-text-search:** FTS5 virtual table + 3 triggers + post-schema rebuild; `search_tasks` with BM25 ranking; `_fts_available` flag; error messages sanitized (Dom)
3. **bulk-operations:** `create_tasks`, `update_tasks`, `complete_tasks`; `_validate_create/update_params()` helpers; single transaction per call; per-item error collection; `_BULK_MAX=50`; ID validation on `complete_tasks` (Dom)
4. **activity-log:** `activity_log` table + indexes; `_log()` helper; per-field old/new tracking in all write paths; `get_task_activity` orphan-safe — no task existence check (Trenton); actor resolution from MCP context
5. **export-import:** `export_all_tasks` with project-filtered dep subset; `import_tasks` with merge mode; full field validation; single transaction; 5MB cap
6. **rest-api:** `_build_rest_router()` inner function returning Starlette Router; 7 endpoints (GET/POST /tasks, GET/PATCH/DELETE /tasks/{id}, GET /projects, GET /stats); `--rest-api` CLI flag; `enable_rest` param on `create_server()`; 1MiB body cap (Dom); existence check before activity log in PATCH (Mobley)
7. **webhooks:** `webhooks` table; `register_webhook` with SSRF guard — IPv4-mapped IPv6 fixed (Dom), `getaddrinfo` in `run_in_executor` (Trenton/Dom); GC-safe `_background_tasks` set (Mobley); task data captured before DELETE for `task.deleted` payload (Mobley); HMAC-SHA256 signing; `httpx` optional dep guarded with ImportError; tag length/count caps (Dom)

**Test count:** 188 tests passing post-implementation (before Romero gap analysis).

### 2026-04-01 — Self-service token registration implementation

**Task:** Implement `POST /api/v1/register` + `DELETE /api/v1/register/{squad}` per Elliot's brief (`darlene-brief-register.md`).

**Work completed in `server.py` and `__main__.py`:**

- **Schema:** `tenant_keys` table appended to `_SCHEMA` (`CREATE TABLE IF NOT EXISTS` — idempotent, no migration block)
- **`_verify_bearer` closure:** Defined inside `create_server()` after `_lock`; env var keys first, DB re-queried on every call on miss; shared by both `ApiKeyVerifier` and REST `_check_auth`
- **`ApiKeyVerifier` refactored:** `__init__` now accepts `verify_fn: Callable` only; class is testable in isolation; `verify_token` delegates entirely to closure
- **`_check_auth` updated:** Unauthenticated mode (no env keys + empty `tenant_keys` table) still returns `"system"` actor; authenticated path calls `_verify_bearer`
- **Rate limiter:** `_check_rate_limit(ip)` inside `_build_rest_router()` using `defaultdict(list)` + `time.monotonic()`; `_RATE_WINDOW=60.0`, `_RATE_MAX=5`; no external deps
- **`_SQUAD_RE`:** `re.compile(r'^[a-zA-Z0-9_-]{1,64}$')` squad name validation
- **`register_endpoint`:** `POST /register`; reads `OPM_REGISTRATION_KEY` via `os.environ.get`; rate-limited; validates squad name + key; `409` on duplicate; inserts and commits; `201` + `secrets.token_urlsafe(32)` + one-time note
- **`deregister_endpoint`:** `DELETE /register/{squad}`; key in `X-Registration-Key` header; constant-time compare; `Response(status_code=204)` on success; `404` if not found
- **Routes added:** `/register` (POST) and `/register/{squad:str}` (DELETE) appended to `_build_rest_router()` route list
- **`__main__.py`:** `OPM_REGISTRATION_KEY` length warning — warns to stderr if set but < 16 chars
- **New imports added to `server.py`:** `import os`, `import re`, `import secrets`, `import time`, `from collections import defaultdict`

**Tests:** 26 new tests in `tests/test_registration.py` → **250 total** (all passing). Covers: 404 disabled, 401 wrong key, 400 invalid squad, 201 success + DB row, 409 duplicate, 429 rate limit, 401/404/204 deregister paths, DB token in `_check_auth`, env var precedence, unauthenticated mode, startup warning.

## Learnings
