"""Tests for all 11 MCP tools in server.py (real SQLite via tmp_path)."""

import asyncio
import functools
import json

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mcp_server(tmp_path):
    """Spin up create_server() with a fresh SQLite database."""
    from open_project_manager_mcp.server import create_server
    server = create_server(str(tmp_path / "tasks.db"))
    server._get = lambda name: _sync_wrap(_get_tool(server, name))
    return server


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

class TestCreateTask:
    def test_creates_task_returns_json(self, mcp_server):
        result = json.loads(mcp_server._get("create_task")(id="t1", title="First task"))
        assert result["id"] == "t1"
        assert result["status"] == "pending"
        assert result["priority"] == "medium"
        assert result["project"] == "default"

    def test_custom_priority_and_project(self, mcp_server):
        result = json.loads(mcp_server._get("create_task")(id="t2", title="High", priority="high", project="myapp"))
        assert result["priority"] == "high"
        assert result["project"] == "myapp"

    def test_duplicate_id_returns_error(self, mcp_server):
        create = mcp_server._get("create_task")
        create(id="dup", title="First")
        result = create(id="dup", title="Second")
        assert "already exists" in result

    def test_invalid_priority_returns_error(self, mcp_server):
        result = mcp_server._get("create_task")(id="t3", title="Bad", priority="urgent")
        assert "Error" in result

    def test_tags_round_trip(self, mcp_server):
        mcp_server._get("create_task")(id="t4", title="Tagged", tags=["alpha", "beta"])
        task = json.loads(mcp_server._get("get_task")(task_id="t4"))
        assert task["tags"] == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------

class TestUpdateTask:
    def test_updates_title(self, mcp_server):
        mcp_server._get("create_task")(id="u1", title="Old")
        mcp_server._get("update_task")(task_id="u1", title="New")
        task = json.loads(mcp_server._get("get_task")(task_id="u1"))
        assert task["title"] == "New"

    def test_updates_status(self, mcp_server):
        mcp_server._get("create_task")(id="u2", title="Task")
        mcp_server._get("update_task")(task_id="u2", status="in_progress")
        task = json.loads(mcp_server._get("get_task")(task_id="u2"))
        assert task["status"] == "in_progress"

    def test_no_fields_returns_error(self, mcp_server):
        mcp_server._get("create_task")(id="u3", title="Task")
        result = mcp_server._get("update_task")(task_id="u3")
        assert "Error" in result

    def test_missing_task_returns_error(self, mcp_server):
        result = mcp_server._get("update_task")(task_id="nope", title="x")
        assert "Error" in result

    def test_invalid_priority_returns_error(self, mcp_server):
        mcp_server._get("create_task")(id="u4", title="Task")
        result = mcp_server._get("update_task")(task_id="u4", priority="super")
        assert "Error" in result

    def test_invalid_status_returns_error(self, mcp_server):
        mcp_server._get("create_task")(id="u5", title="Task")
        result = mcp_server._get("update_task")(task_id="u5", status="flying")
        assert "Error" in result

    def test_updates_tags(self, mcp_server):
        mcp_server._get("create_task")(id="u6", title="Task")
        mcp_server._get("update_task")(task_id="u6", tags=["x", "y"])
        task = json.loads(mcp_server._get("get_task")(task_id="u6"))
        assert task["tags"] == ["x", "y"]

    def test_updates_assignee(self, mcp_server):
        mcp_server._get("create_task")(id="u7", title="Task")
        mcp_server._get("update_task")(task_id="u7", assignee="bob")
        task = json.loads(mcp_server._get("get_task")(task_id="u7"))
        assert task["assignee"] == "bob"


# ---------------------------------------------------------------------------
# complete_task
# ---------------------------------------------------------------------------

class TestCompleteTask:
    def test_marks_done(self, mcp_server):
        mcp_server._get("create_task")(id="c1", title="Task")
        result = json.loads(mcp_server._get("complete_task")(task_id="c1"))
        assert result["status"] == "done"
        assert json.loads(mcp_server._get("get_task")(task_id="c1"))["status"] == "done"

    def test_missing_task_returns_error(self, mcp_server):
        result = mcp_server._get("complete_task")(task_id="ghost")
        assert "Error" in result


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------

class TestDeleteTask:
    def test_requires_human_approval(self, mcp_server):
        mcp_server._get("create_task")(id="d1", title="Task")
        result = mcp_server._get("delete_task")(task_id="d1")
        assert "Error" in result

    def test_deletes_with_approval(self, mcp_server):
        mcp_server._get("create_task")(id="d2", title="Task")
        result = json.loads(mcp_server._get("delete_task")(task_id="d2", human_approval=True))
        assert result["deleted"] is True
        assert "Error" in mcp_server._get("get_task")(task_id="d2")

    def test_missing_task_returns_error(self, mcp_server):
        result = mcp_server._get("delete_task")(task_id="ghost", human_approval=True)
        assert "Error" in result

    def test_cleans_up_dependency_edges(self, mcp_server):
        mcp_server._get("create_task")(id="parent", title="Parent")
        mcp_server._get("create_task")(id="child", title="Child")
        mcp_server._get("add_dependency")(task_id="child", depends_on_id="parent")
        mcp_server._get("delete_task")(task_id="parent", human_approval=True)
        # child should now be unblocked (dependency edge removed)
        ids = [t["id"] for t in json.loads(mcp_server._get("list_ready_tasks")())["tasks"]]
        assert "child" in ids


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------

class TestGetTask:
    def test_returns_all_fields(self, mcp_server):
        mcp_server._get("create_task")(id="g1", title="My task", description="desc", project="proj", assignee="alice")
        task = json.loads(mcp_server._get("get_task")(task_id="g1"))
        assert task["id"] == "g1"
        assert task["title"] == "My task"
        assert task["description"] == "desc"
        assert task["project"] == "proj"
        assert task["assignee"] == "alice"

    def test_includes_depends_on_and_blocked_by(self, mcp_server):
        mcp_server._get("create_task")(id="blocker", title="Blocker")
        mcp_server._get("create_task")(id="blocked", title="Blocked")
        mcp_server._get("add_dependency")(task_id="blocked", depends_on_id="blocker")
        task = json.loads(mcp_server._get("get_task")(task_id="blocked"))
        assert "blocker" in task["depends_on"]
        assert "blocker" in task["blocked_by"]

    def test_blocked_by_clears_when_dep_done(self, mcp_server):
        mcp_server._get("create_task")(id="prereq", title="Prereq")
        mcp_server._get("create_task")(id="next", title="Next")
        mcp_server._get("add_dependency")(task_id="next", depends_on_id="prereq")
        mcp_server._get("complete_task")(task_id="prereq")
        task = json.loads(mcp_server._get("get_task")(task_id="next"))
        assert task["blocked_by"] == []

    def test_missing_task_returns_error(self, mcp_server):
        assert "Error" in mcp_server._get("get_task")(task_id="nope")


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------

class TestListTasks:
    def test_returns_all_tasks(self, mcp_server):
        mcp_server._get("create_task")(id="l1", title="A")
        mcp_server._get("create_task")(id="l2", title="B")
        result = json.loads(mcp_server._get("list_tasks")())
        assert len(result["tasks"]) == 2

    def test_filter_by_project(self, mcp_server):
        mcp_server._get("create_task")(id="l3", title="X", project="alpha")
        mcp_server._get("create_task")(id="l4", title="Y", project="beta")
        result = json.loads(mcp_server._get("list_tasks")(project="alpha"))
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["id"] == "l3"

    def test_filter_by_status(self, mcp_server):
        mcp_server._get("create_task")(id="l5", title="A")
        mcp_server._get("create_task")(id="l6", title="B")
        mcp_server._get("update_task")(task_id="l5", status="in_progress")
        result = json.loads(mcp_server._get("list_tasks")(status="in_progress"))
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["id"] == "l5"

    def test_priority_sort_order(self, mcp_server):
        mcp_server._get("create_task")(id="low", title="Low", priority="low")
        mcp_server._get("create_task")(id="crit", title="Critical", priority="critical")
        mcp_server._get("create_task")(id="med", title="Medium", priority="medium")
        ids = [t["id"] for t in json.loads(mcp_server._get("list_tasks")())["tasks"]]
        assert ids.index("crit") < ids.index("med") < ids.index("low")

    def test_pagination_has_more(self, mcp_server):
        for i in range(5):
            mcp_server._get("create_task")(id=f"p{i}", title=f"Task {i}")
        result = json.loads(mcp_server._get("list_tasks")(limit=3))
        assert result["has_more"] is True
        assert len(result["tasks"]) == 3

    def test_pagination_no_more(self, mcp_server):
        for i in range(3):
            mcp_server._get("create_task")(id=f"q{i}", title=f"Task {i}")
        result = json.loads(mcp_server._get("list_tasks")(limit=10))
        assert result["has_more"] is False

    def test_filter_by_assignee(self, mcp_server):
        mcp_server._get("create_task")(id="la1", title="Assigned", assignee="alice")
        mcp_server._get("create_task")(id="la2", title="Unassigned")
        result = json.loads(mcp_server._get("list_tasks")(assignee="alice"))
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["assignee"] == "alice"

    def test_filter_by_priority(self, mcp_server):
        mcp_server._get("create_task")(id="lp1", title="Critical", priority="critical")
        mcp_server._get("create_task")(id="lp2", title="Low", priority="low")
        result = json.loads(mcp_server._get("list_tasks")(priority="critical"))
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["priority"] == "critical"


# ---------------------------------------------------------------------------
# add_dependency / remove_dependency
# ---------------------------------------------------------------------------

class TestDependencies:
    def test_add_dependency(self, mcp_server):
        mcp_server._get("create_task")(id="a", title="A")
        mcp_server._get("create_task")(id="b", title="B")
        mcp_server._get("add_dependency")(task_id="b", depends_on_id="a")
        task = json.loads(mcp_server._get("get_task")(task_id="b"))
        assert "a" in task["depends_on"]

    def test_self_dependency_returns_error(self, mcp_server):
        mcp_server._get("create_task")(id="self", title="Self")
        result = mcp_server._get("add_dependency")(task_id="self", depends_on_id="self")
        assert "Error" in result

    def test_missing_task_returns_error(self, mcp_server):
        mcp_server._get("create_task")(id="exists", title="Exists")
        result = mcp_server._get("add_dependency")(task_id="exists", depends_on_id="ghost")
        assert "Error" in result

    def test_first_task_missing_returns_error(self, mcp_server):
        mcp_server._get("create_task")(id="dep_exists", title="Exists")
        result = mcp_server._get("add_dependency")(task_id="dep_ghost", depends_on_id="dep_exists")
        assert "Error" in result

    def test_duplicate_dependency_reports_exists(self, mcp_server):
        mcp_server._get("create_task")(id="x", title="X")
        mcp_server._get("create_task")(id="y", title="Y")
        mcp_server._get("add_dependency")(task_id="y", depends_on_id="x")
        result = mcp_server._get("add_dependency")(task_id="y", depends_on_id="x")
        assert "already exists" in result

    def test_remove_dependency(self, mcp_server):
        mcp_server._get("create_task")(id="m", title="M")
        mcp_server._get("create_task")(id="n", title="N")
        mcp_server._get("add_dependency")(task_id="n", depends_on_id="m")
        mcp_server._get("remove_dependency")(task_id="n", depends_on_id="m")
        task = json.loads(mcp_server._get("get_task")(task_id="n"))
        assert "m" not in task["depends_on"]

    def test_remove_nonexistent_returns_error(self, mcp_server):
        result = mcp_server._get("remove_dependency")(task_id="x", depends_on_id="y")
        assert "Error" in result


# ---------------------------------------------------------------------------
# list_ready_tasks
# ---------------------------------------------------------------------------

class TestListReadyTasks:
    def test_task_with_no_deps_is_ready(self, mcp_server):
        mcp_server._get("create_task")(id="r1", title="Ready")
        ids = [t["id"] for t in json.loads(mcp_server._get("list_ready_tasks")())["tasks"]]
        assert "r1" in ids

    def test_task_with_unfinished_dep_is_not_ready(self, mcp_server):
        mcp_server._get("create_task")(id="blocker2", title="Blocker")
        mcp_server._get("create_task")(id="blocked2", title="Blocked")
        mcp_server._get("add_dependency")(task_id="blocked2", depends_on_id="blocker2")
        ids = [t["id"] for t in json.loads(mcp_server._get("list_ready_tasks")())["tasks"]]
        assert "blocked2" not in ids

    def test_task_becomes_ready_after_dep_completed(self, mcp_server):
        mcp_server._get("create_task")(id="prereq2", title="Prereq")
        mcp_server._get("create_task")(id="next2", title="Next")
        mcp_server._get("add_dependency")(task_id="next2", depends_on_id="prereq2")
        mcp_server._get("complete_task")(task_id="prereq2")
        ids = [t["id"] for t in json.loads(mcp_server._get("list_ready_tasks")())["tasks"]]
        assert "next2" in ids

    def test_done_tasks_excluded(self, mcp_server):
        mcp_server._get("create_task")(id="done1", title="Done")
        mcp_server._get("complete_task")(task_id="done1")
        ids = [t["id"] for t in json.loads(mcp_server._get("list_ready_tasks")())["tasks"]]
        assert "done1" not in ids

    def test_filter_by_project(self, mcp_server):
        mcp_server._get("create_task")(id="ra", title="A", project="alpha")
        mcp_server._get("create_task")(id="rb", title="B", project="beta")
        ids = [t["id"] for t in json.loads(mcp_server._get("list_ready_tasks")(project="alpha"))["tasks"]]
        assert "ra" in ids
        assert "rb" not in ids

    def test_filter_by_assignee(self, mcp_server):
        mcp_server._get("create_task")(id="rac", title="Alice task", assignee="alice")
        mcp_server._get("create_task")(id="rbc", title="Bob task", assignee="bob")
        ids = [t["id"] for t in json.loads(mcp_server._get("list_ready_tasks")(assignee="alice"))["tasks"]]
        assert "rac" in ids
        assert "rbc" not in ids


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------

class TestListProjects:
    def test_lists_all_projects(self, mcp_server):
        mcp_server._get("create_task")(id="p1", title="A", project="alpha")
        mcp_server._get("create_task")(id="p2", title="B", project="beta")
        names = [p["project"] for p in json.loads(mcp_server._get("list_projects")())["projects"]]
        assert "alpha" in names
        assert "beta" in names

    def test_open_count_excludes_done(self, mcp_server):
        mcp_server._get("create_task")(id="pa", title="A", project="gamma")
        mcp_server._get("create_task")(id="pb", title="B", project="gamma")
        mcp_server._get("complete_task")(task_id="pa")
        projects = json.loads(mcp_server._get("list_projects")())["projects"]
        gamma = next(p for p in projects if p["project"] == "gamma")
        assert gamma["open"] == 1
        assert gamma["total"] == 2


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_counts_by_status(self, mcp_server):
        mcp_server._get("create_task")(id="s1", title="A")
        mcp_server._get("create_task")(id="s2", title="B")
        mcp_server._get("complete_task")(task_id="s1")
        result = json.loads(mcp_server._get("get_stats")())
        assert result["by_status"].get("pending", 0) >= 1
        assert result["by_status"].get("done", 0) >= 1

    def test_counts_by_priority(self, mcp_server):
        mcp_server._get("create_task")(id="s3", title="A", priority="critical")
        mcp_server._get("create_task")(id="s4", title="B", priority="low")
        result = json.loads(mcp_server._get("get_stats")())
        assert "critical" in result["by_priority"]
        assert "low" in result["by_priority"]

    def test_done_excluded_from_by_priority(self, mcp_server):
        mcp_server._get("create_task")(id="s5", title="Done crit", priority="critical")
        mcp_server._get("complete_task")(task_id="s5")
        result = json.loads(mcp_server._get("get_stats")())
        assert result["by_priority"].get("critical", 0) == 0

    def test_empty_db_returns_null_oldest(self, mcp_server):
        result = json.loads(mcp_server._get("get_stats")())
        assert result["oldest_open"] is None

    def test_oldest_open_has_value(self, mcp_server):
        mcp_server._get("create_task")(id="s6", title="Open task")
        result = json.loads(mcp_server._get("get_stats")())
        assert result["oldest_open"] is not None
