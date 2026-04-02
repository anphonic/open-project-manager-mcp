# Quickstart — Connect in 5 Minutes

Get your squad connected to OPM and run your first task.

## Prerequisites

- OPM server running (e.g., `http://192.168.1.178:8765`)
- Bearer token for your squad (ask your admin or self-register)
- MCP client configured (Claude Desktop, Codebase Agent, etc.)

## Step 1 — Get Your Bearer Token

### Option A: Ask the Admin

Contact your OPM admin and ask for a token for your squad.

### Option B: Self-Service Registration

If `OPM_REGISTRATION_KEY` is configured:

```bash
curl -X POST http://192.168.1.178:8765/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{"squad": "my-squad", "registration_key": "<admin-provided-secret>"}'
```

Response:
```json
{"squad": "my-squad", "token": "opm_abc123def456..."}
```

Save your token — it won't be shown again.

## Step 2 — Set OPM_BEARER_TOKEN Environment Variable

### Windows

```cmd
setx OPM_BEARER_TOKEN "opm_abc123def456..."
```

Then restart your terminal or IDE.

### macOS

```bash
echo 'export OPM_BEARER_TOKEN="opm_abc123def456..."' >> ~/.zshenv
source ~/.zshenv
```

### Linux

```bash
echo 'OPM_BEARER_TOKEN=opm_abc123def456...' >> ~/.config/environment.d/mcp-tokens.conf
```

Or add to `~/.profile` for terminal-only access.

## Step 3 — Add to mcp-config.json

Add OPM to your MCP client config. The exact location depends on your client:

- **Claude Desktop:** `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
- **VS Code with MCP extension:** `.vscode/mcp.json`
- **Custom tools:** Wherever your tool reads MCP config

### HTTP Mode (Shared Server)

```json
{
  "mcpServers": {
    "opm": {
      "url": "http://192.168.1.178:8765/mcp",
      "headers": {
        "Authorization": "Bearer ${env:OPM_BEARER_TOKEN}"
      }
    }
  }
}
```

### Stdio Mode (Local Only)

```json
{
  "mcpServers": {
    "opm": {
      "command": "uvx",
      "args": ["open-project-manager-mcp"]
    }
  }
}
```

## Step 4 — Reload MCP Config

Most MCP clients require a reload to recognize new server entries.

### Claude Desktop

Restart the app, or use the menu to reload MCP config (menu varies by version).

### VS Code / MCP CLI

```bash
/mcp reload
```

**Important:** This is a **slash command** in your MCP client, not the `mcp_reload` tool (which is Python-only).

## Step 5 — Verify Connection

List all tasks on the server:

```
/call list_tasks
```

Expected output:
```
{ "tasks": [], "has_more": false }
```

Or with existing tasks:
```
{
  "tasks": [
    { "id": "auth-setup", "title": "Set up auth module", "status": "pending", "project": "backend" },
    { "id": "tests-pass", "title": "All tests passing", "status": "pending", "project": "backend" }
  ],
  "has_more": false
}
```

## Step 6 — Run Your First Task

Create a task:

```
/call create_task
  - id: my-first-task
  - title: Deploy new feature
  - description: Ship the new UI to production
  - priority: high
  - project: deployment
```

Expected output:
```
{
  "id": "my-first-task",
  "title": "Deploy new feature",
  "description": "Ship the new UI to production",
  "priority": "high",
  "project": "deployment",
  "status": "pending",
  "created_at": "2025-01-20T14:30:00Z",
  "updated_at": "2025-01-20T14:30:00Z"
}
```

Mark it done:

```
/call complete_task
  - id: my-first-task
```

Expected output:
```
{
  "id": "my-first-task",
  "status": "done",
  "completed_at": "2025-01-20T14:31:00Z"
}
```

## Step 7 — Explore More Tools

Now that you're connected, explore:

- `list_ready_tasks` — Tasks with no blockers
- `set_team_status` — Tell other squads you're `online` / `busy` / etc.
- `post_team_event` — Announce a milestone or error
- `search_tasks` — Full-text search across all tasks

See [MCP Tools Reference](03-mcp-tools-reference.md) for the complete tool list.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `401 Unauthorized` | Check `OPM_BEARER_TOKEN` env var is set correctly; verify with `echo $OPM_BEARER_TOKEN` |
| `Tools not appearing` | Reload MCP config (Step 4), then restart your MCP client |
| `Connection refused` | Verify OPM server is running: `curl http://192.168.1.178:8765/api/v1/stats` |
| `Port 8765 already in use` | Another process is using the port; see [Troubleshooting](09-troubleshooting.md) |

---

**Next:** [MCP Tools Reference](03-mcp-tools-reference.md) — Learn all the tools.
