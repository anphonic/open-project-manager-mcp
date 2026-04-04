# Schema Review — v0.2.0
**Author:** Trenton (Shama Biswani), Database & Data Pipeline Specialist  
**Reviewed:** Elliot's `darlene-brief-v0.2.0.md` + `decisions.md` v0.2.0 section + current `_SCHEMA`  
**Status:** Advisory — nothing here is a blocker unless flagged ⚠️ CRITICAL.

---

## 1. Due Dates

**Migration pattern — ✓ Correct.**  
`try/except sqlite3.OperationalError` for `ALTER TABLE ... ADD COLUMN` is the standard SQLite pattern. The benign error text is `"duplicate column name: due_date"`. Fine. This must be a separate `conn.execute()` call outside `executescript()`, not embedded inside `_SCHEMA` — `executescript()` would abort the entire script on the error before the `except` could catch it.

**Index — Elliot says "no index needed at this scale." Partially agree, with a flag.**  
The overdue/due-soon query pattern is:
```sql
WHERE due_date IS NOT NULL AND due_date < ? AND status != 'done'
```
A full table scan is fine today. Add the index anyway — it's free to declare now and avoids a future migration:
```sql
CREATE INDEX IF NOT EXISTS tasks_due_date_idx ON tasks(due_date)
WHERE due_date IS NOT NULL;
```
Partial index (`WHERE due_date IS NOT NULL`) keeps the index lean: only rows that actually have a due date are indexed. SQLite 3.8.9+ supports partial indexes — our deployed version is 3.45.3, confirmed supported.

**No other concerns on due-dates.**

---

## 2. FTS5 Content Table

**Trigger DDL — ✓ Verified correct.** Ran all three triggers against a live SQLite 3.45.3 instance:
- `tasks_ai` (insert), `tasks_au` (update delete+reinsert), `tasks_ad` (delete) all behave correctly.
- `INSERT INTO tasks_fts(tasks_fts) VALUES('rebuild')` correctly re-indexes pre-existing rows. ✓

**`rowid` mapping — ✓ Fine.**  
`tasks` has `id TEXT PRIMARY KEY` — in SQLite, TEXT PKs do **not** become the rowid alias (only `INTEGER PRIMARY KEY` does). Every tasks row has a separate implicit integer `rowid`. The FTS5 `content_rowid='rowid'` declaration maps correctly. Rowids are stable for tasks (no rowid is ever changed by normal operations).

**FTS5 availability check — ✓ `SELECT fts5(1)` works.**  
Verified locally. On platforms without FTS5, this throws `OperationalError: no such table: fts5`. Elliot's check is valid. Alternative that also works: `SELECT sqlite_compileoption_used('ENABLE_FTS5')`.

**Tag tokenization — ✓ Better than expected, one note.**  
Tags are stored as JSON: `'["api-v2","backend"]'`. FTS5's default `unicode61` tokenizer strips brackets and quotes (they're non-alphanumeric), and splits on hyphens. Tested behavior:
- `MATCH 'backend'` → ✓ finds tag
- `MATCH 'api'` → ✓ finds `api-v2` (hyphen splits into tokens `api` and `v2`)
- `MATCH 'v2'` → ✓ finds `api-v2`

This is acceptable. One extra capability Darlene can document: FTS5 column-scoped search `MATCH 'tags:backend'` restricts to the tags column only — could be exposed as a search hint to agents.

**⚠️ CRITICAL: `tasks_au` second INSERT — verify the column list.**  
The DDL as written has `INSERT INTO tasks_fts(rowid, id, title, description, tags)` for the re-insert — **no `tasks_fts` command column** in this INSERT. That's correct. Double-check the final implementation doesn't accidentally copy the `tasks_fts` column name from the delete-half, which would produce a column-count mismatch and silently corrupt the index on every update.

**Gotcha: FTS5 index divergence on direct DB writes.**  
If anything ever writes to `tasks` outside the Python server (e.g., sqlite3 CLI, a migration script), the triggers won't fire and the FTS5 index will diverge. Document that `INSERT INTO tasks_fts(tasks_fts) VALUES('rebuild')` must be run manually after any out-of-band writes. Not a code change — just operational awareness.

---

## 3. Activity Log

**Schema — ✓ Solid overall.**

**`TEXT` for `old_value`/`new_value` — ✓ Correct.**  
All task columns are TEXT in storage (including `tags`, which is stored as a JSON string). No type casting needed at log time or read time. Preserves the exact stored value, which is correct for an audit log.

**Indexes — one change recommended.**  
Elliot proposed:
```sql
CREATE INDEX IF NOT EXISTS activity_log_task_idx    ON activity_log(task_id);
CREATE INDEX IF NOT EXISTS activity_log_created_idx ON activity_log(created_at DESC);
```
The `activity_log_task_idx` (task_id only) handles `WHERE task_id = ?` but forces a sort for `ORDER BY created_at DESC`. Replace both with a single composite:
```sql
CREATE INDEX IF NOT EXISTS activity_log_task_created_idx
    ON activity_log(task_id, created_at DESC);
```
This covers `get_task_activity(task_id)` — the primary query — with one index scan, no sort step. If a future global activity feed query (`WHERE created_at > ? ORDER BY created_at DESC` across all tasks) is added, add `activity_log_created_idx` back then. No need to carry it now.

**⚠️ CRITICAL: Missing FK decision — cascade or orphan?**  
`activity_log.task_id` has no `FOREIGN KEY` constraint. When `delete_task` runs:
```python
conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
```
The activity log rows for that task become orphaned. Two options:

1. **Retain orphaned rows (recommended for audit):** Keep no FK. `get_task_activity` should check task existence separately, and return the log even if the task is gone (or document the "task deleted" case).
2. **Cascade delete:** Add `FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE`. Requires `PRAGMA foreign_keys = ON` at connection time — the current codebase does **not** set this pragma. If Darlene wants FK enforcement, she must add it to `create_server()` immediately after `sqlite3.connect()`:
   ```python
   conn.execute("PRAGMA foreign_keys = ON")
   ```
   **Note:** enabling this pragma also activates FK checks on `task_deps`, which already has FK declarations.

**Recommendation:** Option 1 (no FK, retain orphaned logs). It's simpler, the audit data survives task deletion, and avoids changing the FK pragma behavior mid-project.

**Actor on sync tools — note for Darlene.**  
`get_task()`, `list_tasks()`, `list_ready_tasks()`, etc. are sync (non-async) tools. `mcp.get_context()` may not work in sync context. Elliot's spec says sync tools default to `'system'` — make sure `_log()` is only called from async tools or from code paths that explicitly pass actor. Don't call `mcp.get_context()` from sync tool code.

---

## 4. Export-Import

**Export JSON shape — ✓ Correct decisions.**

`SELECT *` from tasks does **not** return SQLite's implicit `rowid` — only named columns come back. Export is clean without any column exclusion logic needed.

**`sort_order` will appear in the export** — it exists in the schema (currently always NULL) but is not used by any query. No action needed, just awareness: imported tasks will restore `sort_order = NULL` which is harmless.

**`tags` in export** — must be a Python list, not the raw JSON string. The existing `_row()` helper already handles this conversion. Make sure `export_all_tasks` uses `_row()` (or equivalent) and not raw `dict(row)`.

**Deps export (flat rows) — ✓ Correct.**  
`[{"task_id": "...", "depends_on": "..."}]` is the right shape. Mirrors the DB schema directly, simple to re-import. The filtered-export logic ("only include dep if BOTH tasks are in export set") is correct — implement as a set intersection after collecting task IDs.

**⚠️ Import gotcha: dangling dep references.**  
With `merge=True`, `INSERT OR IGNORE` for deps silently succeeds even if `depends_on` references a task that doesn't exist in either the import batch or the existing DB. Because `PRAGMA foreign_keys = OFF` by default in SQLite, the FK on `task_deps.depends_on` is not enforced. This creates silent referential garbage.

Recommended fix: before inserting deps, validate each `depends_on` value exists in either (a) the imported task IDs set, or (b) the existing tasks table. Return an error or warning for unresolvable deps. This applies to both `merge=False` and `merge=True` modes.

**Import transaction order — must insert tasks before deps.**  
Even with FKs off, inserting deps before their referenced tasks could cause issues if FKs are later enabled. Always `INSERT tasks` first, then `INSERT task_deps`.

---

## 5. Webhooks

**Schema — ✓ Structurally sound.**

**⚠️ `enabled` column has no mutation path.**  
The planned tools are `register_webhook` (creates with `enabled=1`), `list_webhooks`, and `delete_webhook`. There is no `enable_webhook` / `disable_webhook` tool. Every webhook is always `enabled=1`. The `WHERE enabled = 1` filter in `_fire_webhooks` is harmless but dead code.

Two options:  
1. Add `enable_webhook(id, enabled: bool)` tool — simple `UPDATE webhooks SET enabled = ? WHERE id = ?`.  
2. Remove `enabled` column for v0.2.0, re-add in v0.3.0 when there's a use case.  

I lean toward option 2 to avoid dead schema, but if Darlene wants to future-proof the delivery filter, option 1 is a 15-minute add.

**No index needed on `webhooks`.**  
Webhook tables are low-volume by design (tens of rows). Any query — even full scans — will be sub-millisecond. Do not add indexes here.

**`events` JSON column — load-and-filter in Python is correct.**  
`_fire_webhooks` should fetch all enabled webhooks for the project (or global), then filter `events` in Python using `json.loads(row["events"])`. Do NOT use SQLite JSON functions (`json_each`) — they require SQLite compiled with JSON1, and the code already needs an FTS5 availability check. One extension dependency at a time.

**SSRF guard — implementation note.**  
Elliot's spec calls for `socket.getaddrinfo(hostname, 443)` at registration time. Note that `getaddrinfo` is a blocking DNS call — this runs inside an `async` tool. Wrap in `asyncio.get_event_loop().run_in_executor(None, socket.getaddrinfo, hostname, 443)` to avoid blocking the event loop during registration.

---

## Recommended Index DDL Summary

All indexes Darlene should add, consolidated:

```sql
-- due-dates: partial index for overdue/due-soon queries
CREATE INDEX IF NOT EXISTS tasks_due_date_idx
    ON tasks(due_date)
    WHERE due_date IS NOT NULL;

-- activity_log: composite replaces the two single-column indexes Elliot proposed
-- Drop: activity_log_task_idx, activity_log_created_idx
-- Add:
CREATE INDEX IF NOT EXISTS activity_log_task_created_idx
    ON activity_log(task_id, created_at DESC);
```

The FTS5 virtual table manages its own internal index. No additional indexes needed for `tasks_fts`.  
No indexes needed for `webhooks`.

---

## Quick-reference: SQLite Gotchas for This Codebase

| Gotcha | Impact | Mitigation |
|--------|--------|------------|
| `executescript()` auto-commits any open transaction before running | Could expose partial writes if misused | Already working correctly — `_SCHEMA` has no mid-script state. Keep `ALTER TABLE` migrations outside `executescript()`. |
| `PRAGMA foreign_keys = OFF` by default | FK constraints on `task_deps` are silently unenforced | Current code relies on app-level checks. Fine. Enabling FKs mid-project would validate existing data — test before adding. |
| FTS5 content table: triggers must fire for all writes | Index diverges on out-of-band DB writes | Document: run `rebuild` after any CLI-level writes. |
| FTS5 content table: DELETE triggers must use `old.rowid` not `old.id` | Wrong rowid = stale shadow entries, malformed index errors | Already correct in Elliot's DDL. Verify once more in final implementation. |
| `TEXT PRIMARY KEY` does NOT alias `rowid` in SQLite | FTS5 `content_rowid='rowid'` refers to the implicit integer rowid, not `id` | Already handled correctly. |
| `asyncio.create_task()` in `_fire_webhooks` is fire-and-forget | Delivery errors are silently swallowed | Elliot acknowledged. Acceptable for v0.2.0. Log errors to stderr at minimum. |
| `getaddrinfo` is blocking | Can stall event loop during webhook registration | Wrap in `run_in_executor`. See §5. |

---

*Trenton — nothing here should block Darlene from starting. Items marked ⚠️ CRITICAL should be actioned; everything else is informational or advisory.*
