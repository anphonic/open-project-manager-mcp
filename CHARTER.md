# open-project-manager-mcp

## Project Scope & Charter

**Status:** Pre-development — scoping phase  
**Origin:** Identified during squad-knowledge-mcp development (session 2026-03-31)

---

## Problem Statement

AI agent squads (and their human coordinators) need a lightweight, persistent, prioritized work queue that:

- Survives session boundaries — work items don't vanish when context resets
- Is readable by any agent in any session with minimal token cost
- Supports priority ordering, status transitions, and dependency tracking
- Doesn't require GitHub or any external service to function
- Returns small, focused payloads (not fat API JSON)

GitHub Issues solves some of this, but carries real costs: network latency on every query, fat JSON responses, API rate limits, and a hard dependency on GitHub being the project's home. Ralph's work-check cycle runs 4 parallel queries per round — in long sessions this accumulates significant token and latency overhead.

The squad-knowledge-mcp server (squad knowledge + agent memory + group Q&A board) is intentionally kept pure. Project management has different data access patterns (frequent writes, ordering, status transitions) that don't fit ChromaDB's vector-store model.

---

## Proposed Solution

A standalone MCP server backed by **SQLite** providing a minimal, fast project management API for AI agent squads.

### Core Design Principles

1. **Tiny payloads** — return only what agents need (`id, title, priority, status`). No fat JSON.
2. **Local-first** — runs on the same host as the agent. Zero network latency, no rate limits.
3. **Agent-native** — tools are designed for LLM consumption, not human UI.
4. **Persistent across sessions** — SQLite file survives restarts and context resets.
5. **Composable** — works alongside squad-knowledge-mcp and GitHub Issues, not instead of them.
6. **Transport-compatible** — stdio (default) + HTTP/TCP for multi-agent access, matching squad-knowledge-mcp's proven pattern.

---

## Scope

### In Scope (v1)

**Work items:**
- `create_task(title, description, priority, project, tags, assignee)` -> task_id
- `update_task(task_id, ...)` — update any field
- `complete_task(task_id)` — mark done
- `delete_task(task_id, human_approval=True)` — safeguarded delete (mirrors squad-knowledge-mcp pattern)
- `get_task(task_id)` -> single task
- `list_tasks(project, assignee, status, priority, limit, offset)` -> paginated list with `has_more`

**Prioritization:**
- Priority levels: `critical`, `high`, `medium`, `low`
- Explicit ordering within priority levels
- `list_tasks` default sort: priority desc, created_at asc

**Dependencies:**
- `add_dependency(task_id, depends_on_id)` — task A blocks until task B is done
- `remove_dependency(task_id, depends_on_id)`
- `list_ready_tasks(project, assignee)` — tasks with no unresolved dependencies

**Projects:**
- `list_projects()` — distinct project names + open task counts
- Tasks are scoped to a project string (no separate project entity needed)

**Stats:**
- `get_stats()` — task counts by status/priority, oldest open item age
- `GET /stats` HTTP endpoint (same pattern as squad-knowledge-mcp)

### Out of Scope (v1)

- Time tracking / due dates / sprints (could be v2)
- Comments / discussion threads (use squad-knowledge-mcp group board for that)
- GitHub Issues sync (separate integration concern)
- UI / web dashboard
- Multi-user auth (single-tenant v1, same as squad-knowledge-mcp default)
- Semantic/vector search (wrong tool — use squad-knowledge-mcp for semantic queries)

---

## Architecture

```
open-project-manager-mcp/
├── src/open_project_manager_mcp/
│   ├── __main__.py      # CLI entry, transport config (mirrors squad-knowledge-mcp pattern)
│   └── server.py        # create_server(db_path) factory, all tools as closures
├── tests/
│   ├── test_tools.py
│   └── test_config.py
└── pyproject.toml
```

**Backend:** SQLite via Python `sqlite3` stdlib — no ORM, no external DB dependency.
**Framework:** FastMCP (same as squad-knowledge-mcp).
**Transport:** stdio default + `--http`/`--tcp` flags.
**Schema:** Two tables — `tasks` and `task_deps`. Migrations handled at startup.

**Key schema:**
```sql
CREATE TABLE tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    project     TEXT NOT NULL DEFAULT 'default',
    priority    TEXT NOT NULL DEFAULT 'medium',
    status      TEXT NOT NULL DEFAULT 'pending',
    assignee    TEXT,
    tags        TEXT,
    sort_order  INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE task_deps (
    task_id     TEXT NOT NULL,
    depends_on  TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on)
);
```

Priority values: critical/high/medium/low
Status values: pending/in_progress/done/blocked
tags: JSON array string (mirrors squad-knowledge-mcp convention)

---

## Relationship to squad-knowledge-mcp

| Concern | Tool |
|---------|------|
| What did we decide? | squad-knowledge-mcp -> search_squad_knowledge |
| What does agent X know? | squad-knowledge-mcp -> get_agent_knowledge |
| Who has a question on the board? | squad-knowledge-mcp -> list_open_questions |
| What should I work on next? | open-project-manager-mcp -> list_ready_tasks |
| What is blocking this task? | open-project-manager-mcp -> get_task (deps) |
| Show me all open critical tasks | open-project-manager-mcp -> list_tasks(priority=critical) |

They connect at one seam: when an agent posts a decision or answer to the knowledge board, it may also create a follow-up task here. That integration is by convention (agent behavior), not by coupling the servers.

---

## Why Not GitHub Issues?

|                        | GitHub Issues                  | open-project-manager-mcp |
|------------------------|-------------------------------|--------------------------|
| Payload size           | ~2-5KB per issue (full JSON)   | ~100 bytes per task      |
| Latency                | Network + GitHub API           | Local disk (~1ms)        |
| Rate limits            | Yes (5000 req/hr)              | None                     |
| External dependencies  | GitHub account, internet       | None                     |
| Ordering/priority      | Labels (approximate)           | First-class fields       |
| Ready-task query       | Not possible                   | list_ready_tasks         |
| Session cost (Ralph 10 rounds) | ~200-500KB context    | ~10-20KB context         |

GitHub Issues remains valuable for public-facing project tracking and @copilot integration. This server is the agent internal work queue — fast, cheap, and always available.

---

## Non-Goals

- Replacing GitHub Issues for human-facing project management
- Adding semantic search (not the right tool)
- Building a UI
- Becoming a general-purpose task manager (keep it agent-native)

---

## Success Criteria

- Ralph can replace its 4-query GitHub scan with 1-2 calls to this server
- Agents can query their work queue in <5ms with <200 token response
- Work items survive session resets, machine restarts
- Any squad can adopt it by adding one MCP config entry
- Zero external dependencies beyond Python stdlib + FastMCP

---

## Open Questions

1. **Multi-squad isolation**: Should tasks be tenant-scoped (like squad-knowledge-mcp SQUAD_TENANT_KEYS)? Or is project-scoping sufficient for v1?
2. **GitHub sync**: Should there be a one-way sync mode that mirrors GitHub Issues into this server for offline/fast Ralph access? Separate tool or built-in?
3. **Squad integration**: Should Ralph check this server first via list_ready_tasks, then GitHub Issues as fallback? Or treat them as separate queues?
4. **Hosting**: Co-located with squad-knowledge-mcp on skitterphuger, or separate deploy per project?

---

*Charter created: 2026-03-31*
*Based on squad-knowledge-mcp architecture decisions and cross-squad field experience*
