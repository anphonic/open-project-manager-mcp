# OPM v0.3.0 Integration Test Plan

**Author:** Mobley (Integration & External Systems Specialist)  
**Date:** 2026-04-04  
**Status:** APPROVED

---

## Overview

This document defines curl-based smoke tests for OPM v0.3.0 REST API endpoints. Tests validate:
1. **Telemetry** — 4 endpoints
2. **Permissions** — 4 endpoints (+ 2 recommended additions)

**Test environment:**
- OPM running on http://192.168.1.178:8765 with `--rest-api` flag
- Two test tenants: `test-squad-alpha` and `test-squad-beta`
- Bearer tokens stored in `$ALPHA_TOKEN` and `$BETA_TOKEN` env vars

**Pre-requisites:**
- OPM v0.3.0 deployed with telemetry + permissions schema
- Test tokens registered via `/api/v1/register` or `OPM_TENANT_KEYS` env var
- `jq` installed for JSON parsing

---

## 1. Test Setup

### 1.1 Environment Variables

```bash
export OPM_BASE_URL="http://192.168.1.178:8765"
export ALPHA_TOKEN="<test-squad-alpha-token>"
export BETA_TOKEN="<test-squad-beta-token>"
export REG_KEY="<OPM_REGISTRATION_KEY>"  # for registration tests
```

### 1.2 Helper Function

```bash
# Helper to make authenticated requests
opm_curl() {
    local method=$1
    local path=$2
    local token=${3:-$ALPHA_TOKEN}
    shift 3
    curl -s -X "$method" \
         -H "Authorization: Bearer $token" \
         -H "Content-Type: application/json" \
         "$OPM_BASE_URL$path" \
         "$@"
}
```

### 1.3 Test Data Seed

Generate test data for telemetry:

```bash
# Create 10 tasks to generate telemetry metrics
for i in {1..10}; do
    opm_curl POST "/api/v1/tasks" "$ALPHA_TOKEN" \
        -d "{\"id\":\"test-task-$i\", \"title\":\"Test Task $i\", \"project\":\"test-telemetry\"}"
done

# Create a few tasks with errors (invalid priority)
opm_curl POST "/api/v1/tasks" "$ALPHA_TOKEN" \
    -d '{"id":"test-fail-1", "title":"Fail Task", "priority":"invalid"}'
```

---

## 2. Telemetry Endpoint Tests

### 2.1 GET /api/v1/telemetry/summary

**Test Case:** Basic telemetry summary retrieval

```bash
# Test: Get 24-hour summary
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/summary" | jq .

# Expected:
# {
#   "hours": 24,
#   "metrics": [
#     {
#       "metric_type": "rest_call",
#       "metric_name": "/api/v1/tasks",
#       "total_calls": 11,
#       "total_errors": 1,
#       "avg_latency_ms": 15.23
#     }
#   ]
# }

# Verify: 
# - Status 200
# - "metrics" array present
# - total_calls > 0
```

**Test Case:** Custom time window

```bash
# Test: Get 1-hour summary
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/summary?hours=1" | jq .

# Verify:
# - "hours": 1 in response
# - Status 200
```

**Test Case:** Tenant isolation

```bash
# Test: Beta tenant sees no alpha's metrics
curl -s -H "Authorization: Bearer $BETA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/summary" | jq .metrics

# Verify:
# - Empty array OR only beta's own metrics
# - No metrics from alpha's test tasks
```

**Test Case:** Unauthenticated access

```bash
# Test: No token
curl -s "$OPM_BASE_URL/api/v1/telemetry/summary" | jq .

# Expected:
# {
#   "error": "Unauthorized"
# }
# Status: 401
```

---

### 2.2 GET /api/v1/telemetry/tools/{tool_name}

**Test Case:** Per-tool metrics

```bash
# Test: Get create_task metrics
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/tools/create_task" | jq .

# Expected:
# {
#   "tool": "create_task",
#   "hours": 24,
#   "buckets": [
#     {
#       "bucket_hour": "2026-04-04T15:00:00Z",
#       "count": 10,
#       "error_count": 0,
#       "avg_latency_ms": 12.5,
#       "min_ms": 8,
#       "max_ms": 23
#     }
#   ]
# }

# Verify:
# - buckets array has entries
# - bucket_hour in ISO8601 format
# - count matches number of created tasks
```

**Test Case:** Non-existent tool

```bash
# Test: Query tool that doesn't exist
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/tools/fake_tool_xyz" | jq .

# Expected:
# {
#   "tool": "fake_tool_xyz",
#   "hours": 24,
#   "buckets": []
# }
# Status: 200 (not an error, just empty)
```

**Test Case:** Custom time window

```bash
# Test: Get last 2 hours
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/tools/create_task?hours=2" | jq .hours

# Verify: returns 2
```

---

### 2.3 GET /api/v1/telemetry/top

**Test Case:** Top tools list

```bash
# Test: Get top 5 tools
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/top?limit=5" | jq .

# Expected:
# {
#   "hours": 24,
#   "top_tools": [
#     {"metric_name": "create_task", "total_calls": 10},
#     {"metric_name": "list_tasks", "total_calls": 5}
#   ]
# }

# Verify:
# - Sorted by total_calls DESC
# - Array length <= 5
```

**Test Case:** Default limit

```bash
# Test: No limit param (should default to 10)
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/top" | jq '.top_tools | length'

# Verify: <= 10
```

**Test Case:** Large limit (should cap at 100)

```bash
# Test: Request 500 tools
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/top?limit=500" | jq '.top_tools | length'

# Verify: <= 100 (capped by implementation)
```

---

### 2.4 GET /api/v1/telemetry/errors

**Test Case:** Error summary

```bash
# Test: Get error summary after failed task creation
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/errors" | jq .

# Expected:
# {
#   "hours": 24,
#   "errors": [
#     {
#       "metric_name": "create_task",
#       "total_errors": 1,
#       "total_calls": 11
#     }
#   ]
# }

# Verify:
# - Only tools with total_errors > 0
# - total_calls >= total_errors
```

**Test Case:** No errors scenario

```bash
# Test: New tenant with no errors
curl -s -H "Authorization: Bearer $BETA_TOKEN" \
     "$OPM_BASE_URL/api/v1/telemetry/errors" | jq .errors

# Expected: []
# Verify: Empty array, not an error
```

---

## 3. Permissions Endpoint Tests

### 3.1 Pre-test Setup

Create test project and grant alpha ownership:

```bash
# Create project via task
opm_curl POST "/api/v1/tasks" "$ALPHA_TOKEN" \
    -d '{"id":"perm-test-1", "title":"Permission Test Task", "project":"test-perms"}'

# If enforcement enabled, run migration first
# (via MCP or curl if migration endpoint exposed)
```

---

### 3.2 GET /api/v1/projects/{project}/members

**Test Case:** List project members

```bash
# Test: Get members list
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" | jq .

# Expected:
# {
#   "project": "test-perms",
#   "members": [
#     {
#       "tenant_id": "test-squad-alpha",
#       "role": "owner",
#       "granted_by": "test-squad-alpha",
#       "created_at": "2026-04-04T10:00:00Z"
#     }
#   ]
# }

# Verify:
# - members array has alpha as owner
```

**Test Case:** Access denied (no permissions)

```bash
# Test: Beta tries to list alpha's project members
curl -s -H "Authorization: Bearer $BETA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" | jq .

# Expected (if enforcement enabled):
# {
#   "error": "Error: access denied to project 'test-perms'"
# }
# Status: 403

# Expected (if enforcement disabled):
# Same as alpha's response (permission system not active)
```

---

### 3.3 POST /api/v1/projects/{project}/members

**Test Case:** Grant contributor access

```bash
# Test: Alpha grants beta contributor access
curl -s -X POST \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     -H "Content-Type: application/json" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" \
     -d '{
       "tenant_id": "test-squad-beta",
       "role": "contributor",
       "human_approval": true
     }' | jq .

# Expected:
# {
#   "project": "test-perms",
#   "tenant_id": "test-squad-beta",
#   "role": "contributor",
#   "granted_by": "test-squad-alpha"
# }
# Status: 200 or 201

# Verify with GET:
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" | jq '.members | length'
# Should be 2 (alpha + beta)
```

**Test Case:** Grant without human_approval

```bash
# Test: Missing human_approval
curl -s -X POST \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     -H "Content-Type: application/json" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" \
     -d '{
       "tenant_id": "test-squad-gamma",
       "role": "reader"
     }' | jq .

# Expected:
# {
#   "error": "Error: human_approval=True required for grant_project_access"
# }
# Status: 400
```

**Test Case:** Invalid role

```bash
# Test: Grant with invalid role
curl -s -X POST \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     -H "Content-Type: application/json" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" \
     -d '{
       "tenant_id": "test-squad-gamma",
       "role": "admin",
       "human_approval": true
     }' | jq .

# Expected:
# {
#   "error": "Error: invalid role 'admin' (must be owner/contributor/reader)"
# }
# Status: 400
```

**Test Case:** Non-owner tries to grant

```bash
# Test: Beta (contributor) tries to grant access
curl -s -X POST \
     -H "Authorization: Bearer $BETA_TOKEN" \
     -H "Content-Type: application/json" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" \
     -d '{
       "tenant_id": "test-squad-gamma",
       "role": "reader",
       "human_approval": true
     }' | jq .

# Expected (if enforcement enabled):
# {
#   "error": "Error: insufficient permissions (have 'contributor', need 'owner')"
# }
# Status: 403
```

---

### 3.4 DELETE /api/v1/projects/{project}/members/{tenant_id}

**Test Case:** Revoke access

```bash
# Test: Alpha revokes beta's access
curl -s -X DELETE \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members/test-squad-beta?confirm=true"

# Expected: Empty response
# Status: 204

# Verify with GET:
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" | jq '.members | length'
# Should be 1 (only alpha)
```

**Test Case:** Revoke without confirm

```bash
# Test: Missing confirm param
curl -s -X DELETE \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members/test-squad-beta" | jq .

# Expected:
# {
#   "error": "Error: confirm=true is required"
# }
# Status: 400
```

**Test Case:** Revoke non-existent member

```bash
# Test: Revoke access for tenant that doesn't have access
curl -s -X DELETE \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members/test-squad-gamma?confirm=true" | jq .

# Expected:
# {
#   "error": "Error: no access entry found for 'test-squad-gamma' on 'test-perms'"
# }
# Status: 404
```

**Test Case:** Cannot revoke own access

```bash
# Test: Alpha tries to revoke own access
curl -s -X DELETE \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members/test-squad-alpha?confirm=true" | jq .

# Expected:
# {
#   "error": "Error: cannot revoke your own access (use transfer_project_ownership)"
# }
# Status: 400
```

---

### 3.5 GET /api/v1/projects/mine

**Test Case:** List caller's projects

```bash
# Test: Alpha's projects
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/mine" | jq .

# Expected:
# {
#   "tenant_id": "test-squad-alpha",
#   "projects": [
#     {
#       "project": "test-perms",
#       "role": "owner",
#       "created_at": "2026-04-04T10:00:00Z"
#     },
#     {
#       "project": "test-telemetry",
#       "role": "owner",
#       "created_at": "2026-04-04T09:30:00Z"
#     }
#   ]
# }

# Verify: All projects where alpha has access
```

**Test Case:** No projects

```bash
# Test: New tenant with no projects
curl -s -H "Authorization: Bearer $BETA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/mine" | jq .

# Expected:
# {
#   "tenant_id": "test-squad-beta",
#   "projects": []
# }
# Status: 200 (empty array is valid)
```

---

## 4. Recommended Endpoint Tests

### 4.1 POST /api/v1/projects/{project}/transfer

**Note:** Only test if Darlene implements this endpoint per spec recommendation.

```bash
# Test: Transfer ownership
curl -s -X POST \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     -H "Content-Type: application/json" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/transfer" \
     -d '{
       "new_owner_tenant_id": "test-squad-beta",
       "human_approval": true
     }' | jq .

# Expected:
# {
#   "transferred": true,
#   "project": "test-perms",
#   "new_owner": "test-squad-beta",
#   "previous_owner": "test-squad-alpha",
#   "previous_owner_new_role": "contributor"
# }

# Verify:
# - Beta is now owner
# - Alpha is contributor
curl -s -H "Authorization: Bearer $BETA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" | jq '.members[] | select(.tenant_id=="test-squad-beta") | .role'
# Should be "owner"
```

---

### 4.2 GET /api/v1/projects/{project}/members/{tenant_id}

**Note:** Only test if implemented.

```bash
# Test: Check specific member access
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members/test-squad-beta" | jq .

# Expected:
# {
#   "project": "test-perms",
#   "tenant_id": "test-squad-beta",
#   "access": {
#     "role": "owner",
#     "granted_by": "test-squad-alpha",
#     "created_at": "2026-04-04T10:15:00Z"
#   }
# }

# Test: Non-existent access
curl -s -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members/test-squad-gamma" | jq .

# Expected:
# {
#   "project": "test-perms",
#   "tenant_id": "test-squad-gamma",
#   "access": null
# }
```

---

## 5. Error Handling Tests

### 5.1 Invalid JSON Body

```bash
# Test: Malformed JSON
curl -s -X POST \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     -H "Content-Type: application/json" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" \
     -d '{invalid json}' | jq .

# Expected:
# {
#   "error": "Error: invalid JSON body"
# }
# Status: 400
```

### 5.2 Large Request Body

```bash
# Test: Body exceeds _MAX_REST_BODY (1MB)
curl -s -X POST \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     -H "Content-Type: application/json" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" \
     -d "$(python3 -c 'print("{\"data\":\"" + "x"*1048577 + "\"}")')" | jq .

# Expected: Connection error or 413 Payload Too Large
# (Starlette should reject before reaching handler)
```

### 5.3 Database Error Simulation

**Note:** Requires manual DB corruption or permission change. Skip in automated tests.

```bash
# Test: Make DB read-only, attempt write
# (manual step: chmod 444 opm.db)

curl -s -X POST \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     -H "Content-Type: application/json" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members" \
     -d '{
       "tenant_id": "test-squad-gamma",
       "role": "reader",
       "human_approval": true
     }' | jq .

# Expected:
# {
#   "error": "Error: database error"
# }
# Status: 500

# Restore: chmod 644 opm.db
```

---

## 6. Integration Test Automation

### 6.1 Test Runner Script

Save as `test-v03-rest.sh`:

```bash
#!/bin/bash
set -e

# Configuration
export OPM_BASE_URL="${OPM_BASE_URL:-http://192.168.1.178:8765}"
export ALPHA_TOKEN="${ALPHA_TOKEN:-test-token-alpha}"
export BETA_TOKEN="${BETA_TOKEN:-test-token-beta}"

PASS=0
FAIL=0

# Helper: run test and check
test_curl() {
    local name="$1"
    local expected_status="$2"
    shift 2
    
    echo -n "Testing: $name ... "
    response=$(curl -s -w "\n%{http_code}" "$@")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" = "$expected_status" ]; then
        echo "✓ PASS (HTTP $http_code)"
        PASS=$((PASS + 1))
    else
        echo "✗ FAIL (expected $expected_status, got $http_code)"
        echo "  Response: $body"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== OPM v0.3.0 REST API Integration Tests ==="
echo

# Telemetry tests
echo "--- Telemetry Endpoints ---"
test_curl "Telemetry summary (authenticated)" 200 \
    -H "Authorization: Bearer $ALPHA_TOKEN" \
    "$OPM_BASE_URL/api/v1/telemetry/summary"

test_curl "Telemetry summary (unauthenticated)" 401 \
    "$OPM_BASE_URL/api/v1/telemetry/summary"

test_curl "Telemetry top tools" 200 \
    -H "Authorization: Bearer $ALPHA_TOKEN" \
    "$OPM_BASE_URL/api/v1/telemetry/top?limit=5"

test_curl "Telemetry errors" 200 \
    -H "Authorization: Bearer $ALPHA_TOKEN" \
    "$OPM_BASE_URL/api/v1/telemetry/errors"

# Permissions tests
echo
echo "--- Permissions Endpoints ---"

test_curl "List my projects" 200 \
    -H "Authorization: Bearer $ALPHA_TOKEN" \
    "$OPM_BASE_URL/api/v1/projects/mine"

test_curl "Grant access (missing human_approval)" 400 \
    -X POST \
    -H "Authorization: Bearer $ALPHA_TOKEN" \
    -H "Content-Type: application/json" \
    "$OPM_BASE_URL/api/v1/projects/test/members" \
    -d '{"tenant_id":"test-beta","role":"reader"}'

test_curl "Revoke without confirm" 400 \
    -X DELETE \
    -H "Authorization: Bearer $ALPHA_TOKEN" \
    "$OPM_BASE_URL/api/v1/projects/test/members/test-beta"

# Summary
echo
echo "=== Test Summary ==="
echo "PASSED: $PASS"
echo "FAILED: $FAIL"
[ $FAIL -eq 0 ] && echo "All tests passed ✓" || echo "Some tests failed ✗"
exit $FAIL
```

**Usage:**
```bash
chmod +x test-v03-rest.sh
./test-v03-rest.sh
```

---

## 7. Performance Baseline Tests

### 7.1 Latency Benchmarks

```bash
# Test: Average latency for telemetry summary
for i in {1..100}; do
    curl -s -o /dev/null -w "%{time_total}\n" \
         -H "Authorization: Bearer $ALPHA_TOKEN" \
         "$OPM_BASE_URL/api/v1/telemetry/summary"
done | awk '{sum+=$1; count++} END {print "Avg latency:", sum/count*1000, "ms"}'

# Expected: < 50ms for empty/small datasets
```

### 7.2 Concurrent Request Handling

```bash
# Test: 10 concurrent requests
seq 1 10 | xargs -P10 -I{} curl -s -o /dev/null -w "Request {}: %{http_code}\n" \
    -H "Authorization: Bearer $ALPHA_TOKEN" \
    "$OPM_BASE_URL/api/v1/telemetry/summary"

# Verify: All return 200
```

---

## 8. Test Cleanup

```bash
# Remove test tasks
for i in {1..10}; do
    curl -s -X DELETE \
         -H "Authorization: Bearer $ALPHA_TOKEN" \
         "$OPM_BASE_URL/api/v1/tasks/test-task-$i?confirm=true"
done

# Revoke test permissions (if granted)
curl -s -X DELETE \
     -H "Authorization: Bearer $ALPHA_TOKEN" \
     "$OPM_BASE_URL/api/v1/projects/test-perms/members/test-squad-beta?confirm=true"

# Delete test projects (via deleting all tasks then project is orphaned)
```

---

## 9. CI/CD Integration

### 9.1 GitHub Actions Workflow

```yaml
name: OPM v0.3.0 REST API Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  integration-tests:
    runs-on: ubuntu-latest
    
    services:
      opm:
        image: opm:v0.3.0
        env:
          OPM_TENANT_KEYS: "test-alpha:${{ secrets.TEST_ALPHA_TOKEN }},test-beta:${{ secrets.TEST_BETA_TOKEN }}"
        ports:
          - 8765:8765
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Wait for OPM
        run: |
          timeout 30 bash -c 'until curl -f http://localhost:8765/health; do sleep 1; done'
      
      - name: Run integration tests
        env:
          OPM_BASE_URL: http://localhost:8765
          ALPHA_TOKEN: ${{ secrets.TEST_ALPHA_TOKEN }}
          BETA_TOKEN: ${{ secrets.TEST_BETA_TOKEN }}
        run: |
          chmod +x .squad/agents/mobley/test-v03-rest.sh
          .squad/agents/mobley/test-v03-rest.sh
```

---

**Next Steps:**
1. Romero runs manual tests after Darlene's implementation
2. Automate via test runner script
3. Add to CI/CD pipeline (optional for v0.3.0)

**Test ownership:** Romero (QA Engineer)  
**Reviewed by:** Mobley
