"""Tests for v0.2.0 features: due-dates, FTS, bulk-ops, activity-log, export-import, REST, webhooks."""

import asyncio
import functools
import json
import socket
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
# Feature 1: Due dates
# ---------------------------------------------------------------------------

class TestCreateTaskWithDueDate:
    def test_create_with_valid_date(self, mcp_server):
        result = json.loads(mcp_server._get("create_task")(id="dd1", title="T", due_date="2099-12-31"))
        assert result["id"] == "dd1"

    def test_create_with_full_iso(self, mcp_server):
        result = json.loads(mcp_server._get("create_task")(id="dd2", title="T", due_date="2099-12-31T10:00:00+00:00"))
        assert result["id"] == "dd2"

    def test_create_with_invalid_date_returns_error(self, mcp_server):
        result = mcp_server._get("create_task")(id="dd3", title="T", due_date="not-a-date")
        assert "Error" in result

    def test_create_with_wrong_format_returns_error(self, mcp_server):
        result = mcp_server._get("create_task")(id="dd4", title="T", due_date="31/12/2099")
        assert "Error" in result

    def test_due_date_stored_and_retrievable(self, mcp_server):
        mcp_server._get("create_task")(id="dd5", title="T", due_date="2099-06-15")
        task = json.loads(mcp_server._get("get_task")(task_id="dd5"))
        assert task["due_date"] == "2099-06-15"

    def test_due_date_not_in_compact_list(self, mcp_server):
        mcp_server._get("create_task")(id="dd6", title="T", due_date="2099-06-15")
        rows = json.loads(mcp_server._get("list_tasks")())["tasks"]
        assert all("due_date" not in r for r in rows)

    def test_update_task_with_due_date(self, mcp_server):
        mcp_server._get("create_task")(id="dd7", title="T")
        mcp_server._get("update_task")(task_id="dd7", due_date="2099-01-01")
        task = json.loads(mcp_server._get("get_task")(task_id="dd7"))
        assert task["due_date"] == "2099-01-01"

    def test_update_task_with_invalid_due_date_returns_error(self, mcp_server):
        mcp_server._get("create_task")(id="dd8", title="T")
        result = mcp_server._get("update_task")(task_id="dd8", due_date="bad")
        assert "Error" in result


class TestListOverdueTasks:
    def test_overdue_task_appears(self, mcp_server):
        mcp_server._get("create_task")(id="ov1", title="Overdue", due_date="2000-01-01")
        result = json.loads(mcp_server._get("list_overdue_tasks")())
        ids = [t["id"] for t in result["tasks"]]
        assert "ov1" in ids

    def test_future_task_not_in_overdue(self, mcp_server):
        mcp_server._get("create_task")(id="ov2", title="Future", due_date="2099-01-01")
        result = json.loads(mcp_server._get("list_overdue_tasks")())
        ids = [t["id"] for t in result["tasks"]]
        assert "ov2" not in ids

    def test_done_task_excluded_from_overdue(self, mcp_server):
        mcp_server._get("create_task")(id="ov3", title="Done", due_date="2000-01-01")
        mcp_server._get("complete_task")(task_id="ov3")
        result = json.loads(mcp_server._get("list_overdue_tasks")())
        ids = [t["id"] for t in result["tasks"]]
        assert "ov3" not in ids

    def test_overdue_compact_fields_include_due_date(self, mcp_server):
        mcp_server._get("create_task")(id="ov4", title="T", due_date="2000-01-01")
        result = json.loads(mcp_server._get("list_overdue_tasks")())
        row = next(t for t in result["tasks"] if t["id"] == "ov4")
        assert "due_date" in row
        assert "id" in row
        assert "title" in row
        assert "priority" in row
        assert "status" in row

    def test_filter_by_project(self, mcp_server):
        mcp_server._get("create_task")(id="ov5", title="A", project="alpha", due_date="2000-01-01")
        mcp_server._get("create_task")(id="ov6", title="B", project="beta", due_date="2000-01-01")
        result = json.loads(mcp_server._get("list_overdue_tasks")(project="alpha"))
        ids = [t["id"] for t in result["tasks"]]
        assert "ov5" in ids
        assert "ov6" not in ids

    def test_no_due_date_not_in_overdue(self, mcp_server):
        mcp_server._get("create_task")(id="ov7", title="No due date")
        result = json.loads(mcp_server._get("list_overdue_tasks")())
        ids = [t["id"] for t in result["tasks"]]
        assert "ov7" not in ids


class TestListDueSoonTasks:
    def test_task_due_today_appears(self, mcp_server):
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        mcp_server._get("create_task")(id="ds1", title="Soon", due_date=tomorrow)
        result = json.loads(mcp_server._get("list_due_soon_tasks")(days=7))
        ids = [t["id"] for t in result["tasks"]]
        assert "ds1" in ids

    def test_task_due_in_30_days_not_in_7_day_window(self, mcp_server):
        far = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        mcp_server._get("create_task")(id="ds2", title="Far", due_date=far)
        result = json.loads(mcp_server._get("list_due_soon_tasks")(days=7))
        ids = [t["id"] for t in result["tasks"]]
        assert "ds2" not in ids

    def test_overdue_task_not_in_due_soon(self, mcp_server):
        mcp_server._get("create_task")(id="ds3", title="Old", due_date="2000-01-01")
        result = json.loads(mcp_server._get("list_due_soon_tasks")(days=7))
        ids = [t["id"] for t in result["tasks"]]
        assert "ds3" not in ids

    def test_days_capped_at_365(self, mcp_server):
        # Should not raise; just return result with capped days
        result = json.loads(mcp_server._get("list_due_soon_tasks")(days=9999))
        assert "tasks" in result

    def test_done_task_excluded(self, mcp_server):
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        mcp_server._get("create_task")(id="ds4", title="Done soon", due_date=tomorrow)
        mcp_server._get("complete_task")(task_id="ds4")
        result = json.loads(mcp_server._get("list_due_soon_tasks")(days=7))
        ids = [t["id"] for t in result["tasks"]]
        assert "ds4" not in ids


# ---------------------------------------------------------------------------
# Feature 2: Full-text search
# ---------------------------------------------------------------------------

class TestSearchTasks:
    def test_search_by_title(self, mcp_server):
        mcp_server._get("create_task")(id="fts1", title="Unicorn rainbow login")
        result = json.loads(mcp_server._get("search_tasks")(query="Unicorn"))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        ids = [t["id"] for t in result["tasks"]]
        assert "fts1" in ids

    def test_search_by_description(self, mcp_server):
        mcp_server._get("create_task")(id="fts2", title="Task", description="zephyr authentication flow")
        result = json.loads(mcp_server._get("search_tasks")(query="zephyr"))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        ids = [t["id"] for t in result["tasks"]]
        assert "fts2" in ids

    def test_search_by_tags(self, mcp_server):
        mcp_server._get("create_task")(id="fts3", title="Task", tags=["quasar", "backend"])
        result = json.loads(mcp_server._get("search_tasks")(query="quasar"))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        ids = [t["id"] for t in result["tasks"]]
        assert "fts3" in ids

    def test_no_match_returns_empty(self, mcp_server):
        result = json.loads(mcp_server._get("search_tasks")(query="xyzzy_nomatch_token"))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        assert result["tasks"] == []

    def test_search_filter_by_project(self, mcp_server):
        mcp_server._get("create_task")(id="fts4", title="nebula task", project="alpha")
        mcp_server._get("create_task")(id="fts5", title="nebula task", project="beta")
        result = json.loads(mcp_server._get("search_tasks")(query="nebula", project="alpha"))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        ids = [t["id"] for t in result["tasks"]]
        assert "fts4" in ids
        assert "fts5" not in ids

    def test_search_filter_by_status(self, mcp_server):
        mcp_server._get("create_task")(id="fts6", title="pulsar task")
        mcp_server._get("create_task")(id="fts7", title="pulsar task")
        mcp_server._get("complete_task")(task_id="fts7")
        result = json.loads(mcp_server._get("search_tasks")(query="pulsar", status="pending"))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        ids = [t["id"] for t in result["tasks"]]
        assert "fts6" in ids
        assert "fts7" not in ids

    def test_has_more_flag(self, mcp_server):
        for i in range(5):
            mcp_server._get("create_task")(id=f"fts_many{i}", title="cosmos repeated token")
        result = json.loads(mcp_server._get("search_tasks")(query="cosmos", limit=2))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        assert result["has_more"] is True
        assert len(result["tasks"]) == 2

    def test_query_too_long_returns_error(self, mcp_server):
        result = mcp_server._get("search_tasks")(query="x" * 501)
        assert "Error" in result

    def test_fts_updates_after_task_update(self, mcp_server):
        mcp_server._get("create_task")(id="fts_upd", title="original quartz title")
        mcp_server._get("update_task")(task_id="fts_upd", title="updated obsidian title")
        result = json.loads(mcp_server._get("search_tasks")(query="obsidian"))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        ids = [t["id"] for t in result["tasks"]]
        assert "fts_upd" in ids

    def test_fts_removes_after_delete(self, mcp_server):
        mcp_server._get("create_task")(id="fts_del", title="ephemeral tungsten word")
        mcp_server._get("delete_task")(task_id="fts_del", human_approval=True)
        result = json.loads(mcp_server._get("search_tasks")(query="tungsten"))
        if "Error" in str(result):
            pytest.skip("FTS5 not available")
        ids = [t["id"] for t in result["tasks"]]
        assert "fts_del" not in ids


# ---------------------------------------------------------------------------
# Feature 3: Bulk operations
# ---------------------------------------------------------------------------

class TestCreateTasks:
    def test_bulk_create_returns_created_ids(self, mcp_server):
        result = json.loads(mcp_server._get("create_tasks")(tasks=[
            {"id": "b1", "title": "Bulk 1"},
            {"id": "b2", "title": "Bulk 2"},
        ]))
        assert set(result["created"]) == {"b1", "b2"}
        assert result["errors"] == []

    def test_bulk_create_persists_tasks(self, mcp_server):
        mcp_server._get("create_tasks")(tasks=[{"id": "bp1", "title": "Persisted"}])
        task = json.loads(mcp_server._get("get_task")(task_id="bp1"))
        assert task["id"] == "bp1"

    def test_bulk_create_per_item_error_no_fail_fast(self, mcp_server):
        mcp_server._get("create_task")(id="dup_b", title="Existing")
        result = json.loads(mcp_server._get("create_tasks")(tasks=[
            {"id": "dup_b", "title": "Duplicate"},
            {"id": "new_b", "title": "New"},
        ]))
        assert "new_b" in result["created"]
        assert any(e["id"] == "dup_b" for e in result["errors"])

    def test_bulk_create_too_many_returns_error(self, mcp_server):
        tasks = [{"id": f"tb{i}", "title": f"T{i}"} for i in range(51)]
        result = mcp_server._get("create_tasks")(tasks=tasks)
        assert "Error" in result

    def test_bulk_create_validates_priority(self, mcp_server):
        result = json.loads(mcp_server._get("create_tasks")(tasks=[
            {"id": "bpri1", "title": "Valid"},
            {"id": "bpri2", "title": "Invalid", "priority": "super"},
        ]))
        assert "bpri1" in result["created"]
        assert any(e["id"] == "bpri2" for e in result["errors"])

    def test_bulk_create_with_due_date(self, mcp_server):
        result = json.loads(mcp_server._get("create_tasks")(tasks=[
            {"id": "bdd1", "title": "With date", "due_date": "2099-01-01"},
        ]))
        assert "bdd1" in result["created"]
        task = json.loads(mcp_server._get("get_task")(task_id="bdd1"))
        assert task["due_date"] == "2099-01-01"


class TestUpdateTasks:
    def test_bulk_update_returns_updated_ids(self, mcp_server):
        mcp_server._get("create_task")(id="bu1", title="Old")
        mcp_server._get("create_task")(id="bu2", title="Old")
        result = json.loads(mcp_server._get("update_tasks")(updates=[
            {"task_id": "bu1", "title": "New 1"},
            {"task_id": "bu2", "status": "in_progress"},
        ]))
        assert set(result["updated"]) == {"bu1", "bu2"}
        assert result["errors"] == []

    def test_bulk_update_missing_task_collected_as_error(self, mcp_server):
        result = json.loads(mcp_server._get("update_tasks")(updates=[
            {"task_id": "ghost_bu", "title": "X"},
        ]))
        assert any(e["id"] == "ghost_bu" for e in result["errors"])

    def test_bulk_update_too_many_returns_error(self, mcp_server):
        updates = [{"task_id": f"x{i}", "title": "T"} for i in range(51)]
        result = mcp_server._get("update_tasks")(updates=updates)
        assert "Error" in result

    def test_bulk_update_persists_changes(self, mcp_server):
        mcp_server._get("create_task")(id="bup_p", title="Before")
        mcp_server._get("update_tasks")(updates=[{"task_id": "bup_p", "title": "After"}])
        task = json.loads(mcp_server._get("get_task")(task_id="bup_p"))
        assert task["title"] == "After"


class TestCompleteTasks:
    def test_bulk_complete_marks_done(self, mcp_server):
        mcp_server._get("create_task")(id="bc1", title="T")
        mcp_server._get("create_task")(id="bc2", title="T")
        result = json.loads(mcp_server._get("complete_tasks")(ids=["bc1", "bc2"]))
        assert set(result["completed"]) == {"bc1", "bc2"}
        assert result["not_found"] == []

    def test_bulk_complete_missing_task_in_not_found(self, mcp_server):
        mcp_server._get("create_task")(id="bc3", title="T")
        result = json.loads(mcp_server._get("complete_tasks")(ids=["bc3", "ghost_bc"]))
        assert "bc3" in result["completed"]
        assert "ghost_bc" in result["not_found"]

    def test_bulk_complete_too_many_returns_error(self, mcp_server):
        result = mcp_server._get("complete_tasks")(ids=[f"x{i}" for i in range(51)])
        assert "Error" in result

    def test_bulk_complete_persists(self, mcp_server):
        mcp_server._get("create_task")(id="bcp1", title="T")
        mcp_server._get("complete_tasks")(ids=["bcp1"])
        task = json.loads(mcp_server._get("get_task")(task_id="bcp1"))
        assert task["status"] == "done"


# ---------------------------------------------------------------------------
# Feature 4: Activity log
# ---------------------------------------------------------------------------

class TestGetTaskActivity:
    def test_create_logs_created_action(self, mcp_server):
        mcp_server._get("create_task")(id="al1", title="T")
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al1"))
        actions = [e["action"] for e in result["activity"]]
        assert "created" in actions

    def test_complete_logs_completed_action(self, mcp_server):
        mcp_server._get("create_task")(id="al2", title="T")
        mcp_server._get("complete_task")(task_id="al2")
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al2"))
        actions = [e["action"] for e in result["activity"]]
        assert "completed" in actions

    def test_update_logs_updated_fields(self, mcp_server):
        mcp_server._get("create_task")(id="al3", title="Old")
        mcp_server._get("update_task")(task_id="al3", title="New")
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al3"))
        update_events = [e for e in result["activity"] if e["action"] == "updated"]
        assert any(e["field"] == "title" for e in update_events)

    def test_update_logs_old_and_new_value(self, mcp_server):
        mcp_server._get("create_task")(id="al4", title="Before")
        mcp_server._get("update_task")(task_id="al4", title="After")
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al4"))
        title_event = next(
            (e for e in result["activity"] if e["action"] == "updated" and e["field"] == "title"),
            None,
        )
        assert title_event is not None
        assert title_event["old_value"] == "Before"
        assert title_event["new_value"] == "After"

    def test_delete_logs_deleted_action(self, mcp_server):
        mcp_server._get("create_task")(id="al5", title="T")
        mcp_server._get("delete_task")(task_id="al5", human_approval=True)
        # Activity log is orphan-safe — history survives task deletion
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al5"))
        actions = [e["action"] for e in result["activity"]]
        assert "deleted" in actions

    def test_dep_added_logged(self, mcp_server):
        mcp_server._get("create_task")(id="al6a", title="A")
        mcp_server._get("create_task")(id="al6b", title="B")
        mcp_server._get("add_dependency")(task_id="al6b", depends_on_id="al6a")
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al6b"))
        actions = [e["action"] for e in result["activity"]]
        assert "dep_added" in actions

    def test_dep_removed_logged(self, mcp_server):
        mcp_server._get("create_task")(id="al7a", title="A")
        mcp_server._get("create_task")(id="al7b", title="B")
        mcp_server._get("add_dependency")(task_id="al7b", depends_on_id="al7a")
        mcp_server._get("remove_dependency")(task_id="al7b", depends_on_id="al7a")
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al7b"))
        actions = [e["action"] for e in result["activity"]]
        assert "dep_removed" in actions

    def test_missing_task_returns_empty(self, mcp_server):
        # Orphan-safe: unknown task returns empty activity list, not an error
        result = json.loads(mcp_server._get("get_task_activity")(task_id="ghost_al"))
        assert result["activity"] == []

    def test_limit_respected(self, mcp_server):
        mcp_server._get("create_task")(id="al8", title="T")
        for _ in range(5):
            mcp_server._get("update_task")(task_id="al8", title="X")
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al8", limit=2))
        assert len(result["activity"]) <= 2

    def test_results_newest_first(self, mcp_server):
        mcp_server._get("create_task")(id="al9", title="T")
        mcp_server._get("update_task")(task_id="al9", title="U1")
        mcp_server._get("update_task")(task_id="al9", title="U2")
        result = json.loads(mcp_server._get("get_task_activity")(task_id="al9"))
        timestamps = [e["created_at"] for e in result["activity"]]
        assert timestamps == sorted(timestamps, reverse=True)


class TestGetActivityLog:
    def test_returns_recent_activity(self, mcp_server):
        mcp_server._get("create_task")(id="gal1", title="T")
        result = json.loads(mcp_server._get("get_activity_log")())
        assert result["count"] >= 1

    def test_filter_by_project(self, mcp_server):
        mcp_server._get("create_task")(id="gal2", title="A", project="proj_x")
        mcp_server._get("create_task")(id="gal3", title="B", project="proj_y")
        result = json.loads(mcp_server._get("get_activity_log")(project="proj_x"))
        task_ids = {e["task_id"] for e in result["activity"]}
        assert "gal2" in task_ids
        assert "gal3" not in task_ids


# ---------------------------------------------------------------------------
# Feature 5: Export / Import
# ---------------------------------------------------------------------------

class TestExportAllTasks:
    def test_export_produces_valid_json(self, mcp_server):
        mcp_server._get("create_task")(id="ex1", title="T")
        result = json.loads(mcp_server._get("export_all_tasks")())
        assert result["version"] == "1.0"
        assert "exported_at" in result
        assert isinstance(result["tasks"], list)
        assert isinstance(result["deps"], list)

    def test_export_includes_all_tasks(self, mcp_server):
        mcp_server._get("create_task")(id="ex2", title="A")
        mcp_server._get("create_task")(id="ex3", title="B")
        result = json.loads(mcp_server._get("export_all_tasks")())
        ids = [t["id"] for t in result["tasks"]]
        assert "ex2" in ids
        assert "ex3" in ids

    def test_export_includes_due_date(self, mcp_server):
        mcp_server._get("create_task")(id="ex4", title="T", due_date="2099-01-01")
        result = json.loads(mcp_server._get("export_all_tasks")())
        task = next(t for t in result["tasks"] if t["id"] == "ex4")
        assert task["due_date"] == "2099-01-01"

    def test_export_tags_as_list(self, mcp_server):
        mcp_server._get("create_task")(id="ex5", title="Tagged", tags=["a", "b"])
        result = json.loads(mcp_server._get("export_all_tasks")())
        task = next(t for t in result["tasks"] if t["id"] == "ex5")
        assert task["tags"] == ["a", "b"]

    def test_export_filter_by_project(self, mcp_server):
        mcp_server._get("create_task")(id="ex6", title="A", project="p1")
        mcp_server._get("create_task")(id="ex7", title="B", project="p2")
        result = json.loads(mcp_server._get("export_all_tasks")(project="p1"))
        ids = [t["id"] for t in result["tasks"]]
        assert "ex6" in ids
        assert "ex7" not in ids

    def test_export_includes_deps(self, mcp_server):
        mcp_server._get("create_task")(id="ex8a", title="A")
        mcp_server._get("create_task")(id="ex8b", title="B")
        mcp_server._get("add_dependency")(task_id="ex8b", depends_on_id="ex8a")
        result = json.loads(mcp_server._get("export_all_tasks")())
        dep_pairs = [(d["task_id"], d["depends_on"]) for d in result["deps"]]
        assert ("ex8b", "ex8a") in dep_pairs

    def test_export_project_scoped_deps_excludes_cross_project(self, mcp_server):
        mcp_server._get("create_task")(id="ex9a", title="A", project="p1")
        mcp_server._get("create_task")(id="ex9b", title="B", project="p2")
        mcp_server._get("add_dependency")(task_id="ex9b", depends_on_id="ex9a")
        result = json.loads(mcp_server._get("export_all_tasks")(project="p1"))
        dep_pairs = [(d["task_id"], d["depends_on"]) for d in result["deps"]]
        assert ("ex9b", "ex9a") not in dep_pairs


class TestImportTasks:
    def _make_export(self, tasks, deps=None):
        return json.dumps({
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tasks": tasks,
            "deps": deps or [],
        })

    def test_import_creates_tasks(self, mcp_server):
        data = self._make_export([{"id": "imp1", "title": "Imported"}])
        result = json.loads(mcp_server._get("import_tasks")(data=data))
        assert result["imported"] == 1
        assert result["errors"] == []
        task = json.loads(mcp_server._get("get_task")(task_id="imp1"))
        assert task["id"] == "imp1"

    def test_import_without_merge_aborts_on_conflict(self, mcp_server):
        mcp_server._get("create_task")(id="imp2", title="Existing")
        data = self._make_export([{"id": "imp2", "title": "Conflict"}])
        result = mcp_server._get("import_tasks")(data=data, merge=False)
        assert "Error" in result
        assert "imp2" in result

    def test_import_with_merge_skips_existing(self, mcp_server):
        mcp_server._get("create_task")(id="imp3", title="Existing")
        data = self._make_export([
            {"id": "imp3", "title": "Skip this"},
            {"id": "imp3b", "title": "New one"},
        ])
        result = json.loads(mcp_server._get("import_tasks")(data=data, merge=True))
        assert result["skipped"] >= 1
        # imp3b should have been imported; verify via get_task
        assert "Error" not in mcp_server._get("get_task")(task_id="imp3b")
        assert result["errors"] == []

    def test_import_missing_version_returns_error(self, mcp_server):
        data = json.dumps({"tasks": [{"id": "x", "title": "T"}]})
        result = mcp_server._get("import_tasks")(data=data)
        assert "Error" in result

    def test_import_invalid_json_returns_error(self, mcp_server):
        result = mcp_server._get("import_tasks")(data="not-json")
        assert "Error" in result

    def test_import_size_cap(self, mcp_server):
        data = "x" * 5_000_001
        result = mcp_server._get("import_tasks")(data=data)
        assert "Error" in result

    def test_import_with_deps(self, mcp_server):
        data = self._make_export(
            [{"id": "imd1", "title": "A"}, {"id": "imd2", "title": "B"}],
            deps=[{"task_id": "imd2", "depends_on": "imd1"}],
        )
        result = json.loads(mcp_server._get("import_tasks")(data=data))
        assert result["imported"] == 2
        task = json.loads(mcp_server._get("get_task")(task_id="imd2"))
        assert "imd1" in task["depends_on"]

    def test_roundtrip_export_import(self, mcp_server):
        mcp_server._get("create_task")(id="rt1", title="Roundtrip", due_date="2099-06-01", tags=["x"])
        exported = mcp_server._get("export_all_tasks")()
        # Import into same server with merge (rt1 already exists)
        result = json.loads(mcp_server._get("import_tasks")(data=exported, merge=True))
        assert result["skipped"] >= 1


# ---------------------------------------------------------------------------
# Feature 6: REST API
# ---------------------------------------------------------------------------

class TestRestApi:
    @pytest.fixture()
    def client(self, tmp_path):
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        server = create_server(str(tmp_path / "rest.db"), enable_rest=True)
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])
        return TestClient(app, raise_server_exceptions=True)

    def test_get_tasks_empty(self, client):
        r = client.get("/api/v1/tasks")
        assert r.status_code == 200
        data = r.json()
        assert data["tasks"] == []

    def test_post_task_creates_and_returns_201(self, client):
        r = client.post("/api/v1/tasks", json={"id": "rest1", "title": "REST task"})
        assert r.status_code == 201
        assert r.json()["id"] == "rest1"

    def test_post_task_duplicate_returns_409(self, client):
        client.post("/api/v1/tasks", json={"id": "rest2", "title": "T"})
        r = client.post("/api/v1/tasks", json={"id": "rest2", "title": "T"})
        assert r.status_code == 409

    def test_post_task_invalid_priority_returns_400(self, client):
        r = client.post("/api/v1/tasks", json={"id": "rest3", "title": "T", "priority": "ultra"})
        assert r.status_code == 400

    def test_get_task_by_id(self, client):
        client.post("/api/v1/tasks", json={"id": "rest4", "title": "Get me"})
        r = client.get("/api/v1/tasks/rest4")
        assert r.status_code == 200
        assert r.json()["id"] == "rest4"

    def test_get_task_not_found_returns_404(self, client):
        r = client.get("/api/v1/tasks/ghost_rest")
        assert r.status_code == 404

    def test_patch_task(self, client):
        client.post("/api/v1/tasks", json={"id": "rest5", "title": "Old"})
        r = client.patch("/api/v1/tasks/rest5", json={"title": "New"})
        assert r.status_code == 200
        assert "title" in r.json()["updated"]

    def test_patch_task_not_found_returns_404(self, client):
        r = client.patch("/api/v1/tasks/ghost_patch", json={"title": "X"})
        assert r.status_code == 404

    def test_delete_task_requires_confirm(self, client):
        client.post("/api/v1/tasks", json={"id": "rest6", "title": "T"})
        r = client.delete("/api/v1/tasks/rest6")
        assert r.status_code == 400

    def test_delete_task_with_confirm(self, client):
        client.post("/api/v1/tasks", json={"id": "rest7", "title": "T"})
        r = client.delete("/api/v1/tasks/rest7?confirm=true")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

    def test_delete_task_not_found_returns_404(self, client):
        r = client.delete("/api/v1/tasks/ghost_del?confirm=true")
        assert r.status_code == 404

    def test_get_projects(self, client):
        client.post("/api/v1/tasks", json={"id": "rp1", "title": "T", "project": "rest-proj"})
        r = client.get("/api/v1/projects")
        assert r.status_code == 200
        names = [p["project"] for p in r.json()["projects"]]
        assert "rest-proj" in names

    def test_get_stats(self, client):
        r = client.get("/api/v1/stats")
        assert r.status_code == 200
        data = r.json()
        assert "by_status" in data
        assert "by_priority" in data
        assert "oldest_open" in data

    def test_list_tasks_filter_by_project(self, client):
        client.post("/api/v1/tasks", json={"id": "rfp1", "title": "A", "project": "rp_alpha"})
        client.post("/api/v1/tasks", json={"id": "rfp2", "title": "B", "project": "rp_beta"})
        r = client.get("/api/v1/tasks?project=rp_alpha")
        assert r.status_code == 200
        ids = [t["id"] for t in r.json()["tasks"]]
        assert "rfp1" in ids
        assert "rfp2" not in ids

    def test_list_tasks_pagination(self, client):
        for i in range(5):
            client.post("/api/v1/tasks", json={"id": f"rpag{i}", "title": f"T{i}"})
        r = client.get("/api/v1/tasks?limit=2")
        assert r.status_code == 200
        assert r.json()["has_more"] is True
        assert len(r.json()["tasks"]) == 2


class TestRestApiAuth:
    @pytest.fixture()
    def auth_client(self, tmp_path):
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        server = create_server(
            str(tmp_path / "auth.db"),
            tenant_keys={"squad1": "secret-token-1234"},
            enable_rest=True,
        )
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])
        return TestClient(app, raise_server_exceptions=True)

    def test_no_token_returns_401(self, auth_client):
        r = auth_client.get("/api/v1/tasks")
        assert r.status_code == 401

    def test_wrong_token_returns_401(self, auth_client):
        r = auth_client.get("/api/v1/tasks", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_correct_token_returns_200(self, auth_client):
        r = auth_client.get("/api/v1/tasks", headers={"Authorization": "Bearer secret-token-1234"})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Feature 7: Webhooks
# ---------------------------------------------------------------------------

class TestRegisterWebhook:
    def test_requires_https(self, mcp_server):
        result = mcp_server._get("register_webhook")(
            id="wh1", url="http://example.com/hook", events=["task.created"]
        )
        assert "Error" in result
        assert "HTTPS" in result

    def test_rejects_rfc1918_address(self, mcp_server):
        # Mock getaddrinfo to return a private IP
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("192.168.1.100", 443))]):
            result = mcp_server._get("register_webhook")(
                id="wh2", url="https://internal.example.com/hook", events=["task.created"]
            )
        assert "Error" in result
        assert "blocked" in result

    def test_rejects_loopback_address(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]):
            result = mcp_server._get("register_webhook")(
                id="wh3", url="https://localhost-ish.example.com/hook", events=["task.created"]
            )
        assert "Error" in result

    def test_rejects_172_16_block(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("172.20.0.1", 443))]):
            result = mcp_server._get("register_webhook")(
                id="wh4", url="https://internal2.example.com/hook", events=["task.created"]
            )
        assert "Error" in result

    def test_rejects_link_local(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("169.254.1.1", 443))]):
            result = mcp_server._get("register_webhook")(
                id="wh5", url="https://linklocal.example.com/hook", events=["task.created"]
            )
        assert "Error" in result

    def test_rejects_invalid_events(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            result = mcp_server._get("register_webhook")(
                id="wh6", url="https://example.com/hook", events=["task.invented"]
            )
        assert "Error" in result

    def test_rejects_empty_events(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            result = mcp_server._get("register_webhook")(
                id="wh7", url="https://example.com/hook", events=[]
            )
        assert "Error" in result

    def test_successful_registration(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            result = json.loads(mcp_server._get("register_webhook")(
                id="wh8", url="https://example.com/hook", events=["task.created", "task.updated"]
            ))
        assert result["id"] == "wh8"
        assert result["url"] == "https://example.com/hook"
        assert "task.created" in result["events"]

    def test_duplicate_id_returns_error(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            mcp_server._get("register_webhook")(
                id="wh9", url="https://example.com/hook", events=["task.created"]
            )
            result = mcp_server._get("register_webhook")(
                id="wh9", url="https://example.com/hook2", events=["task.updated"]
            )
        assert "Error" in result

    def test_requires_httpx(self, mcp_server):
        import sys
        # Temporarily hide httpx
        with patch.dict(sys.modules, {"httpx": None}):
            result = mcp_server._get("register_webhook")(
                id="wh_no_httpx", url="https://example.com/hook", events=["task.created"]
            )
        assert "Error" in result
        assert "httpx" in result.lower() or "webhooks" in result.lower()


class TestListWebhooks:
    def test_list_returns_registered_webhooks(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            mcp_server._get("register_webhook")(
                id="lw1", url="https://example.com/h1", events=["task.created"]
            )
        result = json.loads(mcp_server._get("list_webhooks")())
        ids = [w["id"] for w in result["webhooks"]]
        assert "lw1" in ids

    def test_list_does_not_return_secret(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            mcp_server._get("register_webhook")(
                id="lw2", url="https://example.com/h2", events=["task.created"], secret="mysecret"
            )
        result = json.loads(mcp_server._get("list_webhooks")())
        wh = next(w for w in result["webhooks"] if w["id"] == "lw2")
        assert "secret" not in wh

    def test_events_returned_as_list(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            mcp_server._get("register_webhook")(
                id="lw3", url="https://example.com/h3", events=["task.created", "task.deleted"]
            )
        result = json.loads(mcp_server._get("list_webhooks")())
        wh = next(w for w in result["webhooks"] if w["id"] == "lw3")
        assert isinstance(wh["events"], list)
        assert "task.created" in wh["events"]


class TestDeleteWebhook:
    def test_requires_human_approval(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            mcp_server._get("register_webhook")(
                id="dw1", url="https://example.com/h", events=["task.created"]
            )
        result = mcp_server._get("delete_webhook")(id="dw1")
        assert "Error" in result

    def test_deletes_with_approval(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            mcp_server._get("register_webhook")(
                id="dw2", url="https://example.com/h", events=["task.created"]
            )
        result = json.loads(mcp_server._get("delete_webhook")(id="dw2", human_approval=True))
        assert result["deleted"] is True

    def test_delete_not_found_returns_error(self, mcp_server):
        result = mcp_server._get("delete_webhook")(id="ghost_wh", human_approval=True)
        assert "Error" in result


# ---------------------------------------------------------------------------
# Bulk operations with activity log integration
# ---------------------------------------------------------------------------

class TestBulkActivityLog:
    def test_bulk_create_logs_created(self, mcp_server):
        mcp_server._get("create_tasks")(tasks=[{"id": "bal1", "title": "T"}])
        result = json.loads(mcp_server._get("get_task_activity")(task_id="bal1"))
        actions = [e["action"] for e in result["activity"]]
        assert "created" in actions

    def test_bulk_complete_logs_completed(self, mcp_server):
        mcp_server._get("create_task")(id="bal2", title="T")
        mcp_server._get("complete_tasks")(ids=["bal2"])
        result = json.loads(mcp_server._get("get_task_activity")(task_id="bal2"))
        actions = [e["action"] for e in result["activity"]]
        assert "completed" in actions

    def test_bulk_update_logs_updated(self, mcp_server):
        mcp_server._get("create_task")(id="bal3", title="Old")
        mcp_server._get("update_tasks")(updates=[{"task_id": "bal3", "title": "New"}])
        result = json.loads(mcp_server._get("get_task_activity")(task_id="bal3"))
        update_events = [e for e in result["activity"] if e["action"] == "updated"]
        assert any(e["field"] == "title" for e in update_events)


# ---------------------------------------------------------------------------
# GAP TESTS: bulk-operations edge cases
# ---------------------------------------------------------------------------

class TestBulkEmptyInputs:
    """Empty-list inputs must return success with zero results, not an error."""

    def test_create_tasks_empty_list(self, mcp_server):
        result = json.loads(mcp_server._get("create_tasks")(tasks=[]))
        assert result["created"] == []
        assert result["errors"] == []

    def test_update_tasks_empty_list(self, mcp_server):
        result = json.loads(mcp_server._get("update_tasks")(updates=[]))
        assert result["updated"] == []
        assert result["errors"] == []

    def test_complete_tasks_empty_list(self, mcp_server):
        result = json.loads(mcp_server._get("complete_tasks")(ids=[]))
        assert result["completed"] == []
        assert result["not_found"] == []


class TestBulkPartialFailures:
    """Per-item validation errors in bulk ops must be collected, not abort the batch."""

    def test_create_tasks_item_missing_id_collected_as_error(self, mcp_server):
        result = json.loads(mcp_server._get("create_tasks")(tasks=[
            {"title": "No ID here"},
            {"id": "bpf_ok", "title": "Has ID"},
        ]))
        assert "bpf_ok" in result["created"]
        assert any(e["error"] for e in result["errors"])

    def test_create_tasks_item_missing_title_collected_as_error(self, mcp_server):
        result = json.loads(mcp_server._get("create_tasks")(tasks=[
            {"id": "bpf_notitle"},
            {"id": "bpf_withtitle", "title": "Good"},
        ]))
        assert "bpf_withtitle" in result["created"]
        assert any(e["id"] == "bpf_notitle" for e in result["errors"])

    def test_update_tasks_item_missing_task_id_collected_as_error(self, mcp_server):
        result = json.loads(mcp_server._get("update_tasks")(updates=[
            {"title": "No task_id key"},
        ]))
        assert any(e["error"] for e in result["errors"])
        assert result["updated"] == []

    def test_update_tasks_item_no_fields_collected_as_error(self, mcp_server):
        mcp_server._get("create_task")(id="bpf_nofields", title="T")
        result = json.loads(mcp_server._get("update_tasks")(updates=[
            {"task_id": "bpf_nofields"},
        ]))
        assert any(e["id"] == "bpf_nofields" for e in result["errors"])

    def test_update_tasks_mixed_valid_and_invalid(self, mcp_server):
        mcp_server._get("create_task")(id="bpf_mix_good", title="T")
        result = json.loads(mcp_server._get("update_tasks")(updates=[
            {"task_id": "bpf_mix_good", "title": "Updated"},
            {"task_id": "bpf_mix_ghost", "title": "Ghost"},
        ]))
        assert "bpf_mix_good" in result["updated"]
        assert any(e["id"] == "bpf_mix_ghost" for e in result["errors"])


# ---------------------------------------------------------------------------
# GAP TESTS: FTS5 graceful degradation
# ---------------------------------------------------------------------------

class TestFTSGracefulDegradation:
    def test_search_returns_clear_error_when_fts_unavailable(self, tmp_path):
        """search_tasks must return a user-friendly error when FTS5 is not compiled in."""
        import sqlite3 as _sq

        class _NoFTSConn(_sq.Connection):
            """Subclass that refuses to create FTS5 virtual tables."""
            def executescript(self, sql: str):
                if "fts5" in sql or "tasks_fts" in sql:
                    raise _sq.OperationalError("no such module: fts5")
                return super().executescript(sql)

        orig_connect = _sq.connect

        def _no_fts_connect(*args, **kwargs):
            kwargs.pop("factory", None)
            return orig_connect(*args, factory=_NoFTSConn, **kwargs)

        with patch("sqlite3.connect", _no_fts_connect):
            from open_project_manager_mcp.server import create_server as _cs_nofts
            server = _cs_nofts(str(tmp_path / "nofts.db"))
            server._get = lambda name: _sync_wrap(_get_tool(server, name))

        result = server._get("search_tasks")(query="anything")
        assert "Error" in result
        assert "full-text search" in result.lower() or "fts" in result.lower()


# ---------------------------------------------------------------------------
# GAP TESTS: due-date edge cases
# ---------------------------------------------------------------------------

class TestDueDateEdgeCases:
    def test_due_date_null_string_returns_error(self, mcp_server):
        """The string 'null' is not a valid date."""
        result = mcp_server._get("create_task")(id="dde_null", title="T", due_date="null")
        assert "Error" in result

    def test_due_date_iso_with_time_stored_as_given(self, mcp_server):
        """Full ISO datetime is accepted and stored verbatim."""
        mcp_server._get("create_task")(id="dde_iso", title="T", due_date="2099-06-01T00:00:00+00:00")
        task = json.loads(mcp_server._get("get_task")(task_id="dde_iso"))
        assert task["due_date"] == "2099-06-01T00:00:00+00:00"

    def test_overdue_sorted_priority_before_date(self, mcp_server):
        """Overdue list: critical priority task precedes low priority regardless of date."""
        mcp_server._get("create_task")(id="dde_low", title="Low", priority="low", due_date="2000-01-01")
        mcp_server._get("create_task")(id="dde_crit", title="Critical", priority="critical", due_date="2000-01-10")
        result = json.loads(mcp_server._get("list_overdue_tasks")())
        ids = [t["id"] for t in result["tasks"]]
        assert "dde_crit" in ids and "dde_low" in ids
        assert ids.index("dde_crit") < ids.index("dde_low")

    def test_list_due_soon_sorted_priority_before_date(self, mcp_server):
        """Due-soon list: critical task precedes low task."""
        in_3 = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
        in_5 = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
        mcp_server._get("create_task")(id="dds_low", title="Low", priority="low", due_date=in_3)
        mcp_server._get("create_task")(id="dds_crit", title="Critical", priority="critical", due_date=in_5)
        result = json.loads(mcp_server._get("list_due_soon_tasks")(days=7))
        ids = [t["id"] for t in result["tasks"]]
        assert "dds_crit" in ids and "dds_low" in ids
        assert ids.index("dds_crit") < ids.index("dds_low")

    def test_update_task_does_not_change_due_date_when_not_provided(self, mcp_server):
        """Omitting due_date on update_task must leave the existing value intact."""
        mcp_server._get("create_task")(id="dde_preserve", title="T", due_date="2099-03-01")
        mcp_server._get("update_task")(task_id="dde_preserve", title="Renamed")
        task = json.loads(mcp_server._get("get_task")(task_id="dde_preserve"))
        assert task["due_date"] == "2099-03-01"


# ---------------------------------------------------------------------------
# GAP TESTS: webhook SSRF blocklist completeness
# ---------------------------------------------------------------------------

class TestWebhookSSRFAdditional:
    def test_rejects_10_x_block(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.0.1", 443))]):
            result = mcp_server._get("register_webhook")(
                id="wh_10x", url="https://corp.example.com/hook", events=["task.created"]
            )
        assert "Error" in result
        assert "blocked" in result

    def test_rejects_ipv6_loopback(self, mcp_server):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("::1", 443))]):
            result = mcp_server._get("register_webhook")(
                id="wh_ipv6lo", url="https://v6lo.example.com/hook", events=["task.created"]
            )
        assert "Error" in result
        assert "blocked" in result

    def test_http_url_rejected_before_dns(self, mcp_server):
        """HTTP (not HTTPS) must be rejected immediately without DNS resolution."""
        called = []
        with patch("socket.getaddrinfo", side_effect=lambda *a, **k: called.append(a) or []):
            result = mcp_server._get("register_webhook")(
                id="wh_http2", url="http://public.example.com/hook", events=["task.created"]
            )
        assert "Error" in result
        assert "HTTPS" in result
        assert called == [], "DNS must not be queried for non-HTTPS URLs"


# ---------------------------------------------------------------------------
# GAP TESTS: REST API additional coverage
# ---------------------------------------------------------------------------

class TestRestApiAdditionalFilters:
    @pytest.fixture()
    def client(self, tmp_path):
        pytest.importorskip("httpx")
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient
        from open_project_manager_mcp.server import create_server

        server = create_server(str(tmp_path / "rest_extra.db"), enable_rest=True)
        app = Starlette(routes=[Mount("/api/v1", app=server._rest_router)])
        return TestClient(app, raise_server_exceptions=True)

    def test_filter_by_assignee(self, client):
        client.post("/api/v1/tasks", json={"id": "rfa1", "title": "T", "assignee": "alice"})
        client.post("/api/v1/tasks", json={"id": "rfa2", "title": "T", "assignee": "bob"})
        r = client.get("/api/v1/tasks?assignee=alice")
        assert r.status_code == 200
        ids = [t["id"] for t in r.json()["tasks"]]
        assert "rfa1" in ids
        assert "rfa2" not in ids

    def test_filter_by_status(self, client):
        client.post("/api/v1/tasks", json={"id": "rfs1", "title": "T"})
        client.post("/api/v1/tasks", json={"id": "rfs2", "title": "T"})
        client.patch("/api/v1/tasks/rfs2", json={"status": "in_progress"})
        r = client.get("/api/v1/tasks?status=pending")
        assert r.status_code == 200
        ids = [t["id"] for t in r.json()["tasks"]]
        assert "rfs1" in ids
        assert "rfs2" not in ids

    def test_filter_by_priority(self, client):
        client.post("/api/v1/tasks", json={"id": "rfp_h", "title": "T", "priority": "high"})
        client.post("/api/v1/tasks", json={"id": "rfp_l", "title": "T", "priority": "low"})
        r = client.get("/api/v1/tasks?priority=high")
        assert r.status_code == 200
        ids = [t["id"] for t in r.json()["tasks"]]
        assert "rfp_h" in ids
        assert "rfp_l" not in ids

    def test_invalid_limit_returns_400(self, client):
        r = client.get("/api/v1/tasks?limit=notanumber")
        assert r.status_code == 400

    def test_patch_invalid_priority_returns_400(self, client):
        client.post("/api/v1/tasks", json={"id": "rpiv1", "title": "T"})
        r = client.patch("/api/v1/tasks/rpiv1", json={"priority": "ultra"})
        assert r.status_code == 400

    def test_patch_no_fields_returns_400(self, client):
        client.post("/api/v1/tasks", json={"id": "rpnf1", "title": "T"})
        r = client.patch("/api/v1/tasks/rpnf1", json={})
        assert r.status_code == 400

    def test_post_task_with_due_date_returns_201(self, client):
        r = client.post("/api/v1/tasks", json={"id": "rdd_rest1", "title": "T", "due_date": "2099-12-31"})
        assert r.status_code == 201

    def test_post_task_with_invalid_due_date_returns_400(self, client):
        r = client.post("/api/v1/tasks", json={"id": "rdd_rest2", "title": "T", "due_date": "not-a-date"})
        assert r.status_code == 400

    def test_get_task_includes_depends_on(self, client):
        client.post("/api/v1/tasks", json={"id": "rdep_a", "title": "A"})
        client.post("/api/v1/tasks", json={"id": "rdep_b", "title": "B"})
        # Use MCP tool for dep since REST has no dep endpoint
        pytest.importorskip("httpx")
        from open_project_manager_mcp.server import create_server as _cs
        # The client fixture already has tasks; just verify the field is present on GET
        r = client.get("/api/v1/tasks/rdep_a")
        assert r.status_code == 200
        assert "depends_on" in r.json()
        assert "blocked_by" in r.json()

    def test_offset_pagination(self, client):
        for i in range(4):
            client.post("/api/v1/tasks", json={"id": f"rpg2_{i}", "title": f"T{i}"})
        r1 = client.get("/api/v1/tasks?limit=2&offset=0")
        r2 = client.get("/api/v1/tasks?limit=2&offset=2")
        assert r1.status_code == 200
        assert r2.status_code == 200
        ids1 = {t["id"] for t in r1.json()["tasks"]}
        ids2 = {t["id"] for t in r2.json()["tasks"]}
        assert ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# GAP TESTS: export/import fidelity
# ---------------------------------------------------------------------------

class TestImportExportGaps:
    def _make_export(self, tasks, deps=None):
        return json.dumps({
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tasks": tasks,
            "deps": deps or [],
        })

    def test_export_empty_database(self, mcp_server):
        result = json.loads(mcp_server._get("export_all_tasks")())
        assert result["tasks"] == []
        assert result["deps"] == []
        assert result["version"] == "1.0"

    def test_roundtrip_preserves_deps(self, mcp_server, tmp_path):
        """Full export→import round-trip must preserve dependency edges."""
        mcp_server._get("create_task")(id="rt_a", title="A")
        mcp_server._get("create_task")(id="rt_b", title="B")
        mcp_server._get("add_dependency")(task_id="rt_b", depends_on_id="rt_a")
        exported = mcp_server._get("export_all_tasks")()

        from open_project_manager_mcp.server import create_server as _cs2
        server2 = _cs2(str(tmp_path / "roundtrip.db"))
        server2._get = lambda name: _sync_wrap(_get_tool(server2, name))
        result = json.loads(server2._get("import_tasks")(data=exported))
        assert result["imported"] == 2
        task_b = json.loads(server2._get("get_task")(task_id="rt_b"))
        assert "rt_a" in task_b["depends_on"]

    def test_import_dep_with_nonexistent_depends_on_silently_ignored(self, mcp_server):
        """A dep whose depends_on task doesn't exist should not crash the import."""
        data = self._make_export(
            [{"id": "imp_ghost_dep", "title": "Real Task"}],
            deps=[{"task_id": "imp_ghost_dep", "depends_on": "nonexistent_xyz_task"}],
        )
        result = json.loads(mcp_server._get("import_tasks")(data=data))
        assert result["imported"] == 1
        # The task itself should be importable
        task = json.loads(mcp_server._get("get_task")(task_id="imp_ghost_dep"))
        assert task["id"] == "imp_ghost_dep"

    def test_roundtrip_preserves_status(self, mcp_server):
        mcp_server._get("create_task")(id="rt_status", title="T", priority="high")
        mcp_server._get("complete_task")(task_id="rt_status")
        exported = mcp_server._get("export_all_tasks")()
        doc = json.loads(exported)
        task = next(t for t in doc["tasks"] if t["id"] == "rt_status")
        assert task["status"] == "done"
        assert task["priority"] == "high"

    def test_import_wrong_version_accepted_or_errored_consistently(self, mcp_server):
        """Import with a different (but present) version field should at least not crash."""
        data = json.dumps({
            "version": "99.0",
            "exported_at": "2099-01-01T00:00:00+00:00",
            "tasks": [{"id": "imp_ver99", "title": "T"}],
            "deps": [],
        })
        result = mcp_server._get("import_tasks")(data=data)
        # Either succeeds (server is lenient about version) or returns a clean Error
        assert isinstance(result, str)
        parsed = json.loads(result) if not result.startswith("Error") else {"error": result}
        if "error" in parsed:
            assert "Error" in parsed["error"]
        else:
            assert "imported" in parsed

    def test_import_tasks_list_not_list_returns_error(self, mcp_server):
        data = json.dumps({"version": "1.0", "tasks": "not-a-list"})
        result = mcp_server._get("import_tasks")(data=data)
        assert "Error" in result

    def test_import_task_missing_id_returns_error(self, mcp_server):
        data = json.dumps({
            "version": "1.0",
            "exported_at": "2099-01-01T00:00:00+00:00",
            "tasks": [{"title": "No ID"}],
            "deps": [],
        })
        result = mcp_server._get("import_tasks")(data=data)
        assert "Error" in result

    def test_import_task_missing_title_returns_error(self, mcp_server):
        data = json.dumps({
            "version": "1.0",
            "exported_at": "2099-01-01T00:00:00+00:00",
            "tasks": [{"id": "notitle_imp"}],
            "deps": [],
        })
        result = mcp_server._get("import_tasks")(data=data)
        assert "Error" in result

    def test_export_project_scoped_includes_only_project_tasks(self, mcp_server):
        mcp_server._get("create_task")(id="ep_a", title="A", project="scope_test")
        mcp_server._get("create_task")(id="ep_b", title="B", project="other_project")
        result = json.loads(mcp_server._get("export_all_tasks")(project="scope_test"))
        ids = [t["id"] for t in result["tasks"]]
        assert "ep_a" in ids
        assert "ep_b" not in ids
