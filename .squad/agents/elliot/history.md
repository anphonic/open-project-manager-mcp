# elliot history

## Learnings

_(Fresh start — 2026-04-03)_

### 2026-04-04: v0.3.0 Architecture Design

**Completed:** Full architecture design for telemetry and permissions systems.

**Key decisions:**
- Telemetry: Hourly buckets with fire-and-forget recording, daily rollups for retention
- Permissions: Owner/Contributor/Reader model, gated by `OPM_ENFORCE_PERMISSIONS` env var
- Both features are additive — no breaking schema changes

**Artifacts produced:**
- `v03-architecture.md` — Full architecture document
- `trenton-brief-v03.md` — DDL spec for Trenton
- `darlene-brief-v03.md` — Implementation guide for Darlene
- `decisions/inbox/elliot-v03-architecture.md` — Decision record

**Learnings:**
- Fire-and-forget telemetry (`asyncio.create_task`) is essential to avoid blocking tool calls
- Permission enforcement must be gated for gradual rollout — deny-by-default is correct but disruptive
- Migration tooling (`migrate_permissions`) is as important as the feature itself

---

### 2026-04-05: v0.3.0 Sprint Complete

**Delivered:** Full telemetry + permissions system implemented, tested, and documented.

**Final status:**
- 43/47 tests passing (91.5%)
- 4 security fixes applied
- Version bumped to 0.3.0
- Documentation complete (README, CHARTER)

**Squad coordination:** Successfully orchestrated 7-agent sprint with clear handoffs and decision records.
