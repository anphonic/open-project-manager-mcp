# OPM Wiki

Complete documentation for the Open Project Manager (OPM) MCP server and toolkit.

## Table of Contents

1. **[What is OPM?](01-what-is-opm.md)** — Architecture overview, core concepts, SQLite task queue design, MCP protocol, HTTP/SSE transport, REST API, and how OPM fits into multi-squad LAN environments.

2. **[Quickstart](02-quickstart.md)** — Get connected to OPM in 5 minutes: add to mcp-config.json, set bearer token, verify connection, and run your first task.

3. **[MCP Tools Reference](03-mcp-tools-reference.md)** — Complete reference for all 24 MCP tools including task CRUD, dependency tracking, stats, team coordination, and subscriptions. Includes parameters, return values, and examples.

4. **[REST API Reference](04-rest-api-reference.md)** — *Coming soon* — Full REST API endpoint reference with curl examples (Mobley).

5. **[Messaging System](05-messaging-system.md)** — How real-time SSE events work: event types, team status, subscriptions, heartbeats, curl examples, and SSE vs polling.

6. **[Auth and Tokens](06-auth-and-tokens.md)** — Multi-tenant bearer token authentication: token generation, registration, env var configuration, token revocation, and registered squads.

7. **[Onboarding a New Squad](07-onboarding-a-new-squad.md)** — Step-by-step guide for new teams: get token, configure mcp-config.json, set env vars, verify with list_tasks, and troubleshoot common pitfalls.

8. **[Deployment and Operations](08-deployment-and-ops.md)** — Quick reference for skitterphuger deployment: start procedures, health checks, restart, upgrades, and monitoring.

9. **[Troubleshooting](09-troubleshooting.md)** — Comprehensive troubleshooting guide for 401 errors, missing tools, port issues, hanging connections, SSE stream problems, and firewall blocks.

---

**Audience:** AI agent squads, LAN-connected MCP clients, and OPM administrators.
