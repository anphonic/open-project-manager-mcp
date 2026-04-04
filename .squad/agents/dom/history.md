# dom history

## Learnings

_(Fresh start — 2026-04-03)_

### 2026-04-04: v0.3.0 Security Audit

**Telemetry findings:**
- `_record_metric()` correctly sources tenant_id from authenticated context, never user input
- Hourly bucketing naturally limits row growth (UPSERT pattern)
- All telemetry queries properly scope to calling tenant via `_get_actor()`

**Permissions findings:**
- Empty string bypass was a real risk — `os.environ.get()` semantics can be tricky
- Always use explicit value checks like `== "1"` instead of boolean truthy checks for env vars
- Role hierarchy (owner=3, contributor=2, reader=1) correctly prevents escalation
- human_approval=True is consistently required on all destructive permission operations

**Fixes applied:**
1. Added `_MAX_TELEMETRY_HOURS = 720` to cap lookback queries (DoS prevention)
2. Fixed `OPM_ENFORCE_PERMISSIONS` to require explicit `"1"` value
3. Added try/except around REST query parameter parsing

**Key patterns to remember:**
- `_get_actor()` is the single source of truth for tenant identity
- Permission checks happen at tool level, not DB level
- "system" tenant bypasses all permission checks (dev mode)

---

### 2026-04-05: v0.3.0 Sprint Complete

**Delivered:** Security audit with 4 critical fixes applied.

**Security improvements:**
- DoS prevention: Telemetry hours capped at 720
- Permission bypass fix: `OPM_ENFORCE_PERMISSIONS` requires explicit "1"
- Input validation: REST query parameters wrapped in try/except
- Auth check: System tenant explicitly validated in telemetry endpoints
