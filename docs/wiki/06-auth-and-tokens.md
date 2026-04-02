# Authentication and Tokens

OPM uses **multi-tenant bearer token authentication** for HTTP access and REST API.

## How Auth Works

Every request to OPM over HTTP must include a bearer token:

```bash
curl -H "Authorization: Bearer opm_abc123def456..." \
  http://192.168.1.178:8765/api/v1/tasks
```

The token identifies your squad and grants access to OPM's tools and REST API.

## Token Generation

### Admin-Issued Tokens

The OPM admin generates a token for your squad using the CLI:

```bash
python -m open_project_manager_mcp --generate-token my-squad
```

Output:
```
opm_abc123def456ghijklmnopqrstuvwxyz1234567890
```

**Important:** Save the token immediately — it's only shown once. The admin then adds it to `OPM_TENANT_KEYS` and restarts the server.

### Self-Service Registration

If `OPM_REGISTRATION_KEY` is set, squads can self-register:

```bash
curl -X POST http://192.168.1.178:8765/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{
    "squad": "my-squad",
    "registration_key": "<admin-provided-registration-secret>"
  }'
```

Response:
```json
{
  "squad": "my-squad",
  "token": "opm_abc123def456ghijklmnopqrstuvwxyz1234567890"
}
```

**Rate limit:** 5 registrations per minute per IP address.

## OPM_BEARER_TOKEN Environment Variable

Set this environment variable so MCP clients automatically use your token.

### Windows

Persistent for all terminal windows:

```cmd
setx OPM_BEARER_TOKEN "opm_abc123def456..."
```

Then restart your terminal or IDE.

**Check it's set:**
```cmd
echo %OPM_BEARER_TOKEN%
```

### macOS

Persistent for all terminal sessions:

```bash
echo 'export OPM_BEARER_TOKEN="opm_abc123def456..."' >> ~/.zshenv
source ~/.zshenv
```

For GUI apps (e.g., Claude Desktop):

```bash
launchctl setenv OPM_BEARER_TOKEN "opm_abc123def456..."
```

(Resets on reboot; add to a LaunchAgent plist for true persistence.)

**Check it's set:**
```bash
echo $OPM_BEARER_TOKEN
```

### Linux

Persistent for systemd user session:

```bash
mkdir -p ~/.config/environment.d
echo 'OPM_BEARER_TOKEN=opm_abc123def456...' >> ~/.config/environment.d/mcp-tokens.conf
```

Or add to `~/.profile` for terminal-only access:

```bash
export OPM_BEARER_TOKEN="opm_abc123def456..."
```

**Check it's set:**
```bash
echo $OPM_BEARER_TOKEN
```

## Using the Token in MCP Config

Reference the environment variable in your MCP config:

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

The MCP client resolves `${env:OPM_BEARER_TOKEN}` at runtime.

## Multi-Tenant Architecture

OPM supports multiple squads with separate tokens. The server config looks like:

```bash
export OPM_TENANT_KEYS='
{
  "mrrobot": {"key": "opm_token_mrrobot..."},
  "westworld": {"key": "opm_token_westworld..."},
  "fsociety": {"key": "opm_token_fsociety..."},
  "coordinator": {"key": "opm_token_coordinator..."},
  "ralph": {"key": "opm_token_ralph..."}
}
'
```

Each squad's token is validated on every request. The token determines:
- Which squad's events you see
- How your team status is recorded
- Which team events you can publish

## Token Security

### Bearer Tokens

- **Format:** `opm_` prefix followed by random bytes (URL-safe base64)
- **Length:** ~48 characters
- **Validation:** Constant-time comparison (prevents timing attacks)
- **Transmission:** Use HTTPS in production to prevent token interception

### Token Storage

**DO:**
- Store in environment variables
- Use `.env` files (local dev only, never commit)
- Use OS-level secret management (Windows Credential Manager, macOS Keychain, Linux systemd)

**DON'T:**
- Hardcode tokens in source code
- Commit tokens to Git
- Share tokens in Slack/email unencrypted

### Token Revocation

To revoke a token, the admin must:

1. Remove the squad from `OPM_TENANT_KEYS`
2. Restart the OPM server
3. Optionally delete the squad's data (if `tenant_keys` table is used)

No in-place token revocation in v0.2.0; requires server restart.

## Registered Squads

The following squads are pre-registered on skitterphuger:

| Squad | Token | Notes |
|-------|-------|-------|
| `mrrobot` | (ask admin) | Main team |
| `westworld` | (ask admin) | Companion team |
| `fsociety` | (ask admin) | Engineering squad |
| `coordinator` | (ask admin) | Cross-squad coordination |
| `ralph` | (ask admin) | Auxiliary functions |

Each squad gets a unique token. New squads can either:
1. **Ask the admin** to generate a token and add it to `OPM_TENANT_KEYS`
2. **Self-register** if `OPM_REGISTRATION_KEY` is set (less secure but faster)

## Unauthenticated Mode

If neither `OPM_TENANT_KEYS` nor `OPM_REGISTRATION_KEY` is set, the server runs **without authentication**. Suitable for local development but **never** expose to a network.

```bash
# Unsafe — local only
open-project-manager-mcp --http --host 127.0.0.1 --port 8765
```

## Authorization Errors

### 401 Unauthorized

**Symptom:** All API requests return `401 Unauthorized`.

**Causes:**
- Missing `Authorization: Bearer` header
- Invalid or expired token
- Token for a different OPM instance

**Fix:**
```bash
# Verify token is set
echo $OPM_BEARER_TOKEN

# Verify token format
# Should be: opm_<48-char-string>

# Get new token from admin if expired
```

### 403 Forbidden

**Symptom:** Authenticated, but operation is denied (rare).

**Cause:** Human-approval requirement (e.g., `delete_task` requires `human_approval=true`).

**Fix:** Add the required parameter to your call.

## API Endpoints That Require Auth

All HTTP endpoints require a valid bearer token unless `--allow-unauthenticated-network` is set:

| Endpoint | Auth Required | Notes |
|----------|---------------|-------|
| `POST /api/v1/register` | No | Self-service registration endpoint (if enabled) |
| `GET /api/v1/mcp` | Yes | MCP protocol endpoint |
| `GET /api/v1/tasks` | Yes | List tasks |
| `POST /api/v1/tasks` | Yes | Create task |
| `GET /api/v1/events` | Yes | SSE event stream |
| `PUT /api/v1/status` | Yes | Set team status |
| All other `/api/v1/*` | Yes | Everything else |

---

**Next:** [Onboarding a New Squad](07-onboarding-a-new-squad.md) — Step-by-step setup guide.
