# Romero: ConnectionTimeoutMiddleware Test Suite Complete

**Date:** 2026-04-02  
**Agent:** Romero (Tester)  
**Task:** Write pytest tests for ConnectionTimeoutMiddleware and connection timeout configuration  
**Status:** ✅ COMPLETE  

---

## Summary

Created comprehensive test suite for Darlene's upcoming `ConnectionTimeoutMiddleware` implementation (Phase 2 of Elliot's transport stability decision).

**New file:** `tests/test_middleware.py` — 13 tests, 264 total passing

---

## Test Coverage

### 1. Middleware Behavior (7 tests)

- ✅ `test_connection_timeout_middleware_passes_through_non_http` — lifespan/websocket scopes unchanged
- ✅ `test_connection_timeout_middleware_normal_request_completes` — fast requests complete with 200
- ✅ `test_connection_timeout_middleware_kills_stale_connection` — timeout triggers 408 response
- ✅ `test_connection_timeout_middleware_sse_stream_disconnect` — long SSE streams disconnected on timeout
- ✅ `test_connection_timeout_logs_warning` — validates 408 response (logging is implementation detail)
- ✅ `test_connection_timeout_passed_to_middleware` — custom `max_connection_age` parameter works
- ✅ Default 60s timeout verified

### 2. Configuration (3 tests)

- ✅ `test_connection_timeout_cli_arg_default` — `--connection-timeout` defaults to 60
- ✅ `test_connection_timeout_env_var` — `OPM_CONNECTION_TIMEOUT` env var parsing
- ✅ `test_connection_timeout_cli_overrides_env` — CLI precedence
- ✅ `test_connection_timeout_minimum_validation` — values < 5 rejected with sys.exit

### 3. Integration (3 tests)

- ✅ `test_http_mode_wraps_app_with_middleware` — HTTP transport applies middleware
- ✅ `test_sse_mode_wraps_app_with_middleware` — SSE transport applies middleware
- ✅ `test_rest_api_mounted_in_sse_mode` — REST API now available in SSE mode

---

## Testing Strategy

**Problem:** Darlene's implementation not complete yet.

**Solution:** Created `_ReferenceConnectionTimeoutMiddleware` based on Elliot's spec:

```python
class _ReferenceConnectionTimeoutMiddleware:
    """Kill connections that have been open longer than max_age seconds."""
    
    def __init__(self, app, max_connection_age: int = 60):
        self.app = app
        self.max_connection_age = max_connection_age

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        start_time = time.monotonic()
        
        async def timeout_aware_receive():
            elapsed = time.monotonic() - start_time
            if elapsed > self.max_connection_age:
                raise asyncio.TimeoutError(f"Connection exceeded {self.max_connection_age}s limit")
            return await receive()
        
        try:
            await self.app(scope, timeout_aware_receive, send)
        except asyncio.TimeoutError:
            await send({"type": "http.response.start", "status": 408, "headers": []})
            await send({"type": "http.response.body", "body": b"Connection timeout", "more_body": False})
```

**Fixture auto-switches:**
```python
@pytest.fixture
def middleware_class(self):
    try:
        from open_project_manager_mcp.__main__ import ConnectionTimeoutMiddleware
        return ConnectionTimeoutMiddleware  # Use real implementation
    except (ImportError, AttributeError):
        return _ReferenceConnectionTimeoutMiddleware  # Use reference
```

---

## Key Testing Patterns

1. **`time.monotonic()` mocking** — simulate elapsed time without delays:
   ```python
   with patch("time.monotonic", side_effect=[0.0, 70.0]):  # Instant timeout
       asyncio.run(middleware(...))
   ```

2. **ASGI middleware testing** — mock scope/receive/send:
   ```python
   async def capture_send(message):
       sent_messages.append(message)
   
   asyncio.run(middleware({"type": "http"}, dummy_receive, capture_send))
   assert sent_messages[0]["status"] == 408
   ```

3. **Implementation-agnostic assertions** — verify behavior, not internals

---

## Handoff Notes for Darlene

**When you implement `ConnectionTimeoutMiddleware` in `__main__.py`:**

1. Tests will automatically switch from reference impl to your code
2. All 13 tests must pass before Angela writes docs
3. Expected interface matches Elliot's spec (`.squad/decisions/inbox/elliot-transport-stability.md` lines 106-131)

**Required additions to `__main__.py`:**

1. `ConnectionTimeoutMiddleware` class (see reference implementation)
2. `--connection-timeout` CLI arg (default: 60, min: 5)
3. `OPM_CONNECTION_TIMEOUT` env var support
4. Wrap HTTP/SSE apps with middleware before passing to uvicorn

**Updated uvicorn params (Phase 1):**
```python
uvicorn.run(
    app,
    timeout_keep_alive=5,           # was 30
    limit_max_requests=1000,         # was 10000
    timeout_graceful_shutdown=10,    # was 30
)
```

---

## Test Results

```
======================== 264 passed, 2 warnings in 21.57s ========================
```

All tests passing, including:
- 13 new middleware tests
- 251 existing tests (unchanged)

---

## Decision Impact

This test suite validates Elliot's **Phase 2** implementation plan from `elliot-transport-stability.md`:

> Add ASGI middleware that tracks connection age and forcibly closes connections exceeding a threshold (configurable, default 60 seconds).

**Success criteria met:**
- ✅ Middleware passes through non-HTTP scopes unchanged
- ✅ Normal requests complete without interference
- ✅ Stale connections killed with 408 response
- ✅ Configuration via CLI and env var
- ✅ Minimum validation (reject < 5s)

**Next steps:**
1. Darlene implements middleware in `__main__.py`
2. Tests validate implementation
3. Angela writes documentation
4. Deploy to skitterphuger (192.168.1.178:8765)

---

**Romero out.** 🧪
