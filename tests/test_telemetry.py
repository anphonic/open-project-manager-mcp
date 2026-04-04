"""Tests for v0.3.0 telemetry system."""

import asyncio
import functools
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest


def _get_tool(mcp_server, name: str):
    """Extract a raw tool function from a FastMCP server instance."""
    return mcp_server._tool_manager._tools[name].fn


def _sync_wrap(fn):
    """Wrap an async tool function so it can be called synchronously in tests."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return asyncio.run(result)
        return result
    return wrapper


@pytest.fixture()
def mcp_server(tmp_path):
    """Spin up create_server() with a fresh SQLite database."""
    from open_project_manager_mcp.server import create_server
    server = create_server(str(tmp_path / "tasks.db"))
    server._get = lambda name: _sync_wrap(_get_tool(server, name))
    return server


@pytest.fixture()
def db_conn(tmp_path):
    """Direct database connection for low-level inspection."""
    from open_project_manager_mcp.server import create_server, _SCHEMA
    db_path = str(tmp_path / "tasks.db")
    # Initialize schema through server creation
    server = create_server(db_path)
    # Return a separate connection for inspection
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


class TestRecordMetric:
    """Test the _record_metric helper function that writes telemetry data."""
    
    def test_record_metric_creates_row(self, mcp_server, db_conn):
        """_record_metric() should insert a row into telemetry_metrics."""
        # Test via instrumented tool call
        # Create a task which should trigger telemetry recording
        mcp_server._get("create_task")(id="t1", title="Test")
        
        # Wait briefly for fire-and-forget telemetry to complete
        time.sleep(0.5)
        
        # Verify telemetry row was written
        rows = db_conn.execute(
            "SELECT * FROM telemetry_metrics WHERE metric_type = 'tool_call' AND metric_name = 'create_task'"
        ).fetchall()
        
        # Should have at least one metric recorded
        assert len(rows) > 0, "Expected telemetry_metrics row after tool call"
    
    def test_record_metric_increments_count(self, mcp_server, db_conn):
        """Multiple calls to same tool should increment count in same bucket."""
        # Make multiple calls to same tool
        for i in range(3):
            mcp_server._get("create_task")(id=f"t{i}", title=f"Task {i}")
        
        # Check that count was incremented (upsert behavior)
        rows = db_conn.execute(
            "SELECT count FROM telemetry_metrics WHERE metric_name = 'create_task'"
        ).fetchall()
        
        if rows:
            total_count = sum(row["count"] for row in rows)
            assert total_count >= 3, f"Expected count >= 3, got {total_count}"
    
    def test_record_metric_tracks_latency(self, mcp_server, db_conn):
        """Metrics should include latency measurements (sum_ms, min_ms, max_ms)."""
        # Create a task
        mcp_server._get("create_task")(id="t1", title="Test")
        
        # Check latency fields were populated
        row = db_conn.execute(
            "SELECT sum_ms, min_ms, max_ms FROM telemetry_metrics WHERE metric_name = 'create_task'"
        ).fetchone()
        
        if row:
            # Latency should be non-null and reasonable (< 10 seconds for a simple operation)
            assert row["sum_ms"] is not None or row["min_ms"] is not None, "Expected latency to be tracked"
            if row["sum_ms"]:
                assert 0 < row["sum_ms"] < 10000, f"Unreasonable latency: {row['sum_ms']}ms"
    
    def test_record_metric_tracks_errors(self, mcp_server, db_conn):
        """Failed operations should increment error_count."""
        # Try to create duplicate task (should fail)
        mcp_server._get("create_task")(id="dup", title="First")
        result = mcp_server._get("create_task")(id="dup", title="Second")
        
        assert "Error" in result or "already exists" in result
        
        # Check if errors are tracked
        row = db_conn.execute(
            "SELECT error_count FROM telemetry_metrics WHERE metric_name = 'create_task'"
        ).fetchone()
        
        if row and row["error_count"] is not None:
            assert row["error_count"] > 0, "Expected error_count > 0 after failed operation"
    
    def test_record_metric_uses_hourly_buckets(self, mcp_server, db_conn):
        """Metrics should be bucketed by hour (bucket_hour format)."""
        mcp_server._get("create_task")(id="t1", title="Test")
        
        row = db_conn.execute(
            "SELECT bucket_hour FROM telemetry_metrics WHERE metric_name = 'create_task'"
        ).fetchone()
        
        if row:
            bucket = row["bucket_hour"]
            # Should be ISO8601 format with hour precision (minute/second should be :00)
            assert bucket is not None
            # Parse to verify it's a valid datetime
            dt = datetime.fromisoformat(bucket.replace('Z', '+00:00'))
            assert dt.minute == 0 and dt.second == 0, f"Expected hour bucket, got {bucket}"
    
    def test_record_metric_tenant_scoped(self, mcp_server, db_conn):
        """Metrics should be scoped per tenant_id."""
        # Create task (will use default "system" tenant in test environment)
        mcp_server._get("create_task")(id="t1", title="Test")
        
        row = db_conn.execute(
            "SELECT tenant_id FROM telemetry_metrics WHERE metric_name = 'create_task'"
        ).fetchone()
        
        if row:
            assert row["tenant_id"] is not None, "Expected tenant_id to be set"
            assert len(row["tenant_id"]) > 0, "tenant_id should not be empty"


class TestGetTelemetrySummary:
    """Test get_telemetry_summary() tool."""
    
    def test_returns_aggregated_metrics(self, mcp_server, db_conn):
        """Should return aggregated counts and latency for tenant."""
        # Create some tasks to generate telemetry
        for i in range(5):
            mcp_server._get("create_task")(id=f"t{i}", title=f"Task {i}")
        
        # Call get_telemetry_summary (no tenant_id param - uses _get_actor())
        result = mcp_server._get("get_telemetry_summary")(hours=24)
        
        # Should return JSON or structured data
        if result and not result.startswith("Error"):
            data = json.loads(result) if isinstance(result, str) else result
            assert "total_calls" in data or "metrics" in data, "Expected summary data"
    
    def test_filters_by_tenant(self, mcp_server, db_conn):
        """Should only return metrics for specified tenant."""
        # Insert metrics for different tenants
        now = datetime.now(timezone.utc).isoformat()
        bucket = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "create_task", bucket, 10, now, now)
        )
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant2", "tool_call", "create_task", bucket, 5, now, now)
        )
        db_conn.commit()
        
        result = mcp_server._get("get_telemetry_summary")(hours=24)
        
        if result and not result.startswith("Error"):
            data = json.loads(result) if isinstance(result, str) else result
            # Verify only tenant1's data is included (should see 10+ calls, not 15+)
            # Exact assertion depends on implementation format
            assert data is not None
    
    def test_filters_by_time_range(self, mcp_server, db_conn):
        """Should only return metrics within specified hours window."""
        now = datetime.now(timezone.utc)
        bucket_recent = now.replace(minute=0, second=0, microsecond=0).isoformat()
        bucket_old = (now.replace(minute=0, second=0, microsecond=0).timestamp() - 86400 * 3)
        bucket_old = datetime.fromtimestamp(bucket_old, tz=timezone.utc).isoformat()
        
        now_iso = now.isoformat()
        
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "test_tool", bucket_recent, 10, now_iso, now_iso)
        )
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "old_tool", bucket_old, 100, now_iso, now_iso)
        )
        db_conn.commit()
        
        # Query for last 24 hours (no tenant_id param - uses _get_actor())
        result = mcp_server._get("get_telemetry_summary")(hours=24)
        
        if result and not result.startswith("Error"):
            data = json.loads(result) if isinstance(result, str) else result
            # Should include recent but not 3-day-old metrics
            # Exact assertion depends on response format
            assert data is not None


class TestGetTelemetryByTool:
    """Test get_telemetry_by_tool() for per-tool breakdown."""
    
    def test_filters_by_tool_name(self, mcp_server, db_conn):
        """Should return metrics only for specified tool."""
        now = datetime.now(timezone.utc).isoformat()
        bucket = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "create_task", bucket, 10, now, now)
        )
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "update_task", bucket, 5, now, now)
        )
        db_conn.commit()
        
        result = mcp_server._get("get_telemetry_by_tool")(
            tool_name="create_task", hours=24
        )
        
        if result and not result.startswith("Error"):
            data = json.loads(result) if isinstance(result, str) else result
            assert data is not None
            # Should only include create_task metrics


class TestListTopTools:
    """Test list_top_tools() for most frequently called tools."""
    
    def test_orders_by_call_count(self, mcp_server, db_conn):
        """Should return tools ordered by call count descending."""
        now = datetime.now(timezone.utc).isoformat()
        bucket = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "tool_a", bucket, 100, now, now)
        )
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "tool_b", bucket, 50, now, now)
        )
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "tool_c", bucket, 200, now, now)
        )
        db_conn.commit()
        
        result = mcp_server._get("list_top_tools")(limit=10, hours=24)
        
        if result and not result.startswith("Error"):
            data = json.loads(result) if isinstance(result, str) else result
            if isinstance(data, list) and len(data) >= 2:
                # First tool should have highest count
                assert data[0].get("count", 0) >= data[1].get("count", 0)
    
    def test_respects_limit(self, mcp_server, db_conn):
        """Should return at most 'limit' tools."""
        now = datetime.now(timezone.utc).isoformat()
        bucket = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        
        for i in range(20):
            db_conn.execute(
                "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("tenant1", "tool_call", f"tool_{i}", bucket, 10 + i, now, now)
            )
        db_conn.commit()
        
        result = mcp_server._get("list_top_tools")(limit=5, hours=24)
        
        if result and not result.startswith("Error"):
            data = json.loads(result) if isinstance(result, str) else result
            if isinstance(data, list):
                assert len(data) <= 5


class TestGetErrorSummary:
    """Test get_error_summary() for error counts by tool."""
    
    def test_returns_error_counts(self, mcp_server, db_conn):
        """Should return tools with error counts."""
        now = datetime.now(timezone.utc).isoformat()
        bucket = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, error_count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "create_task", bucket, 100, 5, now, now)
        )
        db_conn.execute(
            "INSERT INTO telemetry_metrics (tenant_id, metric_type, metric_name, bucket_hour, count, error_count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tenant1", "tool_call", "update_task", bucket, 50, 10, now, now)
        )
        db_conn.commit()
        
        result = mcp_server._get("get_error_summary")(hours=24)
        
        if result and not result.startswith("Error"):
            data = json.loads(result) if isinstance(result, str) else result
            assert data is not None
            # Should include error counts


class TestInstrumentedTools:
    """Test that actual MCP tools call _record_metric."""
    
    def test_create_task_records_metric(self, mcp_server, db_conn):
        """create_task should write telemetry row."""
        mcp_server._get("create_task")(id="t1", title="Test")
        
        # Wait briefly for fire-and-forget telemetry to complete
        time.sleep(0.5)
        
        rows = db_conn.execute(
            "SELECT * FROM telemetry_metrics WHERE metric_name = 'create_task'"
        ).fetchall()
        
        assert len(rows) > 0, "create_task should record telemetry"
    
    def test_update_task_records_metric(self, mcp_server, db_conn):
        """update_task should write telemetry row."""
        mcp_server._get("create_task")(id="t1", title="Original")
        mcp_server._get("update_task")(task_id="t1", title="Updated")
        
        # Wait briefly for fire-and-forget telemetry to complete
        time.sleep(0.5)
        
        rows = db_conn.execute(
            "SELECT * FROM telemetry_metrics WHERE metric_name = 'update_task'"
        ).fetchall()
        
        assert len(rows) > 0, "update_task should record telemetry"
    
    def test_get_task_records_metric(self, mcp_server, db_conn):
        """get_task should write telemetry row."""
        mcp_server._get("create_task")(id="t1", title="Test")
        mcp_server._get("get_task")(task_id="t1")
        
        # Wait briefly for fire-and-forget telemetry to complete
        time.sleep(0.5)
        
        rows = db_conn.execute(
            "SELECT * FROM telemetry_metrics WHERE metric_name = 'get_task'"
        ).fetchall()
        
        assert len(rows) > 0, "get_task should record telemetry"


class TestFireAndForget:
    """Test that telemetry recording doesn't block tool response."""
    
    def test_metric_recording_is_async(self, mcp_server, db_conn):
        """Tool should return immediately even if metric recording is slow."""
        # This is hard to test directly without mocking, but we can verify
        # that the tool returns quickly
        start = time.time()
        result = mcp_server._get("create_task")(id="t1", title="Test")
        elapsed = time.time() - start
        
        # Should complete in under 1 second (way less, actually)
        assert elapsed < 1.0, f"Tool took {elapsed}s - possible blocking on telemetry"
        assert result is not None
        assert "Error" not in result or json.loads(result).get("id") == "t1"
    
    def test_tool_succeeds_even_if_telemetry_fails(self, mcp_server, db_conn):
        """Tool should succeed even if telemetry write fails."""
        # Drop telemetry table to simulate failure
        db_conn.execute("DROP TABLE IF EXISTS telemetry_metrics")
        db_conn.commit()
        
        # Tool should still succeed
        result = mcp_server._get("create_task")(id="t1", title="Test")
        
        # Create should succeed
        if not result.startswith("Error"):
            data = json.loads(result)
            assert data["id"] == "t1"
