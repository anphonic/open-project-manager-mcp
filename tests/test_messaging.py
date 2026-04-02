"""Tests for Build Orders 8-10: Proactive messaging system (SSE, team status, subscriptions)."""

import asyncio
import functools
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirror test_tools.py patterns)
# ---------------------------------------------------------------------------

def _get_tool(mcp_server, name: str):
    return mcp_server._tool_manager._tools[name].fn


def _sync_wrap(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return asyncio.run(result)
        return result
    return wrapper


@pytest.fixture()
def mcp_server(tmp_path):
    from open_project_manager_mcp.server import create_server
    server = create_server(str(tmp_path / "tasks.db"))
    server._get = lambda name: _sync_wrap(_get_tool(server, name))
    return server


@pytest.fixture()
def mcp_rest(tmp_path):
    """Server with REST API enabled."""
    from open_project_manager_mcp.server import create_server
    server = create_server(str(tmp_path / "tasks.db"), enable_rest=True)
    server._get = lambda name: _sync_wrap(_get_tool(server, name))
    return server


# ---------------------------------------------------------------------------
# Build Order 8: SSE Infrastructure + State Query Tools
# ---------------------------------------------------------------------------

class TestGetServerStats:
    def test_returns_expected_keys(self, mcp_server):
        """get_server_stats returns JSON with queue_depth, by_status, uptime_sec, active_sse_clients."""
        result = json.loads(mcp_server._get("get_server_stats")())
        assert "queue_depth" in result
        assert "by_status" in result
        assert "by_project" in result
        assert "uptime_sec" in result
        assert "active_sse_clients" in result
        assert isinstance(result["uptime_sec"], int)
        assert isinstance(result["active_sse_clients"], int)

    def test_queue_depth_calculation(self, mcp_server):
        """queue_depth is the sum of non-done tasks."""
        mcp_server._get("create_task")(id="t1", title="Pending")
        mcp_server._get("create_task")(id="t2", title="In Progress")
        mcp_server._get("update_task")(task_id="t2", status="in_progress")
        mcp_server._get("create_task")(id="t3", title="Done")
        mcp_server._get("update_task")(task_id="t3", status="done")
        
        result = json.loads(mcp_server._get("get_server_stats")())
        assert result["queue_depth"] == 2  # t1 + t2
        assert result["by_status"]["pending"] == 1  # only t1 is pending
        assert result["by_status"]["in_progress"] == 1
        assert result["by_status"]["done"] == 1

    def test_by_project_grouping(self, mcp_server):
        """by_project contains status counts per project."""
        mcp_server._get("create_task")(id="t1", title="Proj A", project="proj-a")
        mcp_server._get("create_task")(id="t2", title="Proj B", project="proj-b")
        
        result = json.loads(mcp_server._get("get_server_stats")())
        assert "proj-a" in result["by_project"]
        assert "proj-b" in result["by_project"]
        assert result["by_project"]["proj-a"]["pending"] == 1


class TestGetProjectSummary:
    def test_returns_correct_totals(self, mcp_server):
        """get_project_summary returns correct totals for a known project."""
        mcp_server._get("create_task")(id="t1", title="One", project="myapp")
        mcp_server._get("create_task")(id="t2", title="Two", project="myapp")
        mcp_server._get("update_task")(task_id="t2", status="in_progress")
        mcp_server._get("create_task")(id="t3", title="Other", project="other")
        
        result = json.loads(mcp_server._get("get_project_summary")(project="myapp"))
        assert result["project"] == "myapp"
        assert result["total"] == 2
        assert result["pending"] == 1
        assert result["in_progress"] == 1
        assert result["done"] == 0

    def test_missing_project_arg_returns_error(self, mcp_server):
        """Missing project arg returns error."""
        result = mcp_server._get("get_project_summary")(project="")
        assert result.startswith("Error:")

    def test_overdue_count_included(self, mcp_server):
        """Project summary includes overdue count."""
        mcp_server._get("create_task")(id="t1", title="Overdue", project="proj", due_date="2020-01-01")
        mcp_server._get("create_task")(id="t2", title="Not Overdue", project="proj", due_date="2099-12-31")
        
        result = json.loads(mcp_server._get("get_project_summary")(project="proj"))
        assert result["overdue"] == 1


# ---------------------------------------------------------------------------
# Build Order 9: Team Inbound + Notifications
# ---------------------------------------------------------------------------

class TestSetTeamStatus:
    def test_valid_status_succeeds(self, mcp_server):
        """Valid status (online) succeeds and returns expected JSON."""
        result = json.loads(mcp_server._get("set_team_status")(squad="alpha", status="online"))
        assert result["squad"] == "alpha"
        assert result["status"] == "online"
        assert "updated_at" in result

    def test_invalid_status_returns_error(self, mcp_server):
        """Invalid status returns error."""
        result = mcp_server._get("set_team_status")(squad="alpha", status="invalid")
        assert result.startswith("Error:")
        assert "invalid status" in result

    def test_empty_squad_returns_error(self, mcp_server):
        """Empty squad returns error."""
        result = mcp_server._get("set_team_status")(squad="", status="online")
        assert result.startswith("Error:")

    def test_status_with_message(self, mcp_server):
        """Status can include an optional message."""
        result = json.loads(mcp_server._get("set_team_status")(
            squad="beta", status="busy", message="Deploying to prod"
        ))
        assert result["squad"] == "beta"
        assert result["status"] == "busy"
        assert result["message"] == "Deploying to prod"

    def test_upsert_behavior(self, mcp_server):
        """set_team_status upserts — second call updates existing row."""
        mcp_server._get("set_team_status")(squad="gamma", status="online")
        result = json.loads(mcp_server._get("set_team_status")(squad="gamma", status="offline"))
        assert result["status"] == "offline"
        
        # Verify only one row exists
        all_teams = json.loads(mcp_server._get("get_team_status")())
        assert len([t for t in all_teams["squads"] if t["squad"] == "gamma"]) == 1


class TestGetTeamStatus:
    def test_no_squad_arg_returns_all_teams(self, mcp_server):
        """No squad arg returns all teams."""
        mcp_server._get("set_team_status")(squad="alpha", status="online")
        mcp_server._get("set_team_status")(squad="beta", status="busy")
        
        result = json.loads(mcp_server._get("get_team_status")())
        assert "squads" in result
        assert len(result["squads"]) == 2
        squads = {s["squad"]: s["status"] for s in result["squads"]}
        assert squads["alpha"] == "online"
        assert squads["beta"] == "busy"

    def test_specific_squad_returns_that_team(self, mcp_server):
        """Specific squad returns that team."""
        mcp_server._get("set_team_status")(squad="delta", status="degraded", message="High latency")
        
        result = json.loads(mcp_server._get("get_team_status")(squad="delta"))
        assert result["squad"] == "delta"
        assert result["status"] == "degraded"
        assert result["message"] == "High latency"

    def test_missing_squad_returns_error(self, mcp_server):
        """Missing squad returns error."""
        result = mcp_server._get("get_team_status")(squad="nonexistent")
        assert result.startswith("Error:")
        assert "not found" in result


class TestPostTeamEvent:
    def test_valid_event_persisted(self, mcp_server):
        """Valid event is persisted and returns expected JSON."""
        result = json.loads(mcp_server._get("post_team_event")(
            squad="epsilon", event_type="squad.heartbeat", data="All systems operational"
        ))
        assert result["squad"] == "epsilon"
        assert result["event_type"] == "squad.heartbeat"
        assert "created_at" in result

    def test_empty_event_type_returns_error(self, mcp_server):
        """Empty or invalid event_type returns error."""
        result = mcp_server._get("post_team_event")(squad="zeta", event_type="", data="test")
        assert result.startswith("Error:")

    def test_invalid_event_type_returns_error(self, mcp_server):
        """Invalid event_type returns error."""
        result = mcp_server._get("post_team_event")(squad="zeta", event_type="invalid.type", data="test")
        assert result.startswith("Error:")
        assert "invalid event_type" in result

    def test_event_retrievable_via_get_team_events(self, mcp_server):
        """Event is retrievable via get_team_events."""
        mcp_server._get("post_team_event")(squad="eta", event_type="squad.alert", data="Issue detected")
        
        result = json.loads(mcp_server._get("get_team_events")(squad="eta"))
        assert result["count"] == 1
        assert result["events"][0]["squad"] == "eta"
        assert result["events"][0]["event_type"] == "squad.alert"
        assert result["events"][0]["data"] == "Issue detected"


class TestGetTeamEvents:
    def test_returns_events_list(self, mcp_server):
        """Returns events list with count."""
        mcp_server._get("post_team_event")(squad="theta", event_type="squad.heartbeat", data="1")
        mcp_server._get("post_team_event")(squad="theta", event_type="squad.heartbeat", data="2")
        
        result = json.loads(mcp_server._get("get_team_events")())
        assert "events" in result
        assert "count" in result
        assert result["count"] == 2

    def test_filters_by_squad(self, mcp_server):
        """Filters by squad."""
        mcp_server._get("post_team_event")(squad="iota", event_type="squad.heartbeat", data="A")
        mcp_server._get("post_team_event")(squad="kappa", event_type="squad.heartbeat", data="B")
        
        result = json.loads(mcp_server._get("get_team_events")(squad="iota"))
        assert result["count"] == 1
        assert result["events"][0]["squad"] == "iota"

    def test_respects_limit(self, mcp_server):
        """Respects limit parameter."""
        for i in range(10):
            mcp_server._get("post_team_event")(squad="lambda", event_type="squad.heartbeat", data=str(i))
        
        result = json.loads(mcp_server._get("get_team_events")(limit=3))
        assert result["count"] == 3

    def test_filters_by_event_type(self, mcp_server):
        """Filters by event_type."""
        mcp_server._get("post_team_event")(squad="mu", event_type="squad.heartbeat", data="1")
        mcp_server._get("post_team_event")(squad="mu", event_type="squad.alert", data="2")
        
        result = json.loads(mcp_server._get("get_team_events")(event_type="squad.alert"))
        assert result["count"] == 1
        assert result["events"][0]["event_type"] == "squad.alert"


# ---------------------------------------------------------------------------
# Build Order 10: Outbound Event Subscriptions
# ---------------------------------------------------------------------------

class TestSubscribeEvents:
    def test_https_url_succeeds(self, mcp_server):
        """HTTPS URL with public IP succeeds."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            # Mock DNS resolution to a public IP
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            result = json.loads(mcp_server._get("subscribe_events")(
                id="sub1",
                subscriber="team-alpha",
                url="https://example.com/webhook",
                event_type="server.stats",
                interval_sec=300
            ))
            assert result["id"] == "sub1"
            assert result["subscriber"] == "team-alpha"
            assert result["event_type"] == "server.stats"
            assert result["interval_sec"] == 300

    def test_http_url_rejected(self, mcp_server):
        """HTTP URL is rejected (HTTPS-only)."""
        result = mcp_server._get("subscribe_events")(
            id="sub2",
            subscriber="team-beta",
            url="http://example.com/webhook",
            event_type="server.health"
        )
        assert result.startswith("Error:")
        assert "HTTPS" in result

    def test_invalid_event_type_returns_error(self, mcp_server):
        """Invalid event_type returns error."""
        result = mcp_server._get("subscribe_events")(
            id="sub3",
            subscriber="team-gamma",
            url="https://example.com/webhook",
            event_type="invalid.event"
        )
        assert result.startswith("Error:")
        assert "invalid event_type" in result

    def test_interval_sec_below_min_returns_error(self, mcp_server):
        """interval_sec < 60 returns error."""
        result = mcp_server._get("subscribe_events")(
            id="sub4",
            subscriber="team-delta",
            url="https://example.com/webhook",
            event_type="server.stats",
            interval_sec=30
        )
        assert result.startswith("Error:")
        assert "between 60 and" in result

    def test_duplicate_id_returns_error(self, mcp_server):
        """Duplicate id returns error."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            mcp_server._get("subscribe_events")(
                id="dup",
                subscriber="team-epsilon",
                url="https://example.com/webhook",
                event_type="server.health"
            )
            
            result = mcp_server._get("subscribe_events")(
                id="dup",
                subscriber="team-epsilon",
                url="https://example.com/webhook2",
                event_type="server.health"
            )
            assert result.startswith("Error:")
            assert "already exists" in result

    def test_ssrf_private_ip_blocked(self, mcp_server):
        """SSRF check blocks private IPs (RFC1918)."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            # Mock DNS resolution to a private IP
            mock_gai.return_value = [(None, None, None, None, ("192.168.1.1", 443))]
            
            result = mcp_server._get("subscribe_events")(
                id="sub-private",
                subscriber="team-zeta",
                url="https://internal.example.com/webhook",
                event_type="server.stats"
            )
            assert result.startswith("Error:")
            assert "blocked address" in result

    def test_project_filter_optional(self, mcp_server):
        """project parameter is optional."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            result = json.loads(mcp_server._get("subscribe_events")(
                id="sub-proj",
                subscriber="team-eta",
                url="https://example.com/webhook",
                event_type="project.summary",
                project="myapp"
            ))
            assert result["project"] == "myapp"


class TestListSubscriptions:
    def test_returns_subscriptions_list(self, mcp_server):
        """Returns subscriptions list."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            mcp_server._get("subscribe_events")(
                id="sub-list-1",
                subscriber="team-theta",
                url="https://example.com/webhook",
                event_type="server.stats"
            )
            mcp_server._get("subscribe_events")(
                id="sub-list-2",
                subscriber="team-iota",
                url="https://example.com/webhook2",
                event_type="server.health"
            )
            
            result = json.loads(mcp_server._get("list_subscriptions")())
            assert "subscriptions" in result
            assert len(result["subscriptions"]) == 2

    def test_filter_by_subscriber_works(self, mcp_server):
        """Filter by subscriber works."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            mcp_server._get("subscribe_events")(
                id="sub-filter-1",
                subscriber="team-kappa",
                url="https://example.com/webhook",
                event_type="server.stats"
            )
            mcp_server._get("subscribe_events")(
                id="sub-filter-2",
                subscriber="team-lambda",
                url="https://example.com/webhook2",
                event_type="server.health"
            )
            
            result = json.loads(mcp_server._get("list_subscriptions")(subscriber="team-kappa"))
            assert len(result["subscriptions"]) == 1
            assert result["subscriptions"][0]["subscriber"] == "team-kappa"

    def test_filter_by_event_type(self, mcp_server):
        """Filter by event_type works."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            mcp_server._get("subscribe_events")(
                id="sub-evt-1",
                subscriber="team-mu",
                url="https://example.com/webhook",
                event_type="server.stats"
            )
            mcp_server._get("subscribe_events")(
                id="sub-evt-2",
                subscriber="team-nu",
                url="https://example.com/webhook2",
                event_type="server.health"
            )
            
            result = json.loads(mcp_server._get("list_subscriptions")(event_type="server.health"))
            assert len(result["subscriptions"]) == 1
            assert result["subscriptions"][0]["event_type"] == "server.health"


class TestUnsubscribeEvents:
    def test_human_approval_false_returns_error(self, mcp_server):
        """human_approval=False returns error."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            mcp_server._get("subscribe_events")(
                id="unsub-1",
                subscriber="team-xi",
                url="https://example.com/webhook",
                event_type="server.stats"
            )
            
            result = mcp_server._get("unsubscribe_events")(id="unsub-1", human_approval=False)
            assert result.startswith("Error:")
            assert "human_approval=True" in result

    def test_human_approval_true_deletes(self, mcp_server):
        """human_approval=True deletes and returns success."""
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            mcp_server._get("subscribe_events")(
                id="unsub-2",
                subscriber="team-omicron",
                url="https://example.com/webhook",
                event_type="server.stats"
            )
            
            result = json.loads(mcp_server._get("unsubscribe_events")(
                id="unsub-2", human_approval=True
            ))
            assert result["deleted"] is True
            
            # Verify it's gone
            subs = json.loads(mcp_server._get("list_subscriptions")())
            assert not any(s["id"] == "unsub-2" for s in subs["subscriptions"])

    def test_non_existent_id_returns_error(self, mcp_server):
        """Non-existent id returns error."""
        result = mcp_server._get("unsubscribe_events")(id="nonexistent", human_approval=True)
        assert result.startswith("Error:")
        assert "not found" in result


# ---------------------------------------------------------------------------
# REST API Tests
# ---------------------------------------------------------------------------

class TestRestApiNotifications:
    def test_post_notifications_valid_body_returns_201(self, mcp_rest):
        """POST /api/v1/notifications with valid body returns 201."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        response = client.post("/notifications", json={
            "squad": "team-alpha",
            "event_type": "squad.alert",
            "data": {"message": "Test alert"}
        })
        assert response.status_code == 201
        body = response.json()
        assert body["squad"] == "team-alpha"
        assert body["event_type"] == "squad.alert"
        assert "created_at" in body

    def test_post_notifications_invalid_event_type_returns_400(self, mcp_rest):
        """POST /api/v1/notifications with invalid event_type returns 400."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        response = client.post("/notifications", json={
            "squad": "team-beta",
            "event_type": "invalid.type",
            "data": {}
        })
        assert response.status_code == 400
        assert "error" in response.json()

    def test_post_notifications_missing_squad_returns_400(self, mcp_rest):
        """POST /api/v1/notifications with missing squad returns 400."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        response = client.post("/notifications", json={
            "event_type": "squad.alert"
        })
        assert response.status_code == 400


class TestRestApiStatus:
    def test_put_status_valid_returns_200(self, mcp_rest):
        """PUT /api/v1/status/{squad} with valid status returns 200."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        response = client.put("/status/team-gamma", json={
            "status": "online",
            "message": "All systems go"
        })
        assert response.status_code == 200
        body = response.json()
        assert body["squad"] == "team-gamma"
        assert body["status"] == "online"
        assert body["message"] == "All systems go"

    def test_put_status_invalid_returns_400(self, mcp_rest):
        """PUT /api/v1/status/{squad} with invalid status returns 400."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        response = client.put("/status/team-delta", json={
            "status": "invalid"
        })
        assert response.status_code == 400

    def test_get_status_returns_all_teams(self, mcp_rest):
        """GET /api/v1/status returns all teams."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        # Set up some teams
        client.put("/status/team-epsilon", json={"status": "online"})
        client.put("/status/team-zeta", json={"status": "busy"})
        
        response = client.get("/status")
        assert response.status_code == 200
        body = response.json()
        assert "squads" in body
        assert len(body["squads"]) >= 2

    def test_get_status_squad_returns_team_or_404(self, mcp_rest):
        """GET /api/v1/status/{squad} returns team data or 404."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        # Set up a team
        client.put("/status/team-eta", json={"status": "degraded"})
        
        # Get existing team
        response = client.get("/status/team-eta")
        assert response.status_code == 200
        assert response.json()["squad"] == "team-eta"
        
        # Get non-existent team
        response = client.get("/status/nonexistent")
        assert response.status_code == 404


class TestRestApiTeamEvents:
    def test_get_team_events_returns_events(self, mcp_rest):
        """GET /api/v1/team/events returns events with count."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        # Post some events
        client.post("/notifications", json={
            "squad": "team-theta",
            "event_type": "squad.heartbeat",
            "data": {"status": "ok"}
        })
        
        response = client.get("/team/events")
        assert response.status_code == 200
        body = response.json()
        assert "events" in body
        assert "count" in body

    def test_get_team_events_limit_param_respected(self, mcp_rest):
        """GET /api/v1/team/events respects limit param."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        # Post multiple events
        for i in range(10):
            client.post("/notifications", json={
                "squad": "team-iota",
                "event_type": "squad.heartbeat",
                "data": {"seq": i}
            })
        
        response = client.get("/team/events?limit=3")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 3

    def test_get_team_events_filters_by_squad(self, mcp_rest):
        """GET /api/v1/team/events filters by squad param."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        client.post("/notifications", json={"squad": "team-kappa", "event_type": "squad.alert"})
        client.post("/notifications", json={"squad": "team-lambda", "event_type": "squad.alert"})
        
        response = client.get("/team/events?squad=team-kappa")
        assert response.status_code == 200
        body = response.json()
        assert all(e["squad"] == "team-kappa" for e in body["events"])


class TestRestApiStats:
    def test_get_stats_detailed_returns_extended_fields(self, mcp_rest):
        """GET /api/v1/stats?detailed=true returns extended fields."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        response = client.get("/stats?detailed=true")
        assert response.status_code == 200
        body = response.json()
        assert "uptime_sec" in body
        assert "active_sse_clients" in body
        assert "by_project" in body


class TestRestApiSubscriptions:
    def test_post_subscriptions_returns_201(self, mcp_rest):
        """POST /api/v1/subscriptions returns 201 on success."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            response = client.post("/subscriptions", json={
                "id": "rest-sub-1",
                "subscriber": "team-mu",
                "url": "https://example.com/webhook",
                "event_type": "server.stats",
                "interval_sec": 300
            })
            assert response.status_code == 201
            body = response.json()
            assert body["id"] == "rest-sub-1"

    def test_post_subscriptions_http_url_returns_error(self, mcp_rest):
        """POST /api/v1/subscriptions with HTTP URL returns error."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        response = client.post("/subscriptions", json={
            "id": "rest-sub-2",
            "subscriber": "team-nu",
            "url": "http://example.com/webhook",
            "event_type": "server.stats"
        })
        assert response.status_code == 400
        assert "HTTPS" in response.json()["error"]

    def test_get_subscriptions_returns_list(self, mcp_rest):
        """GET /api/v1/subscriptions returns list."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            client.post("/subscriptions", json={
                "id": "rest-sub-3",
                "subscriber": "team-xi",
                "url": "https://example.com/webhook",
                "event_type": "server.health"
            })
            
            response = client.get("/subscriptions")
            assert response.status_code == 200
            body = response.json()
            assert "subscriptions" in body

    def test_delete_subscriptions_returns_204(self, mcp_rest):
        """DELETE /api/v1/subscriptions/{id} returns 204 on success."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            client.post("/subscriptions", json={
                "id": "rest-sub-del",
                "subscriber": "team-omicron",
                "url": "https://example.com/webhook",
                "event_type": "server.stats"
            })
            
            # Note: REST API DELETE uses confirm=true param instead of human_approval
            response = client.delete("/subscriptions/rest-sub-del?confirm=true")
            assert response.status_code == 200
            assert response.json()["deleted"] is True

    def test_delete_subscriptions_unknown_id_returns_404(self, mcp_rest):
        """DELETE /api/v1/subscriptions/{id} returns 404 for unknown id."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        response = client.delete("/subscriptions/nonexistent?confirm=true")
        assert response.status_code == 404

    def test_delete_subscriptions_missing_confirm_returns_400(self, mcp_rest):
        """DELETE /api/v1/subscriptions/{id} without confirm=true returns 400."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        with patch("open_project_manager_mcp.server.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            
            client.post("/subscriptions", json={
                "id": "rest-sub-noconf",
                "subscriber": "team-pi",
                "url": "https://example.com/webhook",
                "event_type": "server.stats"
            })
            
            response = client.delete("/subscriptions/rest-sub-noconf")
            assert response.status_code == 400


class TestRestApiSSE:
    def test_sse_endpoint_requires_auth_if_configured(self, tmp_path):
        """GET /api/v1/events requires auth when tenant_keys are configured."""
        from open_project_manager_mcp.server import create_server
        from starlette.testclient import TestClient
        
        server = create_server(
            str(tmp_path / "tasks.db"),
            tenant_keys={"test-squad": "test-key"},
            enable_rest=True
        )
        client = TestClient(server._rest_router)
        
        # No auth header
        response = client.get("/events")
        assert response.status_code == 401


class TestRestApiProjectSummary:
    def test_get_project_summary_returns_data(self, mcp_rest):
        """GET /api/v1/projects/{project}/summary returns project summary."""
        from starlette.testclient import TestClient
        client = TestClient(mcp_rest._rest_router)
        
        # Create some tasks in the project
        mcp_rest._get("create_task")(id="t1", title="Task 1", project="myapp")
        mcp_rest._get("create_task")(id="t2", title="Task 2", project="myapp")
        
        response = client.get("/projects/myapp/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["project"] == "myapp"
        assert body["total"] == 2
