# Decision: v0.3.0 Security Hardening

**Author:** Dom DiPierro (Security Expert)  
**Date:** 2026-04-04  
**Status:** IMPLEMENTED

---

## Context

Security audit of v0.3.0 telemetry and permissions implementation revealed 4 vulnerabilities that were fixed directly in `server.py`.

## Decisions

### 1. Telemetry Hours Cap (DoS Prevention)

**Decision:** Cap `hours` parameter at 720 (30 days) in all telemetry query tools.

**Rationale:** Unbounded lookback queries could cause database performance degradation or service denial. 30 days provides sufficient historical depth while preventing abuse.

**Implementation:** Added `_MAX_TELEMETRY_HOURS = 720` constant and `max(1, min(hours, _MAX_TELEMETRY_HOURS))` clamping in:
- `get_telemetry_summary()`
- `get_telemetry_by_tool()`
- `list_top_tools()`
- `get_error_summary()`

### 2. Explicit Permission Enforcement Flag

**Decision:** `OPM_ENFORCE_PERMISSIONS` must be exactly `"1"` to enable enforcement.

**Rationale:** Previous code used `os.environ.get()` truthiness check which could be bypassed with empty string or any non-empty value that wasn't explicitly documented. Explicit value matching eliminates ambiguity.

**Implementation:**
```python
enforce = os.environ.get("OPM_ENFORCE_PERMISSIONS", "")
if enforce != "1":
    return None  # Disabled
```

### 3. REST Input Validation

**Decision:** All REST query parameters must be validated with try/except before conversion.

**Rationale:** Invalid input like `?hours=abc` would cause unhandled exceptions, potentially leaking stack traces or causing service errors.

**Implementation:** Added try/except around `int()` conversions in REST telemetry endpoints with 400 response on failure.

## Impact

- No breaking changes to API contracts
- More predictable behavior for edge cases
- Reduced attack surface for DoS and bypass attacks

## Documentation Required

- Document that `OPM_ENFORCE_PERMISSIONS=1` is the only value that enables enforcement
- Update API docs to note 720-hour maximum for telemetry queries
