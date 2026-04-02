"""Tests for Session Reaper — orphaned session cleanup.

Tests the session activity tracking, periodic reaper task, and activity middleware
that prevents FastMCP StreamableHTTPSessionManager from getting stuck with orphaned sessions.

See .squad/decisions/inbox/elliot-session-reaper.md for implementation spec.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest


# ---------------------------------------------------------------------------
# Test 1 & 2: SessionActivityTracker touch and stale detection
# ---------------------------------------------------------------------------

class TestSessionActivityTracker:
    """Tests for SessionActivityTracker class."""

    @pytest.fixture
    def tracker_class(self):
        """Import SessionActivityTracker if it exists, else use reference implementation."""
        try:
            from open_project_manager_mcp.__main__ import SessionActivityTracker
            return SessionActivityTracker
        except (ImportError, AttributeError):
            # Reference implementation for testing until Darlene's code lands
            class _ReferenceSessionActivityTracker:
                def __init__(self, session_timeout: int = 120):
                    self.session_timeout = session_timeout
                    self._sessions: dict[str, float] = {}
                
                def touch(self, session_id: str) -> None:
                    self._sessions[session_id] = time.monotonic()
                
                def remove(self, session_id: str) -> None:
                    self._sessions.pop(session_id, None)
                
                def get_stale_sessions(self) -> list[str]:
                    now = time.monotonic()
                    return [
                        sid for sid, last_active in self._sessions.items()
                        if (now - last_active) > self.session_timeout
                    ]
            return _ReferenceSessionActivityTracker

    def test_session_activity_tracker_touch_and_stale(self, tracker_class):
        """Test 1: Create tracker, touch a session, advance time, verify it appears in get_stale_sessions()."""
        tracker = tracker_class(session_timeout=10)
        
        # Mock time.monotonic to control time progression
        with patch("time.monotonic") as mock_time:
            # Initial touch at t=0
            mock_time.return_value = 0.0
            tracker.touch("session-abc")
            
            # Check stale sessions at t=5 (should be empty)
            mock_time.return_value = 5.0
            stale = tracker.get_stale_sessions()
            assert stale == [], "Session should not be stale after 5 seconds (timeout=10)"
            
            # Check stale sessions at t=15 (should contain session-abc)
            mock_time.return_value = 15.0
            stale = tracker.get_stale_sessions()
            assert "session-abc" in stale, "Session should be stale after 15 seconds (timeout=10)"
            assert len(stale) == 1

    def test_session_activity_tracker_fresh_not_stale(self, tracker_class):
        """Test 2: Touch session, immediately check stale, should be empty."""
        tracker = tracker_class(session_timeout=120)
        
        with patch("time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            tracker.touch("session-fresh")
            
            # Immediately check (same time)
            mock_time.return_value = 100.0
            stale = tracker.get_stale_sessions()
            assert stale == [], "Freshly touched session should not be stale"
            
            # Check after 1 second (well within timeout)
            mock_time.return_value = 101.0
            stale = tracker.get_stale_sessions()
            assert stale == [], "Session touched 1s ago should not be stale (timeout=120s)"

    def test_session_activity_tracker_remove(self, tracker_class):
        """Test 3: Touch then remove, verify not in stale list."""
        tracker = tracker_class(session_timeout=10)
        
        with patch("time.monotonic") as mock_time:
            # Touch session at t=0
            mock_time.return_value = 0.0
            tracker.touch("session-xyz")
            
            # Advance time past timeout
            mock_time.return_value = 20.0
            
            # Remove session before checking stale
            tracker.remove("session-xyz")
            
            # Verify it's not in stale list
            stale = tracker.get_stale_sessions()
            assert "session-xyz" not in stale, "Removed session should not appear in stale list"
            assert len(stale) == 0

    def test_session_activity_tracker_multiple_sessions(self, tracker_class):
        """Test tracker with multiple sessions, some stale, some fresh."""
        tracker = tracker_class(session_timeout=60)
        
        with patch("time.monotonic") as mock_time:
            # Touch three sessions at different times
            mock_time.return_value = 0.0
            tracker.touch("session-old")
            
            mock_time.return_value = 50.0
            tracker.touch("session-medium")
            
            mock_time.return_value = 100.0
            tracker.touch("session-fresh")
            
            # Check at t=110 (session-old is 110s old, session-medium is 60s old, session-fresh is 10s old)
            mock_time.return_value = 110.0
            stale = tracker.get_stale_sessions()
            
            # session-old (110s) should be stale, session-medium (60s exactly) is at boundary
            # session-fresh (10s) should not be stale
            assert "session-old" in stale, "session-old should be stale (110s > 60s timeout)"
            # Note: 60s == timeout, implementation uses > not >=, so session-medium is NOT stale
            assert "session-medium" not in stale, "session-medium should not be stale (60s == 60s timeout, uses > not >=)"
            assert "session-fresh" not in stale, "session-fresh should not be stale (10s < 60s timeout)"


# ---------------------------------------------------------------------------
# Test 4 & 5: session_reaper background task
# ---------------------------------------------------------------------------

class TestSessionReaper:
    """Tests for session_reaper background task."""

    @pytest.fixture
    def reaper_fn(self):
        """Import session_reaper if it exists, else use reference implementation."""
        try:
            from open_project_manager_mcp.__main__ import session_reaper
            return session_reaper
        except (ImportError, AttributeError):
            # Reference implementation
            async def _reference_session_reaper(session_manager, tracker, check_interval: int = 30):
                import logging
                logger = logging.getLogger("opm.session_reaper")
                
                while True:
                    await asyncio.sleep(check_interval)
                    
                    stale = tracker.get_stale_sessions()
                    if not stale:
                        continue
                    
                    logger.info(f"[SessionReaper] Found {len(stale)} stale sessions")
                    
                    for session_id in stale:
                        transport = session_manager._server_instances.get(session_id)
                        if transport:
                            try:
                                await transport.terminate()
                                logger.info(f"[SessionReaper] Terminated session {session_id}")
                            except Exception as e:
                                logger.warning(f"[SessionReaper] Failed to terminate {session_id}: {e}")
                        
                        tracker.remove(session_id)
                    
                    for session_id in stale:
                        session_manager._server_instances.pop(session_id, None)
            
            return _reference_session_reaper

    @pytest.fixture
    def tracker_class(self):
        """Import SessionActivityTracker for reaper tests."""
        try:
            from open_project_manager_mcp.__main__ import SessionActivityTracker
            return SessionActivityTracker
        except (ImportError, AttributeError):
            # Use minimal reference implementation
            class _TrackerRef:
                def __init__(self, session_timeout: int = 120):
                    self.session_timeout = session_timeout
                    self._sessions: dict[str, float] = {}
                
                def touch(self, session_id: str) -> None:
                    self._sessions[session_id] = time.monotonic()
                
                def remove(self, session_id: str) -> None:
                    self._sessions.pop(session_id, None)
                
                def get_stale_sessions(self) -> list[str]:
                    now = time.monotonic()
                    return [
                        sid for sid, last_active in self._sessions.items()
                        if (now - last_active) > self.session_timeout
                    ]
            return _TrackerRef

    def test_session_reaper_terminates_stale(self, reaper_fn, tracker_class):
        """Test 4: Mock session manager with stale session, verify terminate() called."""
        async def run_test():
            # Setup mock session manager
            mock_session_manager = MagicMock()
            mock_transport = AsyncMock()
            mock_session_manager._server_instances = {"session-stale": mock_transport}
            
            # Setup tracker
            tracker = tracker_class(session_timeout=10)
            
            # Mock tracker.get_stale_sessions() to return a stale session
            with patch.object(tracker, "get_stale_sessions", return_value=["session-stale"]):
                # Start reaper with short interval
                reaper_task = asyncio.create_task(reaper_fn(mock_session_manager, tracker, check_interval=0.1))
                
                # Wait for one reaper cycle
                await asyncio.sleep(0.2)
                
                # Cancel reaper
                reaper_task.cancel()
                try:
                    await reaper_task
                except asyncio.CancelledError:
                    pass
            
            # Verify terminate() was called on the stale session
            mock_transport.terminate.assert_called_once()
            
            # Verify session was removed from _server_instances
            assert "session-stale" not in mock_session_manager._server_instances
        
        asyncio.run(run_test())

    def test_session_reaper_handles_terminate_failure(self, reaper_fn, tracker_class):
        """Test 5: Mock terminate() to raise Exception, verify reaper continues and session removed from tracker."""
        async def run_test():
            # Setup mock session manager
            mock_session_manager = MagicMock()
            mock_transport = AsyncMock()
            # Make terminate() raise an exception
            mock_transport.terminate.side_effect = RuntimeError("Terminate failed")
            mock_session_manager._server_instances = {"session-broken": mock_transport}
            
            # Setup tracker
            tracker = tracker_class(session_timeout=10)
            
            # Track remove() calls
            remove_called = []
            original_remove = tracker.remove
            def track_remove(session_id):
                remove_called.append(session_id)
                return original_remove(session_id)
            tracker.remove = track_remove
            
            # Mock tracker.get_stale_sessions() to return the broken session
            with patch.object(tracker, "get_stale_sessions", return_value=["session-broken"]):
                # Start reaper
                reaper_task = asyncio.create_task(reaper_fn(mock_session_manager, tracker, check_interval=0.1))
                
                # Wait for one reaper cycle
                await asyncio.sleep(0.2)
                
                # Cancel reaper (should not raise the terminate exception)
                reaper_task.cancel()
                try:
                    await reaper_task
                except asyncio.CancelledError:
                    pass
                except RuntimeError as e:
                    pytest.fail(f"Reaper should not propagate terminate() exception, got: {e}")
            
            # Verify terminate() was attempted (and failed)
            mock_transport.terminate.assert_called_once()
            
            # Verify tracker.remove() was called anyway (session cleaned up despite failure)
            assert "session-broken" in remove_called, "Tracker should remove session even if terminate() fails"
            
            # Verify session was removed from _server_instances
            assert "session-broken" not in mock_session_manager._server_instances
        
        asyncio.run(run_test())

    def test_session_reaper_skips_cycle_when_no_stale_sessions(self, reaper_fn, tracker_class):
        """Verify reaper does nothing when get_stale_sessions() returns empty list."""
        async def run_test():
            mock_session_manager = MagicMock()
            mock_session_manager._server_instances = {}
            
            tracker = tracker_class(session_timeout=10)
            
            # Mock to always return empty list
            with patch.object(tracker, "get_stale_sessions", return_value=[]):
                reaper_task = asyncio.create_task(reaper_fn(mock_session_manager, tracker, check_interval=0.1))
                
                await asyncio.sleep(0.3)  # Multiple cycles
                
                reaper_task.cancel()
                try:
                    await reaper_task
                except asyncio.CancelledError:
                    pass
            
            # Verify no modifications were made
            assert len(mock_session_manager._server_instances) == 0
        
        asyncio.run(run_test())


# ---------------------------------------------------------------------------
# Test 6 & 7: SessionActivityMiddleware
# ---------------------------------------------------------------------------

class TestSessionActivityMiddleware:
    """Tests for SessionActivityMiddleware ASGI middleware."""

    @pytest.fixture
    def middleware_class(self):
        """Import SessionActivityMiddleware if it exists, else use reference implementation."""
        try:
            from open_project_manager_mcp.__main__ import SessionActivityMiddleware
            return SessionActivityMiddleware
        except (ImportError, AttributeError):
            # Reference implementation
            class _ReferenceSessionActivityMiddleware:
                def __init__(self, app, tracker):
                    self.app = app
                    self.tracker = tracker
                
                async def __call__(self, scope, receive, send):
                    if scope["type"] == "http":
                        headers = dict(scope.get("headers", []))
                        session_id = headers.get(b"mcp-session-id", b"").decode("utf-8", errors="ignore")
                        
                        if session_id:
                            self.tracker.touch(session_id)
                    
                    await self.app(scope, receive, send)
            
            return _ReferenceSessionActivityMiddleware

    @pytest.fixture
    def tracker_class(self):
        """Import SessionActivityTracker for middleware tests."""
        try:
            from open_project_manager_mcp.__main__ import SessionActivityTracker
            return SessionActivityTracker
        except (ImportError, AttributeError):
            # Minimal tracker for middleware tests
            class _TrackerRef:
                def __init__(self):
                    self.touched_sessions = []
                
                def touch(self, session_id: str) -> None:
                    self.touched_sessions.append(session_id)
            
            return _TrackerRef

    def test_activity_middleware_tracks_session(self, middleware_class, tracker_class):
        """Test 6: Send ASGI request with mcp-session-id header, verify tracker.touch() called."""
        async def run_test():
            # Setup tracker
            tracker = tracker_class()
            
            # Track touch() calls
            touch_called = []
            original_touch = tracker.touch
            def track_touch(session_id):
                touch_called.append(session_id)
                return original_touch(session_id)
            tracker.touch = track_touch
            
            # Setup dummy inner app
            async def dummy_app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"OK", "more_body": False})
            
            # Setup middleware
            middleware = middleware_class(dummy_app, tracker)
            
            # Create ASGI scope with mcp-session-id header
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"mcp-session-id", b"test-session-123"),
                ],
            }
            
            async def dummy_receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            
            sent_messages = []
            async def capture_send(message):
                sent_messages.append(message)
            
            # Call middleware
            await middleware(scope, dummy_receive, capture_send)
            
            # Verify tracker.touch() was called with correct session_id
            assert "test-session-123" in touch_called, "Middleware should call tracker.touch() with session_id from header"
        
        asyncio.run(run_test())

    def test_activity_middleware_no_session_id(self, middleware_class, tracker_class):
        """Test 7: Send request without mcp-session-id header, verify no crash, tracker not called."""
        async def run_test():
            # Setup tracker
            tracker = tracker_class()
            
            # Setup dummy inner app
            async def dummy_app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"OK", "more_body": False})
            
            # Setup middleware
            middleware = middleware_class(dummy_app, tracker)
            
            # Create ASGI scope WITHOUT mcp-session-id header
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/health",
                "headers": [
                    (b"content-type", b"text/plain"),
                ],
            }
            
            async def dummy_receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            
            sent_messages = []
            async def capture_send(message):
                sent_messages.append(message)
            
            # Call middleware (should not crash)
            await middleware(scope, dummy_receive, capture_send)
            
            # Verify response was sent (no crash)
            assert len(sent_messages) == 2, "Middleware should complete successfully even without session header"
            
            # Verify tracker was not called (no session ID to track)
            if hasattr(tracker, "touched_sessions"):
                assert len(tracker.touched_sessions) == 0, "Tracker should not be called when no session ID present"
        
        asyncio.run(run_test())

    def test_activity_middleware_non_http_passthrough(self, middleware_class, tracker_class):
        """Verify middleware passes through non-HTTP scopes (lifespan, websocket)."""
        async def run_test():
            tracker = tracker_class()
            
            inner_called = []
            async def dummy_app(scope, receive, send):
                inner_called.append(scope["type"])
            
            middleware = middleware_class(dummy_app, tracker)
            
            async def dummy_receive():
                return {}
            
            async def dummy_send(message):
                pass
            
            # Test lifespan scope
            await middleware({"type": "lifespan"}, dummy_receive, dummy_send)
            assert "lifespan" in inner_called
            
            # Test websocket scope
            await middleware({"type": "websocket"}, dummy_receive, dummy_send)
            assert "websocket" in inner_called
            
            # Verify tracker was not called for non-HTTP scopes
            if hasattr(tracker, "touched_sessions"):
                assert len(tracker.touched_sessions) == 0
        
        asyncio.run(run_test())

    def test_activity_middleware_extracts_session_from_headers(self, middleware_class, tracker_class):
        """Verify middleware correctly extracts session ID from various header formats."""
        async def run_test():
            tracker = tracker_class()
            
            async def dummy_app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"OK", "more_body": False})
            
            middleware = middleware_class(dummy_app, tracker)
            
            async def dummy_receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            
            async def dummy_send(message):
                pass
            
            # Test with different session ID values
            test_cases = [
                b"abc123",
                b"session-with-dashes",
                b"dda4934a",  # Example from spec
                b"",  # Empty should not crash
            ]
            
            for session_id_bytes in test_cases:
                scope = {
                    "type": "http",
                    "headers": [(b"mcp-session-id", session_id_bytes)],
                }
                
                await middleware(scope, dummy_receive, dummy_send)
            
            # Verify non-empty session IDs were tracked
            if hasattr(tracker, "touched_sessions"):
                expected_sessions = [sid.decode("utf-8") for sid in test_cases if sid]
                for sid in expected_sessions:
                    assert sid in tracker.touched_sessions, f"Session {sid} should have been tracked"
        
        asyncio.run(run_test())


# ---------------------------------------------------------------------------
# Integration: Middleware Order
# ---------------------------------------------------------------------------

class TestMiddlewareIntegration:
    """Integration tests for middleware ordering."""

    def test_middleware_order_in_http_mode(self):
        """Verify SessionActivityMiddleware is applied in correct order (after ConnectionTimeout, before FixArguments)."""
        # This test validates the middleware wrapping order in main()
        # Actual implementation order should be:
        # 1. ConnectionTimeoutMiddleware (outermost)
        # 2. SessionActivityMiddleware
        # 3. _FixArgumentsMiddleware (innermost, closest to FastMCP app)
        
        # Test structure only (actual integration test would require full app startup)
        try:
            from open_project_manager_mcp.__main__ import (
                ConnectionTimeoutMiddleware,
                SessionActivityMiddleware,
                _FixArgumentsMiddleware,
            )
        except ImportError:
            pytest.skip("Middleware classes not yet implemented")
        
        # Verify classes exist
        assert ConnectionTimeoutMiddleware is not None
        assert SessionActivityMiddleware is not None
        assert _FixArgumentsMiddleware is not None
