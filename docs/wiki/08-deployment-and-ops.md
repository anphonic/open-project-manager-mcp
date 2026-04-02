# Deployment and Operations

Quick reference for OPM deployment and operations on skitterphuger.

**⚠️ For complete details, see [DEPLOY.md](../../DEPLOY.md) in the project root.**

## Quick Reference

| Item | Value |
|------|-------|
| **Host** | skitterphuger (192.168.1.178) |
| **Port** | 8765 |
| **Process User** | skitterphuger |
| **Git Repo** | `/home/skitterphuger/mcp/open-project-manager/` |
| **Database** | `/home/skitterphuger/mcp/open-project-manager/opm.db` |
| **Start Script** | `/home/skitterphuger/mcp/open-project-manager/start.sh` |

## Starting the Server

### SSH into skitterphuger

```bash
ssh skitterphuger@192.168.1.178
cd /home/skitterphuger/mcp/open-project-manager
```

### Start OPM

```bash
./start.sh
```

Expected output:
```
Uvicorn running on http://0.0.0.0:8765
```

Leave the terminal open (or use `nohup` / `screen` / `systemd`).

## Health Checks

### Server Stats

```bash
curl -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/stats
```

Returns:
```json
{
  "uptime_seconds": 3600,
  "active_connections": 5,
  "total_tasks": 42,
  "tasks_by_status": {...}
}
```

### SSE Stream Test

```bash
curl -N -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/events
```

Should show a `welcome` event immediately, then `server.health` every 30 seconds.

## Restart Procedure

### Graceful Restart

1. Find OPM process:
   ```bash
   ps aux | grep open-project-manager
   ```

2. Kill it:
   ```bash
   kill <PID>
   ```

3. Wait 5 seconds for graceful shutdown

4. Restart:
   ```bash
   ./start.sh
   ```

### Force Restart (if hanging)

```bash
pkill -9 -f open-project-manager
sleep 2
./start.sh
```

## Upgrades

### Pull Latest Code

```bash
cd /home/skitterphuger/mcp/open-project-manager
git pull origin main
```

### Reinstall Dependencies

```bash
pip install -e '.[http]'
```

### Restart Server

```bash
# Kill existing process
pkill -9 -f open-project-manager

# Wait and restart
sleep 2
./start.sh
```

## Monitoring

### Watchdog Script (Auto-Restart)

A watchdog script checks if OPM is running and restarts it if it crashes:

```bash
#!/bin/bash
cd /home/skitterphuger/mcp/open-project-manager

while true; do
  if ! pgrep -f "open-project-manager" > /dev/null; then
    echo "$(date) — OPM down, restarting..."
    ./start.sh
  fi
  sleep 30
done
```

**Install as a background service:** See [DEPLOY.md](../../DEPLOY.md) for systemd setup.

### Manual Monitoring

```bash
watch -n 10 'curl -s -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/stats | jq .'
```

Updates every 10 seconds, showing task counts and connections.

## Logs

OPM logs to stdout. Capture them:

### Using `nohup`

```bash
nohup ./start.sh > opm.log 2>&1 &
```

Then tail:
```bash
tail -f opm.log
```

### Using `screen`

```bash
screen -S opm ./start.sh
```

Attach later:
```bash
screen -r opm
```

## Database Backup

The SQLite database is at `/home/skitterphuger/mcp/open-project-manager/opm.db`.

### Backup

```bash
cp opm.db opm.db.backup.$(date +%Y%m%d-%H%M%S)
```

### Restore

```bash
cp opm.db.backup.20250120-143000 opm.db
# Restart OPM
pkill -9 -f open-project-manager
./start.sh
```

## Disk Space

SQLite database grows with task count. Monitor:

```bash
du -sh /home/skitterphuger/mcp/open-project-manager/opm.db
```

For 10,000 tasks: ~5 MB. For 100,000 tasks: ~50 MB.

## Rollback Procedure

If an upgrade causes issues:

1. Stop OPM: `pkill -9 -f open-project-manager`
2. Rollback code: `git revert HEAD` or `git checkout <previous-commit>`
3. Reinstall: `pip install -e '.[http]'`
4. Restart: `./start.sh`

---

## Common Issues

### Port Already in Use

```
Address already in use: ('0.0.0.0', 8765)
```

**Fix:**
```bash
# Find process using port 8765
lsof -i :8765

# Kill it
kill -9 <PID>

# Or use a different port
OPM_PORT=8766 ./start.sh
```

### Out of Memory

OPM uses ~50–100 MB for typical workloads. If memory usage spikes:

```bash
# Restart to free memory
pkill -9 -f open-project-manager
sleep 2
./start.sh
```

### Database Locked

If you see `database is locked` errors:

```bash
# Another process has the database open
lsof | grep opm.db

# Restart OPM
pkill -9 -f open-project-manager
sleep 2
./start.sh
```

---

## Environment Variables

**Set in `start.sh` or `.env` file:**

```bash
export OPM_TENANT_KEYS='{"squad1":{"key":"..."},"squad2":{"key":"..."}}'
export OPM_REGISTRATION_KEY='<16-char-secret>'
export OPM_DB_PATH='/home/skitterphuger/mcp/open-project-manager/opm.db'
export OPM_HOST='0.0.0.0'
export OPM_PORT='8765'
export OPM_MAX_CONNECTIONS='100'
```

**See [DEPLOY.md](../../DEPLOY.md) for detailed configuration.**

---

**Next:** [Troubleshooting](09-troubleshooting.md) — Common issues and fixes.
