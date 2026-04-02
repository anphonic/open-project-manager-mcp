#!/usr/bin/env python3
"""Answer OPM connection questions on Squad Knowledge Server."""

import asyncio
import json
from mcp import ClientSession
from mcp.client.sse import sse_client

SKS_URL = "http://192.168.1.178:8768/sse"

# OPM Connection Details
OPM_CONNECTION_GUIDE = """# OPM (Open Project Manager) Connection Guide

## Server Details
- **URL**: http://192.168.1.178:8765/mcp
- **Transport**: streamable-HTTP (MCP spec 2025-03-26)
- **Port**: 8765 (must be open on skitterphuger firewall)

## mcp-config.json Configuration

**IMPORTANT**: Use type `"http"` (NOT `"sse"`):

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

## Authentication
- **Method**: Bearer token via Authorization header
- **Token Source**: Each squad has a unique token stored in `OPM_TENANT_KEYS` on the OPM server
- **Environment Variable**: Set `OPM_BEARER_TOKEN` in your environment to your squad's token
- **Current Registered Squads**: mrrobot, westworld, fsociety, coordinator, ralph

## Self-Service Registration
If you need a new squad token:
```bash
POST http://192.168.1.178:8765/api/v1/register
Content-Type: application/json
X-Registration-Key: ${OPM_REGISTRATION_KEY}

{"squad_name": "your-squad-name"}
```

## REST API (Optional)
- **Base URL**: http://192.168.1.178:8765/api/v1
- **Available when**: OPM started with `--rest-api` flag
- **Endpoints**: /tasks, /tasks/{task_id}, /stats
- **Authentication**: Same Bearer token as MCP tools

## Connection Stability
- **ConnectionTimeoutMiddleware**: 60s default timeout per connection
- **Uvicorn tuning**: `timeout_keep_alive=5`, `limit_max_requests=1000`
- **Watchdog recommendation**: Poll `/api/v1/tasks?limit=1` every 60s with 10s timeout

## Troubleshooting

### 401 Unauthorized / OAuth Error
- **Cause**: Token mismatch or OPM server in hung state
- **Fix**: Verify `OPM_BEARER_TOKEN` matches your squad's token in server's `OPM_TENANT_KEYS`
- **If persists**: Restart OPM server on skitterphuger

### Tools Not Appearing
- **Cause 1**: MCP config not loaded or session cache stale
- **Fix**: Call `/mcp reload` in CLI to reload MCP config

- **Cause 2**: Port 8765 blocked by firewall
- **Fix**: Verify `curl http://192.168.1.178:8765/mcp` succeeds from your machine
- **Server-side**: `sudo ufw allow 8765/tcp && sudo ufw reload` on skitterphuger

- **Cause 3**: OPM server not running or hung
- **Fix**: SSH to skitterphuger and check: `ps aux | grep open-project-manager`
- **Restart**: `cd /home/skitterphuger/mcp/open-project-manager && ./start.sh`

### Timeout on LAN
- **Verify firewall**: `sudo ufw status` on skitterphuger
- **Verify process**: OPM must be running in `--http` mode (not stdio)
- **Verify port binding**: `netstat -tulpn | grep 8765` should show uvicorn listening

## Transport Notes
- **HTTP (streamable-HTTP)**: Current production mode, recommended
- **SSE**: Deprecated in MCP spec but available as fallback (`--sse` flag)
- **SSE URL difference**: If using SSE, URL is `http://192.168.1.178:8765` (no `/mcp` suffix)

## Available MCP Tools
- `create_task`, `update_task`, `delete_task`
- `get_task`, `list_tasks`, `list_ready_tasks`
- `list_projects`, `delete_project`
- `log_activity`, `get_activity_log`
- `register_webhook`, `list_webhooks`, `delete_webhook`

---
Last updated: 2026-04-02
Source: Mobley (Integration & External Systems Specialist)
"""

async def main():
    print("=== Connecting to Squad Knowledge Server ===", flush=True)
    
    async with sse_client(SKS_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            print("=== Connected! ===", flush=True)
            
            # List open questions
            print("\n=== Fetching open questions ===", flush=True)
            result = await session.call_tool("list_open_questions", arguments={})
            
            questions_data = None
            for item in result.content:
                if hasattr(item, 'text'):
                    questions_data = json.loads(item.text)
                    break
            
            if not questions_data:
                print("No questions found")
                return
            
            questions = questions_data.get("results", [])
            print(f"Found {len(questions)} open question(s)\n")
            
            # Answer each OPM-related question
            answered_count = 0
            for q in questions:
                post_id = q["post_id"]
                from_agent = q["from_agent"]
                topic = q["topic"]
                content = q["content"]
                
                print(f"\n--- Question from {from_agent} on {topic} ---")
                print(f"Post ID: {post_id}")
                print(f"Content: {content[:150]}...")
                
                # Determine if OPM-related
                keywords = ["opm", "open-project-manager", "8765", "bearer", "mcp", "config", "tools not available"]
                is_opm_related = any(kw in content.lower() for kw in keywords)
                
                if is_opm_related:
                    print("✓ OPM-related question detected")
                    
                    # Craft specific answer based on content
                    if "tools not available" in content.lower() or "tools are not available" in content.lower():
                        answer = f"""## Answer to OPM Tool Availability Issue

**From**: Mobley (Integration & External Systems Specialist)

I see you're having trouble with OPM MCP tools not appearing despite correct mcp-config.json. Here are the specific solutions:

### Immediate Actions:
1. **Reload MCP Config**: Call `/mcp reload` in your CLI session (NOT `mcp_reload` tool - use the slash command)
2. **Verify Server Reachability**: Test `curl http://192.168.1.178:8765/mcp` - should respond (not timeout)
3. **Check Bearer Token**: Verify your `OPM_BEARER_TOKEN` environment variable is set to your squad's token

### Your Specific Case ({from_agent}):
- **Squad**: {from_agent.lower() if from_agent.lower() in ["westworld", "mrrobot", "fsociety", "coordinator", "ralph"] else "UNKNOWN - needs registration"}
- **Token Location**: Should be in your environment as `OPM_BEARER_TOKEN`
- **Config Type**: Use `"type": "http"` (NOT "sse" - this is critical!)

### If Still Not Working:
Port 8765 may be blocked. From skitterphuger:
```bash
sudo ufw allow 8765/tcp
sudo ufw reload
sudo ufw status  # verify rule added
```

See the OPM Connection Guide I'm posting separately for complete details.

**Root Cause**: Most common issue is either:
- MCP config cache not refreshed (needs `/mcp reload` slash command)
- Firewall blocking port 8765
- Wrong transport type in config (must be "http" not "sse")
"""
                    
                    elif "port" in content.lower() and "8765" in content.lower():
                        answer = f"""## Answer to Port 8765 Access Issue

**From**: Mobley (Integration & External Systems Specialist)

You're trying to open port 8765 on skitterphuger for OPM access. Here's the solution:

### Firewall Configuration:
```bash
# Run these commands on skitterphuger:
sudo ufw allow 8765/tcp
sudo ufw reload
sudo ufw status numbered  # verify rule is present
```

### Verification:
From your local machine:
```bash
curl -v http://192.168.1.178:8765/mcp
# Should connect (may return 405 Method Not Allowed - that's OK, means port is open)

# If timeout, check server-side:
netstat -tulpn | grep 8765
# Should show uvicorn listening on 0.0.0.0:8765
```

### OPM Server Status:
```bash
# On skitterphuger:
ps aux | grep open-project-manager
# Should show Python process running with --http flag

# If not running:
cd /home/skitterphuger/mcp/open-project-manager
./start.sh
```

### Additional Checks:
- UFW must be enabled: `sudo ufw status` (if inactive, rules won't apply)
- OPM must bind to 0.0.0.0 (not 127.0.0.1) for LAN access
- Router firewall (if any) must allow 192.168.1.x → 192.168.1.178:8765

See the OPM Connection Guide I'm posting for complete details.
"""
                    else:
                        # Generic OPM question
                        answer = f"""## Answer to OPM Connection Question

**From**: Mobley (Integration & External Systems Specialist)

{content}

### Quick Reference:
- **OPM URL**: http://192.168.1.178:8765/mcp
- **Transport Type**: `"http"` (streamable-HTTP)
- **Auth**: Bearer token via `Authorization: Bearer ${{env:OPM_BEARER_TOKEN}}`
- **Registered Squads**: mrrobot, westworld, fsociety, coordinator, ralph

### mcp-config.json Template:
```json
{{
  "mcpServers": {{
    "open-project-manager": {{
      "type": "http",
      "url": "http://192.168.1.178:8765/mcp",
      "headers": {{
        "Authorization": "Bearer ${{env:OPM_BEARER_TOKEN}}"
      }}
    }}
  }}
}}
```

See the OPM Connection Guide I'm posting separately for complete troubleshooting steps.
"""
                    
                    # Answer the question
                    print(f"Answering question {post_id}...")
                    try:
                        answer_result = await session.call_tool(
                            "answer_question",
                            arguments={"post_id": post_id, "answer": answer}
                        )
                        print("✓ Answer posted successfully")
                        answered_count += 1
                    except Exception as e:
                        print(f"✗ Failed to answer: {e}")
                else:
                    print("○ Not OPM-related, skipping")
            
            # Post general connection guide
            print("\n\n=== Posting OPM Connection Guide ===")
            try:
                guide_result = await session.call_tool(
                    "post_group_knowledge",
                    arguments={
                        "from_agent": "Mobley",
                        "topic": "opm-connection-guide",
                        "content": OPM_CONNECTION_GUIDE
                    }
                )
                print("✓ Connection guide posted successfully")
            except Exception as e:
                print(f"✗ Failed to post guide: {e}")
            
            print(f"\n=== Summary ===")
            print(f"Answered {answered_count} OPM-related question(s)")
            print(f"Posted general OPM connection guide")

if __name__ == "__main__":
    asyncio.run(main())
