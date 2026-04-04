# Trenton Brief: v0.3.0 Schema DDL

**From:** Elliot (Lead & Architect)  
**To:** Trenton (DBA)  
**Date:** 2026-04-04

---

## Overview

Two new tables + indexes for v0.3.0. Both are additive — no changes to existing tables.

---

## 1. Telemetry Tables

### telemetry_metrics (hourly buckets)

```sql
CREATE TABLE IF NOT EXISTS telemetry_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT    NOT NULL,
    metric_type  TEXT    NOT NULL,  -- 'tool_call', 'rest_call', 'error', 'latency'
    metric_name  TEXT    NOT NULL,  -- tool name or endpoint path
    bucket_hour  TEXT    NOT NULL,  -- ISO8601 hour start, e.g. '2026-04-04T14:00:00Z'
    count        INTEGER NOT NULL DEFAULT 0,
    sum_ms       INTEGER,           -- for latency: sum of durations in ms
    min_ms       INTEGER,           -- for latency: minimum duration
    max_ms       INTEGER,           -- for latency: maximum duration
    error_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(tenant_id, metric_type, metric_name, bucket_hour)
);

CREATE INDEX IF NOT EXISTS telemetry_tenant_hour_idx 
    ON telemetry_metrics(tenant_id, bucket_hour DESC);
CREATE INDEX IF NOT EXISTS telemetry_type_idx 
    ON telemetry_metrics(metric_type);
```

### telemetry_daily (daily rollups)

```sql
CREATE TABLE IF NOT EXISTS telemetry_daily (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT    NOT NULL,
    metric_type  TEXT    NOT NULL,
    metric_name  TEXT    NOT NULL,
    bucket_date  TEXT    NOT NULL,  -- ISO8601 date, e.g. '2026-04-04'
    total_count  INTEGER NOT NULL DEFAULT 0,
    total_errors INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms REAL,
    p95_latency_ms INTEGER,         -- approximate via histogram
    created_at   TEXT    NOT NULL,
    UNIQUE(tenant_id, metric_type, metric_name, bucket_date)
);

CREATE INDEX IF NOT EXISTS telemetry_daily_tenant_idx 
    ON telemetry_daily(tenant_id, bucket_date DESC);
```

---

## 2. Permissions Table

```sql
CREATE TABLE IF NOT EXISTS project_permissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project      TEXT    NOT NULL,
    tenant_id    TEXT    NOT NULL,
    role         TEXT    NOT NULL,  -- 'owner', 'contributor', 'reader'
    granted_by   TEXT,              -- tenant_id who granted this
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(project, tenant_id)
);

CREATE INDEX IF NOT EXISTS perm_project_idx 
    ON project_permissions(project);
CREATE INDEX IF NOT EXISTS perm_tenant_idx 
    ON project_permissions(tenant_id);
```

---

## 3. Migration Helper

Darlene will need a migration function. Here's the DDL for the backfill query:

```sql
-- Backfill existing projects: make current tenant owner of all their projects
INSERT INTO project_permissions (project, tenant_id, role, granted_by, created_at, updated_at)
SELECT DISTINCT 
    t.project,
    ? AS tenant_id,  -- current calling tenant
    'owner' AS role,
    'migration' AS granted_by,
    datetime('now') AS created_at,
    datetime('now') AS updated_at
FROM tasks t
WHERE NOT EXISTS (
    SELECT 1 FROM project_permissions pp 
    WHERE pp.project = t.project AND pp.tenant_id = ?
);
```

---

## 4. Integration Points

Add these DDL blocks to `_SCHEMA` in server.py, after the existing tables.

**Location:** After line ~170 in server.py (after `event_subscriptions` table)

**Pattern:** Same as existing — `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`

---

## 5. Validation Rules (enforce in code, not DB)

| Field | Constraint |
|-------|------------|
| `metric_type` | One of: `tool_call`, `rest_call`, `error`, `latency` |
| `role` | One of: `owner`, `contributor`, `reader` |
| `bucket_hour` | ISO8601, always minutes/seconds zeroed |
| `bucket_date` | ISO8601 date only (no time) |

---

## Tasks

- [ ] `v03-telemetry-schema` — Add both telemetry tables to `_SCHEMA`
- [ ] `v03-permissions-schema` — Add permissions table to `_SCHEMA`

After DDL is merged, Darlene handles the tool implementation.

— Elliot
