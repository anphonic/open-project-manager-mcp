# Troubleshooting

Comprehensive troubleshooting guide for common OPM issues.

---

## 401 Unauthorized

**Symptom:** All API requests return `401 Unauthorized`.

```
HTTP/1.1 401 Unauthorized
{"detail": "Invalid API key"}
```

### Causes

- Missing `Authorization: Bearer` header
- Invalid or expired bearer token
- Token from wrong OPM instance
- Typo in token

### Diagnosis

1. Check if bearer token is set:
   ```bash
   echo $OPM_BEARER_TOKEN           # Unix/Linux/macOS
   echo %OPM_BEARER_TOKEN%          # Windows CMD
   ```

2. Token should start with `opm_` and be ~48 characters:
   ```bash
   # Correct format
   opm_abc123def456ghijklmnopqrstuvwxyz1234567890
   ```

3. Try the token manually:
   ```bash
   curl -H "Authorization: Bearer $OPM_BEARER_TOKEN" \
     http://192.168.1.178:8765/api/v1/stats
   ```

### Fixes

1. **Token not set:** Set the environment variable:
   - **Windows:** `setx OPM_BEARER_TOKEN "opm_..."`
   - **macOS:** `echo 'export OPM_BEARER_TOKEN="opm_..."' >> ~/.zshenv`
   - **Linux:** `echo 'OPM_BEARER_TOKEN=opm_...' >> ~/.config/environment.d/mcp-tokens.conf`

2. **Token expired:** Ask your OPM admin for a new token

3. **Restart MCP client:** After setting env var, restart your tool

4. **Verify server auth is enabled:** Run `curl http://192.168.1.178:8765/api/v1/stats` without auth:
   - Returns 401 if auth is enabled (correct)
   - Returns 200 if auth is disabled (unauthenticated mode)

---

## Tools Not Appearing / "OPM Not Available"

**Symptom:** OPM tools don't show up in your MCP client.

```
No tools found for OPM
```

### Causes

- MCP config not reloaded after adding OPM
- Wrong MCP config file location
- Bearer token not set (for HTTP mode)
- Network connectivity issue

### Diagnosis

1. Check MCP config file exists in right location:
   - **Claude Desktop (Windows):** `%APPDATA%\Claude\claude_desktop_config.json`
   - **Claude Desktop (macOS):** `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **VS Code:** `.vscode/mcp.json` (in your workspace)

2. Verify OPM entry in MCP config:
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

3. For HTTP mode, check bearer token is set:
   ```bash
   echo $OPM_BEARER_TOKEN  # Should print token starting with opm_
   ```

### Fixes

1. **Reload MCP config:** Use the `/mcp reload` slash command in your tool (not the `mcp_reload` Python tool)

2. **Restart MCP client entirely:**
   - Claude Desktop: Close and reopen
   - VS Code: Reload window (`Cmd+K Cmd+R` / `Ctrl+K Ctrl+R`)

3. **If using stdio mode:** Reinstall package:
   ```bash
   pip install --upgrade open-project-manager-mcp
   ```

4. **Clear MCP cache:** Some clients cache server responses:
   - Claude Desktop: Delete `~/AppData/Local/Claude/mcp-cache/` (Windows) or `~/Library/Application Support/Claude/mcp-cache/` (macOS)

---

## Connection Refused / Unreachable

**Symptom:** Can't connect to OPM server.

```
curl: (7) Failed to connect to 192.168.1.178 port 8765: Connection refused
```

### Causes

- OPM server not running
- Wrong host or port in MCP config
- Firewall blocking port 8765
- Network unreachable

### Diagnosis

1. Try to reach the server directly:
   ```bash
   curl -v http://192.168.1.178:8765/api/v1/stats
   ```

2. Check if process is running:
   ```bash
   ssh skitterphuger@192.168.1.178 "ps aux | grep open-project-manager"
   ```

3. Ping the host:
   ```bash
   ping 192.168.1.178
   ```

### Fixes

1. **Server not running:** Start OPM on skitterphuger:
   ```bash
   ssh skitterphuger@192.168.1.178 "cd /home/skitterphuger/mcp/open-project-manager && ./start.sh"
   ```

2. **Port already in use:** (Rare, admin problem) Contact admin

3. **Firewall blocking:** Check network rules:
   - Linux: `sudo ufw status` or `sudo iptables -L`
   - Windows: Windows Defender Firewall with Advanced Security
   - macOS: System Preferences → Security & Privacy → Firewall

4. **Network route issue:** Ask network admin to verify 192.168.1.178 is reachable from your location

---

## Port Already in Use

**Symptom:** When starting OPM, error occurs:

```
Address already in use: ('0.0.0.0', 8765)
```

### Diagnosis

1. Find what's using port 8765:
   ```bash
   sudo lsof -i :8765
   ```

   Output might show:
   ```
   COMMAND    PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
   python   12345 skitter   10u  IPv4 999999  0t0 TCP *:8765 (LISTEN)
   ```

### Fixes

1. **If it's an old OPM process:** Kill it:
   ```bash
   kill -9 12345  # Use the PID from above
   sleep 2
   ./start.sh  # Start OPM again
   ```

2. **If it's something else:** Use a different port:
   ```bash
   OPM_PORT=8766 ./start.sh
   ```
   
   Then update MCP config to use 8766 instead of 8765.

---

## OPM Hanging / Unresponsive

**Symptom:** Server was running, now it's not responding.

```
curl: (7) Failed to connect to 192.168.1.178 port 8765: Connection timeout
```

### Causes

- Event loop deadlock
- High memory usage causing swap
- Network interface issue
- Infinite loop in a tool

### Diagnosis

1. Check if process is still running:
   ```bash
   ps aux | grep open-project-manager
   ```

2. Check memory usage:
   ```bash
   ps aux | grep open-project-manager | grep -v grep | awk '{print $6}'  # RSS in KB
   ```

3. Try to connect with a short timeout:
   ```bash
   timeout 5 curl -v http://192.168.1.178:8765/api/v1/stats
   ```

### Fixes

1. **Force restart:**
   ```bash
   pkill -9 -f open-project-manager
   sleep 2
   ./start.sh
   ```

2. **If memory is very high (> 1GB):** Clear the database cache:
   ```bash
   # Backup first
   cp opm.db opm.db.backup
   
   # Restart (clears in-memory caches)
   pkill -9 -f open-project-manager
   sleep 2
   ./start.sh
   ```

3. **Check disk space:**
   ```bash
   df -h /home/skitterphuger/mcp/open-project-manager/
   ```
   
   If full, clean up old logs or database exports.

---

## SSE Stream Issues / "Events Not Arriving"

**Symptom:** Connected to `/api/v1/events` but no events arrive.

```bash
curl -N -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/events
# Hangs, no output
```

### Causes

- Output buffering (most common)
- Stream is open but no events happening
- Network timeout
- Firewall dropping long-lived connections

### Diagnosis

1. Wait 30 seconds; server should send `server.health` heartbeat:
   ```bash
   timeout 35 curl -N -H "Authorization: Bearer <token>" \
     http://192.168.1.178:8765/api/v1/events
   ```

2. Try with unbuffered output:
   ```bash
   curl -N --no-buffer -H "Authorization: Bearer <token>" \
     http://192.168.1.178:8765/api/v1/events
   ```

### Fixes

1. **Use `-N` flag (disable buffering):**
   ```bash
   curl -N -H "Authorization: Bearer <token>" \
     http://192.168.1.178:8765/api/v1/events
   ```

2. **Trigger an event by creating a task:** In another terminal:
   ```bash
   curl -X POST http://192.168.1.178:8765/api/v1/tasks \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"id":"test-task","title":"Test"}'
   ```
   
   Should see `task.created` event in the stream.

3. **If still no heartbeat after 30 seconds:** Check server is running:
   ```bash
   curl -H "Authorization: Bearer <token>" \
     http://192.168.1.178:8765/api/v1/stats
   ```

---

## Wrong Token / Token Revocation

**Symptom:** Token works initially, then suddenly returns 401.

```
curl -H "Authorization: Bearer $OPM_BEARER_TOKEN" \
  http://192.168.1.178:8765/api/v1/stats

# Returns 401
```

### Causes

- Admin revoked your token
- Token is from a different OPM instance
- Admin updated `OPM_TENANT_KEYS` and restarted server

### Diagnosis

1. Check if token is still valid:
   ```bash
   echo $OPM_BEARER_TOKEN  # Print your token
   ```

2. Ask admin if your squad's token was revoked

3. Try with a known-good token (ask admin for a temporary test token)

### Fixes

1. **Request new token:** Contact OPM admin:
   - Admin runs: `open-project-manager-mcp --generate-token my-squad`
   - Admin adds to `OPM_TENANT_KEYS`
   - Admin restarts server
   - Admin provides new token

2. **Update your token:**
   - **Windows:** `setx OPM_BEARER_TOKEN "new-token-here"`
   - **macOS:** `echo 'export OPM_BEARER_TOKEN="new-token-here"' >> ~/.zshenv`
   - **Linux:** `echo 'OPM_BEARER_TOKEN=new-token-here' >> ~/.config/environment.d/mcp-tokens.conf`

3. **Restart MCP client** to apply new token

---

## Port Not Accessible / Firewall

**Symptom:** Can ping 192.168.1.178, but can't reach port 8765.

```bash
ping 192.168.1.178  # Works
curl http://192.168.1.178:8765/api/v1/stats  # Times out
```

### Causes

- Firewall rule blocks TCP 8765
- OPM not listening on 0.0.0.0 (listening only on 127.0.0.1)
- Network ACL or router rule

### Diagnosis

1. From skitterphuger, check if OPM is listening:
   ```bash
   ssh skitterphuger@192.168.1.178
   sudo netstat -tlnp | grep 8765
   # Or
   sudo ss -tlnp | grep 8765
   ```

   Should show:
   ```
   LISTEN  0  128  0.0.0.0:8765  0.0.0.0:*  ...
   ```

2. Check firewall status:
   ```bash
   sudo ufw status  # Linux (UFW)
   sudo iptables -L | grep 8765  # Linux (iptables)
   ```

### Fixes

1. **Allow port 8765 through firewall (Linux):**
   ```bash
   sudo ufw allow 8765/tcp
   sudo ufw reload
   ```

2. **Or allow for specific network:**
   ```bash
   sudo ufw allow from 192.168.0.0/16 to any port 8765
   ```

3. **Check OPM is binding to 0.0.0.0:** In `start.sh`, ensure:
   ```bash
   --host 0.0.0.0 --port 8765
   ```
   
   Not `--host 127.0.0.1` (which is localhost-only).

---

## Database Locked

**Symptom:** Error messages about "database is locked".

```
sqlite3.OperationalError: database is locked
```

### Causes

- Multiple OPM processes accessing the same database
- File permissions issue
- Another tool accessing the database

### Diagnosis

1. Check how many OPM processes are running:
   ```bash
   ps aux | grep open-project-manager | grep -v grep | wc -l
   ```

2. List processes with the database open:
   ```bash
   lsof | grep opm.db
   ```

### Fixes

1. **Kill duplicate processes:**
   ```bash
   pkill -9 -f open-project-manager
   sleep 2
   ./start.sh  # Start fresh
   ```

2. **Check file permissions:**
   ```bash
   ls -la /home/skitterphuger/mcp/open-project-manager/opm.db
   # Should be readable/writable by skitterphuger user
   ```

3. **If permissions wrong, fix them:**
   ```bash
   chown skitterphuger:skitterphuger opm.db
   chmod 644 opm.db
   ```

---

## High Memory / Crashes

**Symptom:** OPM uses lots of memory and crashes.

```
[Out of memory] Process killed
```

### Causes

- Large number of tasks (thousands)
- Memory leak
- Large bulk import operation
- SSE clients not disconnecting

### Diagnosis

1. Check memory usage:
   ```bash
   ps aux | grep open-project-manager | grep -v grep | awk '{print "Memory (KB):", $6}'
   ```

2. Check number of tasks:
   ```bash
   curl -H "Authorization: Bearer <token>" \
     http://192.168.1.178:8765/api/v1/stats | jq '.total_tasks'
   ```

3. Check active SSE connections:
   ```bash
   curl -H "Authorization: Bearer <token>" \
     http://192.168.1.178:8765/api/v1/stats | jq '.active_connections'
   ```

### Fixes

1. **Restart to clear caches:**
   ```bash
   pkill -9 -f open-project-manager
   sleep 2
   ./start.sh
   ```

2. **Increase system swap/memory:** (System admin task)

3. **Archive old tasks:** Export completed tasks and delete from active database:
   ```bash
   # Export completed tasks
   curl -H "Authorization: Bearer <token>" \
     "http://192.168.1.178:8765/api/v1/tasks?status=done&limit=500" > done-tasks.json
   
   # Delete via REST API (if available) or contact admin
   ```

---

## Next Steps

Still stuck? Check:
1. [Quickstart](02-quickstart.md) — Did you follow all steps?
2. [Deployment and Ops](08-deployment-and-ops.md) — Is the server running correctly?
3. Ask your OPM admin for support

---

**Last Resort:** Contact your OPM administrator with:
- Error message (full output)
- Steps to reproduce
- Your squad name
- Timestamp
