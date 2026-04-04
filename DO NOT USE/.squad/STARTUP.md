# OPM Coordinator — Session Bootstrap
## ⚠️ READ THIS ON EVERY SESSION START BEFORE ANYTHING ELSE ⚠️

---

## SESSION START CHECKLIST (do these in order, every time)

1. **Verify OPM is running:**
   ```powershell
   Invoke-RestMethod -Uri "http://192.168.1.178:8765/api/v1/tasks?limit=1" -Headers @{Authorization="Bearer 9vz6REq-oyKF7x_xYEPgk0L1F8F-ajijaoXcM6Id-VM"}
   ```
   If it fails: `ssh skitterphuger "nohup bash ~/mcp/open-project-manager/start.sh > /tmp/opm.log 2>&1 & disown"`

2. **Check OPM_BEARER_TOKEN env var:**
   ```powershell
   $env:OPM_BEARER_TOKEN
   ```
   If EMPTY: fix for this session AND persistently:
   ```powershell
   [System.Environment]::SetEnvironmentVariable("OPM_BEARER_TOKEN", "9vz6REq-oyKF7x_xYEPgk0L1F8F-ajijaoXcM6Id-VM", "User")
   $env:OPM_BEARER_TOKEN = "9vz6REq-oyKF7x_xYEPgk0L1F8F-ajijaoXcM6Id-VM"
   ```
   ⚠️ If you had to set it, Copilot CLI won't expand `${env:OPM_BEARER_TOKEN}` until restarted. Use REST fallback.

3. **Read `.squad/decisions.md`** and **`.squad/team.md`**

---

## MCP Config (C:\Users\qbrot\.copilot\mcp-config.json)

```json
{
  "mcpServers": {
    "squad-knowledge":      { "type": "http", "url": "http://192.168.1.178:8766/mcp" },
    "godot-docs":           { "type": "http", "url": "http://192.168.1.178:8767/mcp" },
    "blender":              { "type": "http", "url": "http://192.168.1.178:8760/mcp" },
    "open-project-manager": {
      "type": "http",
      "url": "http://192.168.1.178:8765/mcp",
      "headers": { "Authorization": "Bearer ${env:OPM_BEARER_TOKEN}" }
    }
  }
}
```

**Why OPM shows "OAuth: needs authentication":** MCP SDK requires `AuthSettings` alongside `TokenVerifier`, which makes the server advertise OAuth metadata. Copilot CLI sees this and tries OAuth. The fix is NOT a code change — it is ensuring `OPM_BEARER_TOKEN` is set in the process before CLI launches. Once set, the CLI sends the real token and auth succeeds.

**Why squad-knowledge fails:** Port 8768 = SSE (legacy, broken). Port 8766 = HTTP/streamable (correct). Config is already fixed.

---

## HTTP Fallback (always works regardless of MCP tool status)

```powershell
# Query OPM tasks
Invoke-RestMethod -Uri "http://192.168.1.178:8765/api/v1/tasks?project=opm-v0.3.0" `
  -Headers @{Authorization="Bearer 9vz6REq-oyKF7x_xYEPgk0L1F8F-ajijaoXcM6Id-VM"}

# Update task status
Invoke-RestMethod -Uri "http://192.168.1.178:8765/api/v1/tasks/TASKID" -Method Patch `
  -Headers @{Authorization="Bearer 9vz6REq-oyKF7x_xYEPgk0L1F8F-ajijaoXcM6Id-VM"; "Content-Type"="application/json"} `
  -Body '{"status":"done"}'

# Search SKS board
Invoke-RestMethod -Uri "http://192.168.1.178:8768/search" -Method Post `
  -Headers @{"Content-Type"="application/json"} `
  -Body '{"query":"your query here","n_results":5}'
```

---

## Tokens

| Squad | Token | Use |
|-------|-------|-----|
| mrrobot | 9vz6REq-oyKF7x_xYEPgk0L1F8F-ajijaoXcM6Id-VM | **OPM_BEARER_TOKEN — coordinator** |
| westworld | UId2CLnMFZ5gXv16BexSgis5Gxj27TC2bKuNGofX1aQ | SKS team |
| fsociety | kdQxUOV-BjyoXyyDnxGTUmFhZ_7ULSpW7UFt0Quo5jw | fsociety agents |
| coordinator | 0hXMzrki16ai1o2UFGuiljy4HylB7pBG1Jba_sKoucg | coordinator squad |
| ralph | 84jPlPe4gyTTAlmtqWlED9S8OhGMqvJbBgMgwrowSmQ | ralph agent |

**OPM_BEARER_TOKEN must = mrrobot token.** Was previously wrong (westworld). Fixed 2026-04-04.

---

## Skitterphuger Deployment

- SSH: `ssh skitterphuger`
- OPM production venv: `/home/skitterphuger/mcp/env/`
- Start script: `~/mcp/open-project-manager/start.sh`
- DB path: `/home/skitterphuger/.local/share/open-project-manager-mcp/tasks.db`
- Logs: `/tmp/opm.log`
- Deploy wheel:
  ```powershell
  cd J:\Coding\open-project-manager-mcp
  python -m build --wheel --no-isolation
  scp dist\*.whl skitterphuger:/tmp/
  ssh skitterphuger "/home/skitterphuger/mcp/env/bin/pip install --force-reinstall --quiet /tmp/*.whl && nohup bash ~/mcp/open-project-manager/start.sh > /tmp/opm.log 2>&1 & disown"
  ```
- OC = OpenCode on skitterphuger. Reach via SKS board.

---

## TEAM_ROOT
`J:\Coding\open-project-manager-mcp`

---

## Current State (as of 2026-04-04)

- **Deployed:** v0.2.1 on skitterphuger
- **Next:** v0.3.0 — telemetry + permissions. Designs DONE. Implementation NOT started.
- **v0.3.0 OPM tasks:** telemetry schema, permissions schema, telemetry API, event stream, permissions enforcement, tests, docs — all pending
- **SKM v2.7.0:** new tools — `get_post`, `list_topics`, `prune_group_knowledge`, `get_collection_stats`, `get_stats`, `register_tenant`, `revoke_tenant`
- **GitHub:** origin/master = d420390

---

## Why Things Break Every Session

| Problem | Root Cause | Fix |
|---------|-----------|-----|
| OPM "OAuth: needs authentication" | `OPM_BEARER_TOKEN` env var not in process | Set User-scope env var before launching CLI |
| OPM env var wrong | Was westworld token, not mrrobot | Fixed 2026-04-04 |
| squad-knowledge ✗ | Config had port 8768 (SSE) not 8766 (HTTP) | Fixed in mcp-config.json |
| MCP tools ✗ after env var set | env var not inherited by already-running CLI process | Restart CLI once after setting |
