# Decision: v0.3.0 Test Coverage Strategy

**Author:** Romero (Tester)  
**Date:** 2026-04-04  
**Status:** Implemented

## Context

v0.3.0 introduces two major features (telemetry and permissions) that require comprehensive test coverage before Angela writes documentation. Tests needed to be written against the architecture spec since Darlene's implementation is still in progress.

## Decision

Write comprehensive pytest tests for all v0.3.0 features using a test-first approach:

### Telemetry Tests (`tests/test_telemetry.py`)
- Test `_record_metric()` helper function behavior (metric creation, upsert, latency, errors, bucketing)
- Test all 4 telemetry query tools (get_telemetry_summary, get_telemetry_by_tool, list_top_tools, get_error_summary)
- Test that existing tools (create_task, update_task, get_task) call `_record_metric()`
- Test fire-and-forget behavior (non-blocking, graceful degradation)

### Permissions Tests (`tests/test_permissions.py`)
- Test default-deny behavior when permissions are enforced
- Test each role's capabilities (owner, contributor, reader)
- Test role hierarchy (owner > contributor > reader)
- Test all permissions management tools (grant, revoke, list, migrate)
- Test backward compatibility (permissions disabled mode)
- Use `OPM_ENFORCE_PERMISSIONS` env var to test both modes

### Test Fixtures
- `mcp_server` — Standard server without permissions enforcement
- `mcp_server_with_perms` — Server with `OPM_ENFORCE_PERMISSIONS=1`
- `db_conn` — Direct SQLite connection for low-level verification

## Rationale

1. **Test-first ensures API correctness** — Writing tests before implementation catches design issues early
2. **Tests document expected behavior** — Comprehensive tests serve as executable specification
3. **Direct DB inspection validates state** — Many behaviors (metrics recording, permissions) are best verified by querying tables directly
4. **Environment variable gating** — Allows testing both new (enforced) and legacy (open) permission modes
5. **Expected failures are OK** — Tests fail with KeyError until Darlene implements the tools, which is expected and useful

## Results

- **47 tests written** (18 telemetry + 29 permissions)
- **21 passing** (44.7%) — tests that don't require new tools
- **26 failing** (55.3%) — expected failures due to missing tools
- All failures are clean KeyError exceptions for missing tools
- Tests are ready for implementation — will turn green as Darlene adds features

## Implications

- Angela can reference tests to understand expected behavior when writing docs
- Darlene has clear acceptance criteria for each tool
- Tests will immediately validate implementation correctness
- Future changes to v0.3.0 features are regression-protected

## Alternatives Considered

1. **Wait for implementation before writing tests** — Rejected: loses test-first benefits, delays feedback
2. **Mock-based tests** — Rejected: doesn't test real database behavior, less valuable for integration
3. **Minimal tests only** — Rejected: insufficient coverage for major features

## Related Work

- Architecture: `.squad/agents/elliot/v03-architecture.md`
- Implementation brief: `.squad/agents/elliot/darlene-brief-v03.md`
- Tests: `tests/test_telemetry.py`, `tests/test_permissions.py`
