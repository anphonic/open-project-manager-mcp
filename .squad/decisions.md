# Decisions

## 2026-03-31: Project bootstrapped

**Decision:** Build open-project-manager-mcp as a standalone SQLite-backed FastMCP server.
**Rationale:** squad-knowledge-mcp uses ChromaDB (wrong fit for ordered mutable task state). SQLite is the right tool for a task queue.
**Patterns to follow:** Mirror squad-knowledge-mcp's `create_server(db_path)` factory pattern, closure-based tools, stdio+TCP transport.

## 2026-03-31: Caller-supplied task IDs

**Decision:** Task IDs are caller-supplied strings (e.g., "auth-login-ui"), not auto-generated UUIDs.
**Rationale:** Agent-friendly — meaningful IDs are easier to reference in tool calls than opaque UUIDs.
