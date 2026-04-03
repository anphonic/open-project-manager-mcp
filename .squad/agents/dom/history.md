# Dom — History

## Core Context
- Project: open-project-manager-mcp
- Stack: Python, SQLite (stdlib), FastMCP
- Sibling: squad-knowledge-mcp at J:\Coding\squad-knowledge-mcp
- Squad Knowledge Server: http://192.168.1.178:8766/mcp
- Requested by: Andrew (project owner)

## Role
Security Expert. I audit server.py, transport layer, input handling, and authentication for vulnerabilities.

## Session Log

### 2026-03-31 — Onboarded + Security audit (v0.1.0 review round)

Cast into the squad to fill the security role gap identified after the initial implementation session.

**Audit scope:** `server.py`, `__main__.py`, transport/auth layer.

**Findings and fixes (5 issues):**
1. **f-string column injection (defence-in-depth):** `update_task` interpolated column names via f-string — currently unexploitable but not fail-safe → added `_VALID_UPDATE_COLUMNS` allowlist; raises `ValueError` on unknown column
2. **DoS via unbounded limit:** `list_tasks` and `list_ready_tasks` accepted arbitrary `limit` values → added `_MAX_LIMIT = 500` cap
3. **Internal error leakage:** `conn.execute()` calls were unwrapped — raw SQLite stack traces could leak to callers → wrapped all in `try/except sqlite3.Error`
4. **Input length validation:** No bounds on text field lengths — oversized inputs could cause unbounded writes → added `_MAX_SHORT_FIELD = 500` (id/title/project/assignee) and `_MAX_DESCRIPTION = 50_000`; enforced in `create_task` and `update_task`
5. **Path traversal via --db-path:** Documented as Info/low risk for v1 single-tenant; no fix required at this time

**Artefact:** `.squad/agents/dom/security-audit-v0.1.0.txt`

### 2026-03-31 — Auth implementation aligns with security recommendations

Dom was not directly invoked this session. Darlene implemented multi-tenant bearer token auth (OPM_TENANT_KEYS) which addresses the outstanding network auth gap flagged in the v0.1.0 audit (item 5: path traversal note mentioned single-tenant scope; auth was the broader concern for network mode).

**Alignment with prior recommendations:**
- `ApiKeyVerifier` uses `hmac.compare_digest` — constant-time comparison, immune to timing attacks
- Tokens generated via `secrets.token_hex(32)` — cryptographically secure, 256-bit entropy
- Missing/malformed Authorization headers return 401 before any tool logic executes
- Three squads provisioned on skitterphuger; tokens stored chmod 600

### 2026-04-01 — Security audit v0.2.0 — 8 fixes, 1 open item

**Audit scope:** All 7 v0.2.0 features (due-dates, FTS5, bulk-ops, activity-log, export-import, REST API, webhooks).

**Artefact:** `.squad/agents/dom/security-audit-v0.2.0.txt`

**Fixes applied (8):**

1. **IPv4-mapped IPv6 SSRF bypass (HIGH)** — `::ffff:192.168.1.x` format bypassed RFC1918 check. Fixed via `ipaddress.ip_address().ipv4_mapped` normalisation before blocklist check.
2. **REST body size cap 1MiB (HIGH)** — No limit on POST/PATCH body size. Added 1MiB cap in REST parsing layer.
3. **`_FixArgumentsMiddleware` 6MB cap (MEDIUM)** — Existing MCP middleware had no body cap. Added 6MB limit.
4. **FTS5 error message sanitized** — Raw SQLite FTS5 errors leaked internal schema. Now returns generic "search failed" string.
5. **Tag length cap** — Per-tag character limit added to `create_task` / `update_task`.
6. **Tag count cap** — Maximum number of tags per task enforced.
7. **`complete_tasks` ID validation** — Bulk complete now validates each ID format/length before querying.
8. **REST required-field guards** — POST /tasks hardened to return 400 in all missing-field code paths.

**Open item — DNS rebinding:**
Registration-time SSRF check only. A low-TTL DNS rebinding attack could pivot a registered HTTPS webhook URL to an internal address after registration. Flagged for Elliot: decision needed — re-validate hostname on each webhook fire vs accept HTTPS certificate validation as sufficient mitigation. Recorded in `.squad/decisions.md`.

### 2026-04-03 — Project-Level Permissions Design

**Request:** Andrew requested a full security model for project-level data privacy and permissions. Current state: bearer tokens identify squads, but `project` is just a filter string — any squad can read/write any project's tasks.

**Deliverable:** `.squad/decisions/inbox/dom-project-permissions-design.md`

**Key design decisions:**
1. **Per-project ACL model** — simpler than RBAC or capability tokens for our 3-10 squad scale
2. **Three roles:** owner (full control), contributor (task CRUD), reader (view-only)
3. **Explicit project creation** — no implicit creation via `create_task`; projects must exist first
4. **Private by default** — new projects accessible only to creator until grants made
5. **Single enforcement layer** — `_check_project_access()` helper prevents scattered checks across 23+ tools

**Threat model covers:**
- Cross-squad data access, project squatting, task injection, enumeration, notification leakage
- Privilege escalation paths: role confusion, self-grant, orphan projects
- Edge cases: non-existent project, cross-project deps, move task between projects

**Open questions for Elliot:**
- Admin override squad (OPM_ADMIN_SQUAD)?
- Cross-project dependencies allowed?
- Notification system composability
- Default project fate (open/migrate/deprecate)

**Schema additions:** `projects` table, `project_access` table with CHECK constraint on roles.

## Learnings

- OPM currently has ~30 MCP tools plus REST endpoints — access control must use single helper to avoid repetition
- `_get_actor()` already extracts squad from bearer token context; this becomes the ACL principal
- Activity log (`activity_log` table) captures `actor` — useful for migration (determine who created tasks)
- Webhooks have optional `project` field — access checks needed both at registration and fire time
- Migration is the hardest part: existing tasks have project strings but no ownership metadata
