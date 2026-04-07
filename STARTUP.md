# OPM Coordinator — Session Bootstrap

Quick-reference for any fresh Copilot/agent session working on this project.
Read this first. Every time.

---

## 1. Copilot Client Config (mcp-config.json)

**CRITICAL: Do NOT include a `headers` block for OPM in `mcp-config.json`.**
Including `headers` (e.g., for Bearer tokens) causes the Copilot CLI to abandon the SSE transport and silently fall back to HTTP POSTs (`/mcp`), which breaks the connection.

```json
{
  "mcpServers": {
    "open-project-manager": {
      "type": "sse",
      "url": "http://<SERVER_IP>:8765/sse",
      "description": "Open Project Manager (OPM) Tasks and Squad Database"
    }
  }
}
```

The server runs in unauthenticated mode on the LAN (`OPM_TENANT_KEYS=""` with `--allow-unauthenticated-network`). Do not add auth headers — they will break the connection.

---

## 2. Verify OPM Is Running

```bash
ssh skitterphuger@192.168.1.178 "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/sse"
```

Expected: `200` (SSE stream will hang open — that's normal, Ctrl+C to cancel)

**If OPM is down or hung:**

```bash
# Find what's holding port 8765
ssh skitterphuger@192.168.1.178 "ss -tlnp sport = :8765"

# Kill by PID from the output above
ssh skitterphuger@192.168.1.178 "kill -9 <PID>"

# Start fresh
ssh skitterphuger@192.168.1.178 "bash -c 'nohup /home/skitterphuger/mcp/start-opm-fixed.sh </dev/null >/home/skitterphuger/mcp/project-manager-mcp.log 2>&1 & disown && echo STARTED'"

# Wait 4 seconds, verify
ssh skitterphuger@192.168.1.178 "sleep 4 && ss -tlnp | grep 8765"
```

---

## 3. Infrastructure Reference

| Service | Type | URL | Auth |
|---------|------|-----|------|
| OPM (SSE) | `sse` | `http://192.168.1.178:8765/sse` | None (LAN unauthenticated) |
| OPM REST API | HTTP | `http://192.168.1.178:8765/api/v1/` | None (LAN unauthenticated) |
| Squad Knowledge (SKS) | `sse` | `http://192.168.1.178:8768/sse` | None |
| Godot Docs | `http` | `http://192.168.1.178:8767/mcp` | None |
| KiCAD | `sse` | `http://192.168.1.178:8770/sse` | None |
| Blender | `sse` | `http://192.168.1.178:8760/sse` | None |
| SSH | — | `ssh skitterphuger@192.168.1.178` | No password |

> ⚠️ **Transport types matter:** FastMCP servers (OPM, SKS, KiCAD, Blender) use `"type": "sse"` with `/sse` URLs. Custom HTTP-only servers (Godot) use `"type": "http"` with `/mcp` URLs.

---

## 4. Server Files (skitterphuger)

| File | Purpose |
|------|---------|
| `/home/skitterphuger/mcp/start-opm-fixed.sh` | Authoritative start script (SSE mode, unauthenticated) |
| `/home/skitterphuger/mcp/.env` | Contains OPM_TENANT_KEYS (sourced by start script, NOT in git) |
| `/home/skitterphuger/mcp/env/bin/` | Python venv — use this pip, not system pip |
| `/home/skitterphuger/mcp/project-manager-mcp.log` | Live server log |
| `/home/skitterphuger/.local/share/open-project-manager-mcp/tasks.db` | SQLite database |

---

## 5. Deploy Updated Package

```bash
# 1. Build wheel (dev machine)
cd /path/to/open-project-manager-mcp
python -m build --wheel

# 2. SCP to server
scp dist/open_project_manager_mcp-*.whl skitterphuger@192.168.1.178:/tmp/

# 3. Install into venv
ssh skitterphuger@192.168.1.178 "/home/skitterphuger/mcp/env/bin/pip install --upgrade /tmp/open_project_manager_mcp-*.whl --quiet"

# 4. Kill old process + restart
ssh skitterphuger@192.168.1.178 "ss -tlnp sport = :8765"
# note PID from output, then:
ssh skitterphuger@192.168.1.178 "kill -9 <PID>"
ssh skitterphuger@192.168.1.178 "bash -c 'nohup /home/skitterphuger/mcp/start-opm-fixed.sh </dev/null >/home/skitterphuger/mcp/project-manager-mcp.log 2>&1 & disown && echo STARTED'"
```

---

## 6. Session Checklist

Before doing any work, confirm:

- [ ] OPM returns `200` on SSE endpoint check
- [ ] No `headers` block in `mcp-config.json` for OPM
- [ ] Read `.squad/team.md` and `.squad/decisions.md`
- [ ] Queried Squad Knowledge for recent decisions

That's it. If all four are green, you're ready to work.
