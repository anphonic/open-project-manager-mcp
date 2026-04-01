# Elliot — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Lead & Architect. I own design decisions and ensure consistency with squad-knowledge-mcp patterns.

## Session Log

### 2026-03-31 — Architecture review (v0.1.0 review round)

**Task:** Review design consistency with CHARTER and squad-knowledge-mcp patterns.

**Critical fix — TransportSecuritySettings wiring:**
- `TransportSecuritySettings` was constructed in `__main__.py` but passed nowhere; `create_server()` had no `transport_security` parameter. The object was silently discarded.
- Impact: LAN clients would have been rejected (or auth settings ignored entirely) without this fix.
- Added `transport_security` parameter to `create_server()` in `server.py`
- Wired parameter through to `FastMCP` constructor call
- Updated `__main__.py` to pass the object through
- Updated `test_config.py` `fake_create_server` to accept `**kwargs`

**Flagged for follow-up:**
- GET `/stats` HTTP endpoint is in CHARTER scope but not yet implemented.

### 2026-04-01 — Architecture decisions for v0.2.0 (7 features)

**Task:** Design all 7 v0.2.0 features; produce brief for Darlene.

**Deliverables:**
- Full v0.2.0 architecture section in `.squad/decisions.md` (schema DDL, tool signatures, integration points, risks, build order)
- `.squad/agents/elliot/darlene-brief-v0.2.0.md`

**Features designed:**
1. **due-dates** — `due_date TEXT` nullable column; `list_overdue_tasks`, `list_due_soon_tasks` tools; ISO 8601 lexicographic sort
2. **full-text-search** — FTS5 virtual table + 3 triggers (insert/update/delete); `search_tasks` with BM25; `_fts_available` guard for distros without FTS5
3. **bulk-operations** — `create_tasks`, `update_tasks`, `complete_tasks`; `_BULK_MAX=50`; shared validation helpers; per-item error collection; single transaction
4. **activity-log** — `activity_log` table; `_log()` inner helper; per-field change tracking; `get_task_activity` tool; actor from MCP context
5. **export-import** — `export_all_tasks` with dep subset logic; `import_tasks` merge mode; 5MB cap; field validation
6. **rest-api** — `/api/v1` mounted before MCP catch-all; `--rest-api` opt-in flag; 7 endpoints; same bearer auth; closes CHARTER GET /stats gap
7. **webhooks** — `webhooks` table; HMAC-SHA256; SSRF blocklist (RFC1918 + loopback + link-local); `httpx` optional; fire-and-forget via `asyncio.create_task`

**Flagged open item (from Dom's audit):** DNS rebinding — registration-time SSRF check only. Needs decision: re-validate on each fire vs accept HTTPS cert validation as sufficient mitigation.

### 2026-04-01 — Self-service token registration architecture

**Task:** Design `POST /api/v1/register` + `DELETE /api/v1/register/{squad}` — self-service bearer token provisioning extending v0.2.0 REST API.

**Deliverables:**
- "Self-Service Token Registration" section in `.squad/decisions.md` (10 decisions, summary table)
- `.squad/agents/elliot/darlene-brief-register.md` — full step-by-step implementation brief for Darlene

**Key decisions:**
1. **Storage:** `tenant_keys` table (`squad TEXT PK`, `key TEXT NOT NULL`, `created_at TEXT NOT NULL`) appended to `_SCHEMA` — idempotent `CREATE TABLE IF NOT EXISTS`
2. **Auth lookup:** Re-query DB on every auth call — no cache, no server restart required when new squad registers
3. **Precedence:** Env var keys first (constant-time); DB keys on miss
4. **`POST /api/v1/register`:** `404` if `OPM_REGISTRATION_KEY` unset; rate-limited 5/min/IP; squad name `[a-zA-Z0-9_-]{1,64}`; `409` duplicate; `201` + one-time token
5. **`ApiKeyVerifier` refactored:** Accepts `verify_fn: Callable` — shared `_verify_bearer` closure eliminates duplication between MCP and REST auth paths
6. **MCP auth scope:** DB-registered keys = REST access only. `if tenant_keys:` guard on `token_verifier`/`auth_settings` preserved — MCP auth requires env var keys
7. **Plaintext token storage:** Consistent with existing local-first posture
8. **`DELETE /api/v1/register/{squad}`:** `X-Registration-Key` header; `204` on success
9. **`--generate-token` CLI:** Unchanged — stdout-only, no DB write
10. **`OPM_REGISTRATION_KEY` min length:** 16 chars startup warning to stderr

## Learnings
