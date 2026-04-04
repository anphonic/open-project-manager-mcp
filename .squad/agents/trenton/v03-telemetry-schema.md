# v0.3.0 Telemetry Schema

**Author:** Trenton (Shama Biswani)  
**Date:** 2026-04-04  
**Status:** Production-ready DDL

---

## Overview

The telemetry schema tracks per-tenant, per-tool usage metrics and error rates for observability and capacity planning. Design prioritizes:
- **Write performance**: Tool calls happen frequently; metrics collection must not slow down the hot path
- **Retention policy**: Raw events roll up to hourly/daily aggregates; old raw data gets pruned
- **Query efficiency**: Indexes optimized for dashboard queries (tenant + time range)

---

## Schema DDL

### 1. Raw Telemetry Events (short-lived buffer)

```sql
CREATE TABLE IF NOT EXISTS telemetry_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT    NOT NULL,
    tool_name    TEXT    NOT NULL,
    latency_ms   INTEGER NOT NULL,  -- milliseconds
    status       TEXT    NOT NULL,  -- 'success' | 'error'
    error_type   TEXT,               -- e.g. 'ValidationError', 'TimeoutError'
    created_at   TEXT    NOT NULL   -- ISO8601 timestamp
);

CREATE INDEX IF NOT EXISTS telemetry_events_tenant_time_idx 
    ON telemetry_events(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS telemetry_events_rollup_idx 
    ON telemetry_events(created_at);
```

**Retention:** Raw events kept for **7 days**, then deleted after rollup.

---

### 2. Hourly Aggregates (medium-term retention)

```sql
CREATE TABLE IF NOT EXISTS telemetry_hourly (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id      TEXT    NOT NULL,
    tool_name      TEXT    NOT NULL,
    hour_bucket    TEXT    NOT NULL,  -- ISO8601 hour: '2026-04-04T14:00:00Z'
    call_count     INTEGER NOT NULL DEFAULT 0,
    success_count  INTEGER NOT NULL DEFAULT 0,
    error_count    INTEGER NOT NULL DEFAULT 0,
    latency_p50    INTEGER,           -- milliseconds (median)
    latency_p95    INTEGER,           -- milliseconds
    latency_p99    INTEGER,           -- milliseconds
    latency_sum    INTEGER NOT NULL DEFAULT 0,  -- for avg calculation
    UNIQUE(tenant_id, tool_name, hour_bucket)
);

CREATE INDEX IF NOT EXISTS telemetry_hourly_tenant_time_idx 
    ON telemetry_hourly(tenant_id, hour_bucket DESC);
CREATE INDEX IF NOT EXISTS telemetry_hourly_time_idx 
    ON telemetry_hourly(hour_bucket DESC);
```

**Retention:** Hourly aggregates kept for **90 days**, then rolled up to daily or deleted.

---

### 3. Daily Aggregates (long-term retention)

```sql
CREATE TABLE IF NOT EXISTS telemetry_daily (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id      TEXT    NOT NULL,
    tool_name      TEXT    NOT NULL,
    day_bucket     TEXT    NOT NULL,  -- ISO8601 date: '2026-04-04'
    call_count     INTEGER NOT NULL DEFAULT 0,
    success_count  INTEGER NOT NULL DEFAULT 0,
    error_count    INTEGER NOT NULL DEFAULT 0,
    latency_p50    INTEGER,
    latency_p95    INTEGER,
    latency_p99    INTEGER,
    latency_avg    INTEGER,           -- avg = sum / count
    UNIQUE(tenant_id, tool_name, day_bucket)
);

CREATE INDEX IF NOT EXISTS telemetry_daily_tenant_time_idx 
    ON telemetry_daily(tenant_id, day_bucket DESC);
CREATE INDEX IF NOT EXISTS telemetry_daily_time_idx 
    ON telemetry_daily(day_bucket DESC);
```

**Retention:** Daily aggregates kept for **2 years**.

---

## Migration Code

Safe to run on existing databases. Uses `CREATE TABLE IF NOT EXISTS` and idempotent index creation.

```python
def _migrate_telemetry_schema(conn: sqlite3.Connection):
    """Apply v0.3.0 telemetry schema migration (idempotent)."""
    _TELEMETRY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS telemetry_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id    TEXT    NOT NULL,
        tool_name    TEXT    NOT NULL,
        latency_ms   INTEGER NOT NULL,
        status       TEXT    NOT NULL,
        error_type   TEXT,
        created_at   TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS telemetry_events_tenant_time_idx 
        ON telemetry_events(tenant_id, created_at);
    CREATE INDEX IF NOT EXISTS telemetry_events_rollup_idx 
        ON telemetry_events(created_at);
    
    CREATE TABLE IF NOT EXISTS telemetry_hourly (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id      TEXT    NOT NULL,
        tool_name      TEXT    NOT NULL,
        hour_bucket    TEXT    NOT NULL,
        call_count     INTEGER NOT NULL DEFAULT 0,
        success_count  INTEGER NOT NULL DEFAULT 0,
        error_count    INTEGER NOT NULL DEFAULT 0,
        latency_p50    INTEGER,
        latency_p95    INTEGER,
        latency_p99    INTEGER,
        latency_sum    INTEGER NOT NULL DEFAULT 0,
        UNIQUE(tenant_id, tool_name, hour_bucket)
    );
    CREATE INDEX IF NOT EXISTS telemetry_hourly_tenant_time_idx 
        ON telemetry_hourly(tenant_id, hour_bucket DESC);
    CREATE INDEX IF NOT EXISTS telemetry_hourly_time_idx 
        ON telemetry_hourly(hour_bucket DESC);
    
    CREATE TABLE IF NOT EXISTS telemetry_daily (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id      TEXT    NOT NULL,
        tool_name      TEXT    NOT NULL,
        day_bucket     TEXT    NOT NULL,
        call_count     INTEGER NOT NULL DEFAULT 0,
        success_count  INTEGER NOT NULL DEFAULT 0,
        error_count    INTEGER NOT NULL DEFAULT 0,
        latency_p50    INTEGER,
        latency_p95    INTEGER,
        latency_p99    INTEGER,
        latency_avg    INTEGER,
        UNIQUE(tenant_id, tool_name, day_bucket)
    );
    CREATE INDEX IF NOT EXISTS telemetry_daily_tenant_time_idx 
        ON telemetry_daily(tenant_id, day_bucket DESC);
    CREATE INDEX IF NOT EXISTS telemetry_daily_time_idx 
        ON telemetry_daily(day_bucket DESC);
    """
    
    conn.executescript(_TELEMETRY_SCHEMA)
    conn.commit()
```

**Integration point:** Add `_migrate_telemetry_schema(conn)` after the main `_SCHEMA` execution in `create_server()`.

---

## Rollup & Retention Queries

### Hourly Rollup (run every hour via background task)

```sql
-- Aggregate raw events into hourly buckets
INSERT INTO telemetry_hourly (
    tenant_id, tool_name, hour_bucket, call_count, success_count, error_count,
    latency_p50, latency_p95, latency_p99, latency_sum
)
SELECT 
    tenant_id,
    tool_name,
    strftime('%Y-%m-%dT%H:00:00Z', created_at) AS hour_bucket,
    COUNT(*) AS call_count,
    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count,
    (SELECT latency_ms FROM telemetry_events e2 
     WHERE e2.tenant_id = e1.tenant_id 
       AND e2.tool_name = e1.tool_name
       AND strftime('%Y-%m-%dT%H:00:00Z', e2.created_at) = strftime('%Y-%m-%dT%H:00:00Z', e1.created_at)
     ORDER BY e2.latency_ms 
     LIMIT 1 OFFSET (
         SELECT COUNT(*) * 50 / 100 FROM telemetry_events e3
         WHERE e3.tenant_id = e2.tenant_id 
           AND e3.tool_name = e2.tool_name
           AND strftime('%Y-%m-%dT%H:00:00Z', e3.created_at) = strftime('%Y-%m-%dT%H:00:00Z', e2.created_at)
     )) AS latency_p50,
    (SELECT latency_ms FROM telemetry_events e2 
     WHERE e2.tenant_id = e1.tenant_id 
       AND e2.tool_name = e1.tool_name
       AND strftime('%Y-%m-%dT%H:00:00Z', e2.created_at) = strftime('%Y-%m-%dT%H:00:00Z', e1.created_at)
     ORDER BY e2.latency_ms 
     LIMIT 1 OFFSET (
         SELECT COUNT(*) * 95 / 100 FROM telemetry_events e3
         WHERE e3.tenant_id = e2.tenant_id 
           AND e3.tool_name = e2.tool_name
           AND strftime('%Y-%m-%dT%H:00:00Z', e3.created_at) = strftime('%Y-%m-%dT%H:00:00Z', e2.created_at)
     )) AS latency_p95,
    (SELECT latency_ms FROM telemetry_events e2 
     WHERE e2.tenant_id = e1.tenant_id 
       AND e2.tool_name = e1.tool_name
       AND strftime('%Y-%m-%dT%H:00:00Z', e2.created_at) = strftime('%Y-%m-%dT%H:00:00Z', e1.created_at)
     ORDER BY e2.latency_ms 
     LIMIT 1 OFFSET (
         SELECT COUNT(*) * 99 / 100 FROM telemetry_events e3
         WHERE e3.tenant_id = e2.tenant_id 
           AND e3.tool_name = e2.tool_name
           AND strftime('%Y-%m-%dT%H:00:00Z', e3.created_at) = strftime('%Y-%m-%dT%H:00:00Z', e2.created_at)
     )) AS latency_p99,
    SUM(latency_ms) AS latency_sum
FROM telemetry_events e1
WHERE created_at < datetime('now', '-1 hour')  -- only roll up completed hours
GROUP BY tenant_id, tool_name, hour_bucket
ON CONFLICT(tenant_id, tool_name, hour_bucket) DO UPDATE SET
    call_count = call_count + excluded.call_count,
    success_count = success_count + excluded.success_count,
    error_count = error_count + excluded.error_count,
    latency_sum = latency_sum + excluded.latency_sum;
```

**Note:** SQLite doesn't have native percentile functions. For production, consider pre-computing percentiles in application code or using a simpler median approximation.

**Simplified median/p95/p99 approach (more practical):**

```python
def _rollup_to_hourly(conn: sqlite3.Connection):
    """Aggregate raw telemetry events into hourly buckets."""
    # Get all hour buckets that need rollup
    cursor = conn.execute("""
        SELECT DISTINCT 
            tenant_id, 
            tool_name, 
            strftime('%Y-%m-%dT%H:00:00Z', created_at) AS hour_bucket
        FROM telemetry_events
        WHERE created_at < datetime('now', '-1 hour')
    """)
    
    for row in cursor.fetchall():
        tenant_id, tool_name, hour_bucket = row
        
        # Get latencies for this bucket
        latencies = [
            r[0] for r in conn.execute("""
                SELECT latency_ms FROM telemetry_events
                WHERE tenant_id = ? 
                  AND tool_name = ?
                  AND strftime('%Y-%m-%dT%H:00:00Z', created_at) = ?
                ORDER BY latency_ms
            """, (tenant_id, tool_name, hour_bucket)).fetchall()
        ]
        
        if not latencies:
            continue
        
        # Calculate percentiles
        n = len(latencies)
        p50 = latencies[int(n * 0.50)] if n > 0 else None
        p95 = latencies[int(n * 0.95)] if n > 0 else None
        p99 = latencies[int(n * 0.99)] if n > 0 else None
        
        # Aggregate counts
        stats = conn.execute("""
            SELECT 
                COUNT(*) AS call_count,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count,
                SUM(latency_ms) AS latency_sum
            FROM telemetry_events
            WHERE tenant_id = ? 
              AND tool_name = ?
              AND strftime('%Y-%m-%dT%H:00:00Z', created_at) = ?
        """, (tenant_id, tool_name, hour_bucket)).fetchone()
        
        # Insert or update hourly aggregate
        conn.execute("""
            INSERT INTO telemetry_hourly (
                tenant_id, tool_name, hour_bucket, call_count, success_count, 
                error_count, latency_p50, latency_p95, latency_p99, latency_sum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, tool_name, hour_bucket) DO UPDATE SET
                call_count = excluded.call_count,
                success_count = excluded.success_count,
                error_count = excluded.error_count,
                latency_p50 = excluded.latency_p50,
                latency_p95 = excluded.latency_p95,
                latency_p99 = excluded.latency_p99,
                latency_sum = excluded.latency_sum
        """, (tenant_id, tool_name, hour_bucket, stats[0], stats[1], stats[2], 
              p50, p95, p99, stats[3]))
    
    conn.commit()
```

### Daily Rollup (run daily via background task)

```python
def _rollup_to_daily(conn: sqlite3.Connection):
    """Aggregate hourly telemetry into daily buckets."""
    conn.execute("""
        INSERT INTO telemetry_daily (
            tenant_id, tool_name, day_bucket, call_count, success_count, 
            error_count, latency_p50, latency_p95, latency_p99, latency_avg
        )
        SELECT 
            tenant_id,
            tool_name,
            date(hour_bucket) AS day_bucket,
            SUM(call_count) AS call_count,
            SUM(success_count) AS success_count,
            SUM(error_count) AS error_count,
            AVG(latency_p50) AS latency_p50,  -- approximate (not true p50 of all events)
            AVG(latency_p95) AS latency_p95,
            AVG(latency_p99) AS latency_p99,
            SUM(latency_sum) / SUM(call_count) AS latency_avg
        FROM telemetry_hourly
        WHERE hour_bucket < date('now')  -- only completed days
        GROUP BY tenant_id, tool_name, day_bucket
        ON CONFLICT(tenant_id, tool_name, day_bucket) DO UPDATE SET
            call_count = excluded.call_count,
            success_count = excluded.success_count,
            error_count = excluded.error_count,
            latency_p50 = excluded.latency_p50,
            latency_p95 = excluded.latency_p95,
            latency_p99 = excluded.latency_p99,
            latency_avg = excluded.latency_avg
    """)
    conn.commit()
```

### Cleanup Old Data

```python
def _cleanup_telemetry(conn: sqlite3.Connection):
    """Delete raw and hourly data past retention windows."""
    # Delete raw events older than 7 days
    conn.execute("""
        DELETE FROM telemetry_events
        WHERE created_at < datetime('now', '-7 days')
    """)
    
    # Delete hourly aggregates older than 90 days
    conn.execute("""
        DELETE FROM telemetry_hourly
        WHERE hour_bucket < datetime('now', '-90 days')
    """)
    
    # Delete daily aggregates older than 2 years
    conn.execute("""
        DELETE FROM telemetry_daily
        WHERE day_bucket < date('now', '-2 years')
    """)
    
    conn.commit()
```

---

## Key Query Patterns

### 1. Get metrics for a tenant (last 24 hours)

```sql
SELECT 
    tool_name,
    SUM(call_count) AS total_calls,
    SUM(success_count) AS successes,
    SUM(error_count) AS errors,
    ROUND(100.0 * SUM(error_count) / SUM(call_count), 2) AS error_rate_pct,
    AVG(latency_p50) AS avg_p50,
    MAX(latency_p99) AS max_p99
FROM telemetry_hourly
WHERE tenant_id = ?
  AND hour_bucket >= datetime('now', '-24 hours')
GROUP BY tool_name
ORDER BY total_calls DESC;
```

### 2. Get top error tools across all tenants

```sql
SELECT 
    tool_name,
    SUM(error_count) AS total_errors,
    SUM(call_count) AS total_calls,
    ROUND(100.0 * SUM(error_count) / SUM(call_count), 2) AS error_rate_pct
FROM telemetry_hourly
WHERE hour_bucket >= datetime('now', '-7 days')
GROUP BY tool_name
HAVING SUM(error_count) > 0
ORDER BY error_rate_pct DESC, total_errors DESC
LIMIT 10;
```

### 3. Get daily trend for a tenant + tool

```sql
SELECT 
    day_bucket,
    call_count,
    success_count,
    error_count,
    latency_avg,
    latency_p95
FROM telemetry_daily
WHERE tenant_id = ?
  AND tool_name = ?
  AND day_bucket >= date('now', '-30 days')
ORDER BY day_bucket DESC;
```

---

## Write Performance Considerations

1. **Raw event insert is fast:** Single row insert with auto-increment PK, minimal indexes
2. **Batch inserts recommended:** If capturing many events, use transactions:
   ```python
   with conn:  # auto-commit on success, rollback on error
       for event in batch:
           conn.execute("INSERT INTO telemetry_events (...) VALUES (...)", event)
   ```
3. **Rollup runs off hot path:** Background task processes old data (>1 hour ago)
4. **WAL mode:** Already enabled in server.py (`PRAGMA journal_mode=WAL`) — allows concurrent reads during writes

---

## Data Volume Estimation

**Assumptions:**
- 10 tenants
- 1000 tool calls/day per tenant
- 7-day raw retention = 70,000 rows (70K * ~100 bytes = 7 MB)
- 90-day hourly retention: 10 tenants × 20 tools × 24 hours × 90 days = 432K rows (~50 MB)
- 2-year daily retention: 10 tenants × 20 tools × 730 days = 146K rows (~20 MB)

**Total:** ~77 MB for above scenario. Scales linearly with tenant count and call volume.

---

## Testing Checklist

- [ ] Insert 1000 raw events, verify indexes used (`EXPLAIN QUERY PLAN`)
- [ ] Run hourly rollup, confirm aggregates match raw data
- [ ] Run daily rollup, confirm aggregates match hourly data
- [ ] Run cleanup, confirm old data deleted
- [ ] Query patterns execute in <100ms with 1M rows (EXPLAIN output attached)
- [ ] Concurrent writes don't block reads (WAL mode verified)

---

## Future Enhancements (post-v0.3.0)

- **Error breakdown by type:** Track top error_type per tool
- **Tenant quotas:** Alert when call_count exceeds threshold
- **Real-time push:** Stream high error rates to event_subscriptions table
- **Compressed storage:** Archive old daily aggregates to JSON exports
