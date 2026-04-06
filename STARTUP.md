# OPM Coordinator — Session Bootstrap

Quick-reference for any fresh Copilot/agent session working on this project.
Read this first. Every time.

---

## 1. Set Your Environment Variable

Every session needs `OPM_BEARER_TOKEN` set **before** any agent connects to OPM.

The token to use depends on which identity you're operating as:

| Identity | Use When |
|----------|----------|
| `coordinator` | Ted / Squad coordinator sessions (default for this project) |
| `mrrobot` | MrRobot squad agents |
| `westworld` | Westworld squad agents |
| `fsociety` | FSociety squad agents |
| `ralph` | Ralph monitor agent |

**To get the token values**, read them from the server:

```bash
ssh skitterphuger@192.168.1.178 "grep OPM_TENANT_KEYS /home/skitterphuger/mcp/open-project-manager/start.sh"
```

Then set in your shell (Windows):

```powershell
$env:OPM_BEARER_TOKEN = "<coordinator-key-from-start.sh>"
```

Or permanently via Windows System Environment Variables so it survives session restarts.

> ⚠️ Tokens are **not** stored in this repo. The authoritative source is `start.sh` on skitterphuger.

---

## 2. Verify OPM Is Running

```bash
ssh skitterphuger@192.168.1.178 "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer <your-token>' http://127.0.0.1:8765/api/v1/stats"
```

Expected: `200`

**If you get a hang or no response** — OPM is down or hung. Restart it:

```bash
# Find what's holding port 8765
ssh skitterphuger@192.168.1.178 "ss -tlnp sport = :8765"

# Kill by PID from the output above
ssh skitterphuger@192.168.1.178 "kill -9 <PID>"

# Start fresh
ssh skitterphuger@192.168.1.178 "bash -c 'nohup /home/skitterphuger/mcp/open-project-manager/start.sh </dev/null >/tmp/opm.log 2>&1 & disown && echo STARTED'"

# Wait 4 seconds, verify
ssh skitterphuger@192.168.1.178 "sleep 4 && curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer <your-token>' http://127.0.0.1:8765/api/v1/stats"
```

> ⚠️ Do NOT use PowerShell `Invoke-WebRequest` to test OPM from the Windows dev machine — it always times out. Use SSH+curl only.

---

## 3. Infrastructure Reference

| Service | URL | Auth |
|---------|-----|------|
| OPM (task queue) | `http://192.168.1.178:8765/mcp` | Bearer `$OPM_BEARER_TOKEN` |
| OPM REST API | `http://192.168.1.178:8765/api/v1/` | Bearer `$OPM_BEARER_TOKEN` |
| Squad Knowledge | `http://192.168.1.178:8768` | None |
| Godot SKS | `http://192.168.1.178:8767/mcp` | — |
| Blender SKS | `http://192.168.1.178:8760/mcp` | — |
| SSH | `ssh skitterphuger@192.168.1.178` | No password |

> ⚠️ Squad Knowledge is at **8768** — never 8766. The `/` root returns 404 (normal). Use `/sse` endpoint.

---

## 4. Server Files (skitterphuger)

| File | Purpose |
|------|---------|
| `/home/skitterphuger/mcp/open-project-manager/start.sh` | Authoritative start script + all tenant tokens |
| `/home/skitterphuger/mcp/env/bin/` | Python venv — use this pip, not system pip |
| `/tmp/opm.log` | Live server log |
| `/home/skitterphuger/.local/share/open-project-manager-mcp/tasks.db` | SQLite database |

---

## 5. Deploy Updated Package

```powershell
# 1. Build wheel (Windows dev machine)
cd J:\Coding\open-project-manager-mcp
python -m build --wheel

# 2. SCP to server
scp dist\open_project_manager_mcp-*.whl skitterphuger@192.168.1.178:/tmp/

# 3. Install into venv
ssh skitterphuger@192.168.1.178 "/home/skitterphuger/mcp/env/bin/pip install --upgrade /tmp/open_project_manager_mcp-*.whl --quiet"

# 4. Kill old process + restart
ssh skitterphuger@192.168.1.178 "ss -tlnp sport = :8765"
# note PID from output, then:
ssh skitterphuger@192.168.1.178 "kill -9 <PID>"
ssh skitterphuger@192.168.1.178 "bash -c 'nohup /home/skitterphuger/mcp/open-project-manager/start.sh </dev/null >/tmp/opm.log 2>&1 & disown && echo STARTED'"
```

---

## 6. Session Checklist

Before doing any work, confirm:

- [ ] `OPM_BEARER_TOKEN` is set in the current shell
- [ ] OPM returns `200` on health check
- [ ] Read `.squad/team.md` and `.squad/decisions.md`
- [ ] Queried Squad Knowledge for recent decisions

That's it. If all four are green, you're ready to work.
