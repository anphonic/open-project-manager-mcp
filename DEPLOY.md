# Skitterphuger Deployment Runbook

Deployment and operations guide for OPM on **skitterphuger** (192.168.1.178).

## Server Details

| Item | Value |
|------|-------|
| **Host** | skitterphuger (192.168.1.178) |
| **OPM Port** | 8765 |
| **Run User** | skitterphuger |
| **Git Repo** | `/home/skitterphuger/mcp/open-project-manager/` |
| **Database** | `/home/skitterphuger/mcp/open-project-manager/opm.db` |
| **Persistent Start Script** | `/home/skitterphuger/mcp/open-project-manager/start.sh` |
| **Temp Start Script** (not persistent) | `/tmp/opm-run.sh` |

---

## First-Time Setup

### Step 1 — Clone and Install

```bash
ssh skitterphuger@192.168.1.178

# Create directories
mkdir -p /home/skitterphuger/mcp/open-project-manager
cd /home/skitterphuger/mcp/open-project-manager

# Clone the repo
git clone https://github.com/yourusername/open-project-manager-mcp.git .

# Install dependencies
pip install -e '.[http]'
```

### Step 2 — Generate Bearer Tokens

Generate a token for each squad that will connect:

```bash
python -m open_project_manager_mcp --generate-token mrrobot
python -m open_project_manager_mcp --generate-token westworld
python -m open_project_manager_mcp --generate-token fsociety
python -m open_project_manager_mcp --generate-token coordinator
python -m open_project_manager_mcp --generate-token ralph
```

Save each output token — they won't be shown again.

### Step 3 — Create Environment File

Create `/home/skitterphuger/mcp/open-project-manager/.env` (or equivalent):

```bash
# OPM tokens for each known squad (JSON object of {squad_id: {key: token}})
export OPM_TENANT_KEYS='{"mrrobot":{"key":"<token-for-mrrobot>"},"westworld":{"key":"<token-for-westworld>"},"fsociety":{"key":"<token-for-fsociety>"},"coordinator":{"key":"<token-for-coordinator>"},"ralph":{"key":"<token-for-ralph>"}}'

# Allows self-service registration via POST /api/v1/register (min 16 chars, omit to disable)
export OPM_REGISTRATION_KEY='<registration-secret-16-chars-minimum>'

# Database path
export OPM_DB_PATH=/home/skitterphuger/mcp/open-project-manager/opm.db

# Max connection age before forced disconnect (prevents event loop saturation)
export OPM_CONNECTION_TIMEOUT=60
```

### Step 4 — Create Persistent Start Script

Create `/home/skitterphuger/mcp/open-project-manager/start.sh`:

```bash
#!/usr/bin/env bash

# Load environment
source /home/skitterphuger/mcp/open-project-manager/.env

# Start OPM with recommended settings
exec python -m open_project_manager_mcp \
  --db "${OPM_DB_PATH}" \
  --host 0.0.0.0 \
  --port 8765 \
  --http \
  --rest-api \
  --connection-timeout "${OPM_CONNECTION_TIMEOUT:-60}"
```

Make it executable and restricted:

```bash
chmod 750 /home/skitterphuger/mcp/open-project-manager/start.sh
chmod 600 /home/skitterphuger/mcp/open-project-manager/.env
```

---

## Environment Variables (Required)

| Variable | Value | Notes |
|----------|-------|-------|
| `OPM_DB_PATH` | `/home/skitterphuger/mcp/open-project-manager/opm.db` | SQLite database file |
| `OPM_TENANT_KEYS` | JSON dict of `{squad: {key: token}}` | Bearer token mappings; omit for no auth |
| `OPM_REGISTRATION_KEY` | 16+ char string | Enables `/api/v1/register` self-service; omit to disable |
| `OPM_CONNECTION_TIMEOUT` | `60` (seconds) | Max connection age before forced disconnect |
| `OPM_BEARER_TOKEN` | Token for OPM itself | Set on each client that calls the server |

---

## Registered Squads (Current)

Five known squads. Their bearer tokens live in `OPM_TENANT_KEYS` on the server.

| Squad ID | Purpose | Token Storage |
|----------|---------|----------------|
| `mrrobot` | MrRobot squad | `OPM_TENANT_KEYS` → `mrrobot.key` |
| `westworld` | Westworld squad | `OPM_TENANT_KEYS` → `westworld.key` |
| `fsociety` | FSociety squad | `OPM_TENANT_KEYS` → `fsociety.key` |
| `coordinator` | Coordinator agent | `OPM_TENANT_KEYS` → `coordinator.key` |
| `ralph` | Ralph squad | `OPM_TENANT_KEYS` → `ralph.key` |

To add a new squad:
1. Generate a token: `python -m open_project_manager_mcp --generate-token newteam`
2. Merge into `OPM_TENANT_KEYS` on the server
3. Restart OPM

---

## Recommended Start Command

```bash
python -m open_project_manager_mcp \
  --db /home/skitterphuger/mcp/open-project-manager/opm.db \
  --host 0.0.0.0 \
  --port 8765 \
  --http \
  --rest-api \
  --connection-timeout 60
```

---

## Upgrading OPM (Standard Procedure)

```bash
cd /home/skitterphuger/mcp/open-project-manager

# Fetch latest code
git pull origin main

# Reinstall package (picks up any new dependencies)
pip install -e '.[http]'

# Find and kill existing process
PID=$(pgrep -f "open_project_manager_mcp" | head -1)
if [ -n "$PID" ]; then
  kill "$PID"
  sleep 2
  if ps -p "$PID" > /dev/null; then
    kill -9 "$PID"
  fi
fi

# Restart
nohup ./start.sh > /tmp/opm.log 2>&1 &
echo $! > /tmp/opm.pid
sleep 2

# Verify health
curl -s http://192.168.1.178:8765/api/v1/stats | head -20
```

> **Note:** If the source repo is not cloned on skitterphuger (e.g., only the installed package is present), see "Deploying When Source Is on a Dev Machine" below instead of using `git pull`.

---

## Deploying When Source Is on a Dev Machine

This section covers the real-world scenario where source code lives on your development machine (e.g., Windows at `J:\Coding\open-project-manager-mcp`) and the server (skitterphuger) only has the installed package/executable — no git repo clone.

Choose one of the three methods below, in order of preference:

### Method 1: Install Directly from Git Remote (Recommended)

If the repository is hosted on GitHub/GitLab and the server has internet access:

```bash
pip install --upgrade "git+https://github.com/anphonic/open-project-manager-mcp.git@<commit-or-tag>"
```

**Steps:**
- Replace `<owner>` with the actual GitHub username or organization
- Use a specific commit SHA or tag (e.g., `@v0.2.0` or `@abc1234def567`) for reproducibility; avoid `@main` in production
- Then restart OPM as normal:

```bash
# Find and kill existing process
PID=$(pgrep -f "open_project_manager_mcp" | head -1)
if [ -n "$PID" ]; then
  kill "$PID"
  sleep 2
  if ps -p "$PID" > /dev/null; then
    kill -9 "$PID"
  fi
fi

# Restart
nohup ./start.sh > /tmp/opm.log 2>&1 &
echo $! > /tmp/opm.pid
```

**Pros:**
- Simplest method; no manual file transfer
- Automatically pulls latest code from the remote
- Best for CI/CD automation

**Cons:**
- Requires internet access on skitterphuger
- Requires GitHub/GitLab to be publicly accessible (or server has SSH keys set up)

---

### Method 2: Build Wheel Locally, SCP to Server

When the server can't reach the git remote (private repo, no internet access, etc.):

**On dev machine:**

```bash
cd J:\Coding\open-project-manager-mcp   # or wherever source lives

# Install build tools
pip install build

# Build the wheel
python -m build --wheel
# Produces: dist/open_project_manager_mcp-<version>-py3-none-any.whl

# Transfer to server (using SCP or another secure method)
scp dist/open_project_manager_mcp-*.whl skitterphuger:/tmp/
```

**On skitterphuger:**

```bash
# Install the wheel
pip install --upgrade /tmp/open_project_manager_mcp-*.whl

# Kill and restart OPM
PID=$(pgrep -f "open_project_manager_mcp" | head -1)
if [ -n "$PID" ]; then
  kill "$PID"
  sleep 2
  if ps -p "$PID" > /dev/null; then
    kill -9 "$PID"
  fi
fi

# Restart
nohup ./start.sh > /tmp/opm.log 2>&1 &
echo $! > /tmp/opm.pid

# Verify
curl -s http://192.168.1.178:8765/api/v1/stats | head -10
```

**Pros:**
- Works without internet access on the server
- Supports private repositories
- Full control over exactly which code is deployed

**Cons:**
- Requires manual build and transfer step
- Need to ensure `build` package is installed locally

---

### Method 3: SCP Source Directory and Install Editable

For rapid iteration when you want live editable installs (not recommended for production):

**On dev machine:**

```bash
# Sync source to server (exclude .git, __pycache__, etc.)
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.pytest_cache' --exclude='*.egg-info' \
  J:/Coding/open-project-manager-mcp/ \
  skitterphuger:/home/skitterphuger/mcp/open-project-manager/
```

*(On Windows, use `wsl rsync` if WSL is available, or use WinSCP / robocopy as alternatives)*

**On skitterphuger:**

```bash
# Navigate to synced directory
cd /home/skitterphuger/mcp/open-project-manager

# Install in editable mode
pip install -e '.[http]'

# Kill and restart OPM
PID=$(pgrep -f "open_project_manager_mcp" | head -1)
if [ -n "$PID" ]; then
  kill "$PID"
  sleep 2
  if ps -p "$PID" > /dev/null; then
    kill -9 "$PID"
  fi
fi

# Restart
nohup ./start.sh > /tmp/opm.log 2>&1 &
echo $! > /tmp/opm.pid
```

**Pros:**
- Code changes are immediately reflected (no rebuild)
- Ideal for rapid testing and debugging

**Cons:**
- Not suitable for production (editable installs can break unexpectedly)
- Requires syncing source on every deployment
- Potential for out-of-sync state between dev and server

---

## Persistent Start Script (Full Content)

Use this as `/home/skitterphuger/mcp/open-project-manager/start.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Load configuration from .env file
ENV_FILE="$(dirname "$0")/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

# Ensure required variables are set
: "${OPM_DB_PATH:?OPM_DB_PATH not set}"
: "${OPM_TENANT_KEYS:?OPM_TENANT_KEYS not set}"

# Create database directory if needed
DB_DIR="$(dirname "$OPM_DB_PATH")"
mkdir -p "$DB_DIR"

# Log startup
echo "[$(date)] Starting open-project-manager-mcp" >&2
echo "  Database: $OPM_DB_PATH" >&2
echo "  Port: 8765" >&2
echo "  Host: 0.0.0.0" >&2
echo "  Connection timeout: ${OPM_CONNECTION_TIMEOUT:-60}s" >&2

# Run the server
exec python -m open_project_manager_mcp \
  --db "$OPM_DB_PATH" \
  --host 0.0.0.0 \
  --port 8765 \
  --http \
  --rest-api \
  --connection-timeout "${OPM_CONNECTION_TIMEOUT:-60}"
```

Set permissions:

```bash
chmod 750 /home/skitterphuger/mcp/open-project-manager/start.sh
chmod 600 /home/skitterphuger/mcp/open-project-manager/.env
```

---

## Watchdog Script

The watchdog monitors OPM health every 60 seconds and restarts if unresponsive.

Create `/home/skitterphuger/mcp/open-project-manager/watchdog.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

OPM_HOST="${OPM_HOST:-192.168.1.178}"
OPM_PORT="${OPM_PORT:-8765}"
STARTUP_DIR="$(dirname "$0")"
PID_FILE="/tmp/opm.pid"
CHECK_URL="http://${OPM_HOST}:${OPM_PORT}/api/v1/stats"
CHECK_TIMEOUT=10
CHECK_INTERVAL=60

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" >> /tmp/opm-watchdog.log
}

restart_opm() {
    log "OPM unresponsive — restarting..."
    
    # Kill existing process if running
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE")
        if ps -p "$OLD_PID" > /dev/null 2>&1; then
            kill "$OLD_PID" 2>/dev/null || true
            sleep 2
            if ps -p "$OLD_PID" > /dev/null 2>&1; then
                kill -9 "$OLD_PID" 2>/dev/null || true
            fi
            log "Killed old process $OLD_PID"
        fi
    fi
    
    # Start new process
    cd "$STARTUP_DIR"
    nohup ./start.sh > /tmp/opm.log 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > "$PID_FILE"
    log "Started new OPM process (PID: $NEW_PID)"
    sleep 3
}

log "Watchdog started (checking every ${CHECK_INTERVAL}s)"

while true; do
    if curl -sf \
        --max-time "$CHECK_TIMEOUT" \
        --connect-timeout "$CHECK_TIMEOUT" \
        "$CHECK_URL" > /dev/null 2>&1; then
        # Health check passed
        :
    else
        # Health check failed
        restart_opm
    fi
    
    sleep "$CHECK_INTERVAL"
done
```

Make it executable:

```bash
chmod 750 /home/skitterphuger/mcp/open-project-manager/watchdog.sh
```

### Running Watchdog as a Background Daemon

```bash
# Manual background run (not persistent across reboot)
nohup /home/skitterphuger/mcp/open-project-manager/watchdog.sh > /tmp/opm-watchdog.log 2>&1 &
echo $! > /tmp/watchdog.pid

# To kill:
kill $(cat /tmp/watchdog.pid)
```

**For persistent startup on reboot**, add to crontab:

```bash
crontab -e
```

Add:

```cron
@reboot /home/skitterphuger/mcp/open-project-manager/watchdog.sh >> /tmp/opm-watchdog.log 2>&1 &
```

Or create a systemd service unit (preferred) at `/etc/systemd/user/opm-watchdog.service`:

```ini
[Unit]
Description=OPM Watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=skitterphuger
WorkingDirectory=/home/skitterphuger/mcp/open-project-manager
ExecStart=/home/skitterphuger/mcp/open-project-manager/watchdog.sh
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user enable opm-watchdog.service
systemctl --user start opm-watchdog.service
systemctl --user status opm-watchdog.service
```

---

## Verifying the Deployment

### Basic Health Check

```bash
# Simple connectivity test (returns 401 if auth is enabled)
curl -I http://192.168.1.178:8765/api/v1/stats
```

### Authenticated Requests (with Bearer Token)

Replace `<token>` with a valid bearer token from `OPM_TENANT_KEYS`.

**Server stats:**

```bash
curl -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/stats
```

**Detailed stats:**

```bash
curl -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/stats?detailed=true
```

**Real-time SSE event stream (receives task + server events):**

```bash
curl -N -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/events
```

**All teams' status:**

```bash
curl -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/status
```

---

## Common Issues

### 401 / "OAuth: needs authentication"

**Cause:** Bearer token mismatch or server in hung state.

**Fix:**
1. Verify the token in `OPM_TENANT_KEYS` matches the one being sent
2. Check token format (should be a long base64 string)
3. If still failing, restart the server:

```bash
pkill -f open_project_manager_mcp
sleep 2
cd /home/skitterphuger/mcp/open-project-manager
nohup ./start.sh > /tmp/opm.log 2>&1 &
```

4. Check logs: `tail -100 /tmp/opm.log`

### Missing MCP Tools

**Cause:** Client connected during OPM startup or during a hang.

**Fix:** Wait 5–10 seconds and reconnect. If the issue persists, check the OPM process is running:

```bash
pgrep -f open_project_manager_mcp
```

If not running, restart it.

### Port 8765 Unreachable

**Cause:** Firewall or OPM not bound to the correct interface.

**Fix:**
1. Check if OPM is running:
   ```bash
   ps aux | grep open_project_manager_mcp
   ```
2. Open firewall port:
   ```bash
   sudo ufw allow 8765/tcp
   ```
3. Verify OPM is bound to 0.0.0.0:
   ```bash
   sudo netstat -tlnp | grep 8765
   ```
4. Test from local host first:
   ```bash
   curl http://127.0.0.1:8765/api/v1/stats
   ```

### Server Hanging (High CPU / Unresponsive)

**Cause:** ConnectionTimeoutMiddleware should kill connections at 60s max; if it's not, forcibly restart.

**Fix:**

```bash
# Find the PID
PID=$(pgrep -f open_project_manager_mcp | head -1)

# Kill it (hard stop)
kill -9 "$PID"

# Restart
cd /home/skitterphuger/mcp/open-project-manager
nohup ./start.sh > /tmp/opm.log 2>&1 &
echo $! > /tmp/opm.pid
```

Check for errors:

```bash
tail -200 /tmp/opm.log
```

### MCP Connection Type Error

**Cause:** Client configured MCP with `type: "sse"` instead of `type: "http"`.

**Fix:** OPM uses streamable HTTP transport. Ensure your `mcp-config.json` has:

```json
"type": "http"
```

Not `"type": "sse"`.

---

## mcp-config.json Entry

Add this to your MCP client configuration (e.g., `~/.mcp.json`, Claude Desktop, etc.):

```json
{
  "mcpServers": {
    "open-project-manager": {
      "type": "http",
      "url": "http://192.168.1.178:8765/mcp",
      "headers": {
        "Authorization": "Bearer ${env:OPM_BEARER_TOKEN}"
      }
    }
  }
}
```

Ensure `OPM_BEARER_TOKEN` environment variable is set on the client machine with one of the squad tokens.

---

## Monitoring

### View Live Logs

```bash
tail -f /tmp/opm.log
```

### View Watchdog Logs

```bash
tail -f /tmp/opm-watchdog.log
```

### Check Process Status

```bash
ps aux | grep -E "open_project_manager|watchdog" | grep -v grep
```

### Check Database Size

```bash
du -h /home/skitterphuger/mcp/open-project-manager/opm.db
```

### View Active Connections

```bash
curl -H "Authorization: Bearer <token>" \
  http://192.168.1.178:8765/api/v1/stats?detailed=true | grep -i "active\|connection"
```

---

## Rollback

To revert to a previous version:

```bash
cd /home/skitterphuger/mcp/open-project-manager

# Show recent commits
git log --oneline -10

# Checkout a previous commit
git checkout <commit-sha>

# Reinstall
pip install -e '.[http]'

# Kill and restart
pkill -f open_project_manager_mcp
sleep 2
nohup ./start.sh > /tmp/opm.log 2>&1 &
echo $! > /tmp/opm.pid
```

---

## Backup

Backup the database periodically:

```bash
BACKUP_DIR="/home/skitterphuger/backups"
mkdir -p "$BACKUP_DIR"

# Manual backup
cp /home/skitterphuger/mcp/open-project-manager/opm.db \
   "$BACKUP_DIR/opm-$(date +%Y%m%d-%H%M%S).db"

# List backups
ls -lh "$BACKUP_DIR"/opm-*.db
```

Automated daily backup via cron:

```bash
crontab -e
```

Add:

```cron
0 2 * * * cp /home/skitterphuger/mcp/open-project-manager/opm.db /home/skitterphuger/backups/opm-$(date +\%Y\%m\%d).db
```

---

## Contact & Support

- **Project repo:** https://github.com/yourusername/open-project-manager-mcp
- **Issue tracker:** GitHub Issues
- **Server admin:** skitterphuger@192.168.1.178
