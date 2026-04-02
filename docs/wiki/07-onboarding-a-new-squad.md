# Onboarding a New Squad

Step-by-step guide to connect a new squad to OPM.

## Prerequisites

- OPM server is running (e.g., `http://192.168.1.178:8765`)
- Your squad name (e.g., `my-squad`)
- MCP client configured (Claude Desktop, local tools, etc.)

## Step 1 — Get a Bearer Token

Choose one of two options:

### Option A: Ask the Admin

Contact your OPM administrator and provide your squad name. They will:

1. Generate a token: `open-project-manager-mcp --generate-token my-squad`
2. Add it to `OPM_TENANT_KEYS` on the server
3. Restart the OPM server
4. Send you the token

### Option B: Self-Register

If `OPM_REGISTRATION_KEY` is configured, you can self-register:

```bash
curl -X POST http://192.168.1.178:8765/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{
    "squad": "my-squad",
    "registration_key": "<admin-provided-secret>"
  }'
```

Response (save this immediately):
```json
{"squad": "my-squad", "token": "opm_abc123def456..."}
```

## Step 2 — Set OPM_BEARER_TOKEN

Store your token in the environment so MCP clients can find it automatically.

### Windows

```cmd
setx OPM_BEARER_TOKEN "opm_abc123def456..."
```

Restart your terminal or IDE to apply.

**Verify:**
```cmd
echo %OPM_BEARER_TOKEN%
```

Should print: `opm_abc123def456...`

### macOS

```bash
echo 'export OPM_BEARER_TOKEN="opm_abc123def456..."' >> ~/.zshenv
source ~/.zshenv
```

**Verify:**
```bash
echo $OPM_BEARER_TOKEN
```

### Linux

```bash
mkdir -p ~/.config/environment.d
echo 'OPM_BEARER_TOKEN=opm_abc123def456...' >> ~/.config/environment.d/mcp-tokens.conf
```

**Verify:**
```bash
echo $OPM_BEARER_TOKEN
```

## Step 3 — Configure mcp-config.json

Add OPM to your MCP client configuration.

**Location:** Depends on your client:
- **Claude Desktop:** `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
- **VS Code with MCP ext:** `.vscode/mcp.json` in your workspace
- **Custom tools:** Wherever your tool stores MCP config

**Configuration (HTTP, shared server):**

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

**Alternatively (stdio, local only):**

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

Restart the application. (Method varies by version.)

### VS Code / MCP CLI Tools

Use the MCP reload slash command:

```
/mcp reload
```

**⚠️ Important:** Use the **slash command** `/mcp reload`, not the `mcp_reload` Python tool.

### Custom Tools

Restart your tool or check its MCP reload documentation.

## Step 5 — Verify Connection

Test that OPM tools are now available by listing tasks:

```
/call list_tasks
```

Expected output:
```
{
  "tasks": [],
  "has_more": false
}
```

Or if there are existing tasks:
```
{
  "tasks": [
    {
      "id": "task1",
      "title": "Sample task",
      "status": "pending",
      "priority": "medium",
      "project": "default"
    }
  ],
  "has_more": false
}
```

If you get a response, **you're connected!**

## Step 6 — Check Squad Knowledge

Visit the squad knowledge server to find OPM guides and FAQs:

```
http://192.168.1.178:8768
```

(No auth required. This is the SSE knowledge board for cross-squad documentation.)

## Common Pitfalls

### Symptom: `401 Unauthorized` Errors

**Cause:** Invalid or missing bearer token.

**Fix:**
1. Verify token is set: `echo $OPM_BEARER_TOKEN` (Unix) or `echo %OPM_BEARER_TOKEN%` (Windows)
2. Token should start with `opm_`
3. Ask admin for a new token if yours was lost
4. Restart MCP client after setting env var

### Symptom: "Tools Not Appearing" / "OPM Not Available"

**Cause:** MCP config not reloaded after adding OPM entry.

**Fix:**
1. Check MCP config file (right file location?)
2. Run `/mcp reload` (slash command in your tool)
3. Restart your MCP client entirely
4. Verify `OPM_BEARER_TOKEN` is set (required for HTTP mode)

### Symptom: `Connection Refused` / `Unable to Connect`

**Cause:** OPM server not running, or wrong host/port.

**Fix:**
1. Verify server is running: `curl -I http://192.168.1.178:8765/api/v1/stats`
2. Should return HTTP 401 (auth required) or 200 (if unauthenticated)
3. If connection refused, check with admin whether server is up
4. Verify firewall isn't blocking port 8765

### Symptom: `Transport Type Mismatch` / "Protocol Error"

**Cause:** Wrong transport in mcp-config.json. (Mixing `http` vs `stdio`.)

**Fix:**
1. For **shared HTTP server:** Use `"url": "http://192.168.1.178:8765/mcp"` with headers
2. For **local stdio:** Use `"command": "uvx"` with `"args": ["open-project-manager-mcp"]`
3. Check you're using the right one for your setup

### Symptom: `Cached Config` / "Still Getting Old Tools"

**Cause:** MCP client cached the old config.

**Fix:**
1. Delete MCP config cache (location varies by client)
2. Run `/mcp reload`
3. Restart tool

### Symptom: "Port 8765 Already in Use"

**Cause:** Another service is using the port (admin problem, not squad).

**Fix:** Contact admin. See [Troubleshooting](09-troubleshooting.md#port-already-in-use).

## Next Steps

1. **Create your first task:** `create_task(id="my-task", title="Sample task")`
2. **Set team status:** `set_team_status(status="online", message="Squad is online")`
3. **Explore:** Use `list_ready_tasks`, `search_tasks`, `get_team_status` to see what other squads are working on
4. **Read docs:** Check the wiki for tool reference and examples

---

**Next:** [Deployment and Operations](08-deployment-and-ops.md) (for administrators).
