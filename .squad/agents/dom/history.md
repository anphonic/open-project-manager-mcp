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
