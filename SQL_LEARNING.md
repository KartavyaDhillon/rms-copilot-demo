# SQL Learning Notes — RMS Copilot Project

Learning SQL through a real project you built is the fastest way to understand it.
Every example here uses tables that actually exist in your database.

---

## CHAPTER 1: What Even Is a Database?

Think of a database as a very organized, very powerful Excel workbook.

- **Database** = the whole workbook
- **Table** = one sheet in the workbook
- **Column** = one column heading in that sheet (like "Date", "Name", "Amount")
- **Row** = one row of data in that sheet

The difference from Excel: a database can hold millions of rows, multiple programs can read/write at the same time, it enforces rules (you can't put text in a number column), and you can ask complex questions using SQL.

**SQL** stands for Structured Query Language. It's the language you speak to a database. It reads almost like plain English.

---

## CHAPTER 2: Your Database Tables

Your project has these tables in PostgreSQL:

### `run_files` — stores every file from every SA run
```
id          — a unique number for each row (auto-assigned)
pipeline    — which process made this file (always "short_allocation" for now)
tag         — which SA run (SA-01, SA-02, SA-03, SA-04)
run_date    — the trading date (e.g. 2026-06-29)
ran_at      — exact timestamp when the file was saved
kind        — what type of file (see list below)
filename    — the original filename (e.g. "C_SA01_P_06436_29062026_i01.lis")
size_bytes  — how big the file is, in bytes
content     — the actual file (stored as raw bytes)
```

**File kinds used in this project:**
- `log` — the console log from each run
- `output` — the SA output Excel (SA01_OUTPUT FILE.xlsx)
- `output_zone` — zone files (LUDH_zone.xlsx, DELH_zone.xlsx, etc.)
- `input_c_lis` — NSE CM segment file
- `input_f_lis` — NSE F&O segment file
- `input_o_lis` — NSE Commodity Options file
- `input_mcx_peak` — MCX peak margin file
- `input_nccl_peak` — NCDEX peak margin file
- `input_mcx_deposit` — MCX deposit/collateral file
- `input_risk_report` — risk report file
- `input_brm_list` — BRM list file
- `input_exception` — exception file

### `run_status` — tracks what stage each SA run is at
```
tag         — SA-01 / SA-02 / SA-03 / SA-04
run_date    — today's date
overall     — done / error / null
code1       — done / error / null  (Download step)
code2       — done / error / null  (SA Processing step)
code3       — done / error / null  (LAS step 1)
code4       — done / error / null  (LAS step 2)
...
```

---

## CHAPTER 3: Basic SQL — Reading Data (SELECT)

`SELECT` is how you read data. This is what you'll use 90% of the time.

### See everything in a table (careful on big tables!)
```sql
SELECT * FROM run_files;
```
`*` means "all columns". On a table with thousands of rows and file content, this is slow. Add `LIMIT` to be safe.

### See only some columns
```sql
SELECT id, tag, kind, filename, run_date, size_bytes
FROM run_files;
```

### Limit how many rows you get back
```sql
SELECT id, tag, kind, filename, size_bytes
FROM run_files
LIMIT 10;
```

### Filter rows with WHERE
```sql
-- Show only output files
SELECT id, tag, filename, run_date
FROM run_files
WHERE kind = 'output';

-- Show only files from today
SELECT id, tag, kind, filename
FROM run_files
WHERE run_date = '2026-06-29';

-- Combine conditions with AND
SELECT id, tag, kind, filename
FROM run_files
WHERE kind = 'output'
  AND run_date = '2026-06-29';
```

### Sort results with ORDER BY
```sql
-- Newest files first
SELECT id, tag, kind, filename, ran_at
FROM run_files
ORDER BY ran_at DESC;   -- DESC = descending (newest first)
                        -- ASC  = ascending  (oldest first)
```

### Count rows
```sql
-- How many files do we have total?
SELECT COUNT(*) FROM run_files;

-- How many files per kind?
SELECT kind, COUNT(*) as file_count
FROM run_files
GROUP BY kind
ORDER BY file_count DESC;
```

---

## CHAPTER 4: Useful Queries for THIS Project

These are real queries you can run to check your database.

### Check what's been archived for today's run
```sql
SELECT id, tag, kind, filename, size_bytes, ran_at
FROM run_files
WHERE run_date = CURRENT_DATE
ORDER BY ran_at;
```

### Check the last 20 files archived (any date)
```sql
SELECT id, tag, kind, filename, run_date, size_bytes
FROM run_files
ORDER BY id DESC
LIMIT 20;
```

### See all input files for a specific date
```sql
SELECT tag, kind, filename, size_bytes
FROM run_files
WHERE kind LIKE 'input_%'     -- LIKE with % means "starts with input_"
  AND run_date = '2026-06-29'
ORDER BY tag, kind;
```

### Check if SA-01 files for today are there
```sql
SELECT kind, filename, size_bytes
FROM run_files
WHERE tag = 'SA-01'
  AND run_date = CURRENT_DATE;
```

### See all output files ever stored
```sql
SELECT tag, run_date, filename, size_bytes, ran_at
FROM run_files
WHERE kind IN ('output', 'output_zone')    -- IN checks multiple values
ORDER BY run_date DESC, tag;
```

### How much total data is stored (in MB)
```sql
SELECT 
    ROUND(SUM(size_bytes) / 1024.0 / 1024.0, 2) AS total_mb,
    COUNT(*) AS file_count
FROM run_files;
```

### Files per day (how many runs archived each day)
```sql
SELECT run_date, COUNT(*) as files, SUM(size_bytes) as total_bytes
FROM run_files
GROUP BY run_date
ORDER BY run_date DESC;
```

---

## CHAPTER 5: LIKE — Pattern Matching

`LIKE` lets you search by partial match. Two special characters:
- `%` means "anything here" (zero or more characters)
- `_` means "exactly one character"

```sql
-- Files whose kind starts with "input_"
WHERE kind LIKE 'input_%'

-- Filenames containing "SA01"
WHERE filename LIKE '%SA01%'

-- Filenames ending in .xlsx
WHERE filename LIKE '%.xlsx'

-- Exactly 5 characters in tag (SA-01, SA-02, etc.)
WHERE tag LIKE 'SA-0_'
```

---

## CHAPTER 6: Writing Data (INSERT)

`INSERT` adds a new row. In this project, Python handles all inserts (via `save_run_file()`), but it's useful to understand what Python is doing:

```sql
INSERT INTO run_files (pipeline, tag, run_date, ran_at, kind, filename, size_bytes, content)
VALUES ('short_allocation', 'SA-01', '2026-06-29', NOW(), 'output', 'SA01_OUTPUT FILE.xlsx', 245678, <bytes>);
```

`NOW()` is a PostgreSQL function that returns the current timestamp automatically.

---

## CHAPTER 7: Updating Data (UPDATE)

`UPDATE` changes existing rows. Always use `WHERE` or you'll change ALL rows (a common disaster).

```sql
-- Change a filename (example only — don't actually do this)
UPDATE run_files
SET filename = 'SA01_OUTPUT_CORRECTED.xlsx'
WHERE id = 42;

-- Update run status to mark as done
UPDATE run_status
SET overall = 'done'
WHERE tag = 'SA-01' AND run_date = '2026-06-29';
```

---

## CHAPTER 8: Deleting Data (DELETE)

`DELETE` removes rows. Always use `WHERE`.

```sql
-- Remove a specific file by ID
DELETE FROM run_files WHERE id = 42;

-- Clean up test data from a specific date
DELETE FROM run_files WHERE run_date = '2026-01-01';
```

**WARNING:** PostgreSQL has no "undo" for DELETE by default. Be careful.

---

## CHAPTER 9: Data Types — What Each Column Can Hold

| Type | Meaning | Example |
|------|---------|---------|
| `TEXT` | Any length string | `'short_allocation'`, `'SA-01'` |
| `INTEGER` | Whole number | `42`, `0`, `-5` |
| `BIGINT` | Large whole number | `9999999999` |
| `BIGSERIAL` | Auto-incrementing BIGINT | Used for `id` columns |
| `DATE` | Calendar date | `'2026-06-29'` |
| `TIMESTAMP` | Date + time | `'2026-06-29 14:30:00'` |
| `BOOLEAN` | True or false | `true`, `false` |
| `BYTEA` | Raw binary data | file contents, images |
| `NUMERIC(10,2)` | Decimal number | `1234567.89` (financial) |

In your `run_files` table:
- `id` is `BIGSERIAL` — auto-assigned, never repeat
- `run_date` is `DATE` — just the date, no time
- `ran_at` is `TIMESTAMP` — date + time
- `content` is `BYTEA` — the file itself
- Everything else is `TEXT` or `INTEGER`

---

## CHAPTER 10: NULL — The Absence of a Value

`NULL` means "no value" — it's not zero, not empty string, it's nothing.

```sql
-- Find rows where overall is NULL (run hasn't finished)
SELECT tag, run_date, code1, code2, overall
FROM run_status
WHERE overall IS NULL;

-- Find runs that DID complete
WHERE overall IS NOT NULL

-- NULL in a WHERE clause with = doesn't work:
WHERE overall = NULL   -- WRONG, always returns nothing
WHERE overall IS NULL  -- CORRECT
```

In Python with psycopg2, `None` becomes `NULL` in the database.

---

## CHAPTER 11: Aggregate Functions

Aggregate functions calculate one result from many rows:

```sql
COUNT(*)              -- count rows
SUM(size_bytes)       -- total size
AVG(size_bytes)       -- average size
MAX(size_bytes)       -- largest file
MIN(run_date)         -- earliest date
```

These usually go with `GROUP BY` to compute per-group:

```sql
-- Average file size per kind
SELECT kind, 
       COUNT(*) as count,
       ROUND(AVG(size_bytes)/1024.0, 1) as avg_kb,
       ROUND(MAX(size_bytes)/1024.0, 1) as max_kb
FROM run_files
GROUP BY kind
ORDER BY count DESC;
```

---

## CHAPTER 12: How Python Talks to PostgreSQL

Your `core/db_postgres.py` uses `psycopg2`. Here's the pattern:

```python
import psycopg2
import psycopg2.extras

def get_something():
    conn = psycopg2.connect("postgresql://user:pass@host:5432/dbname")
    try:
        # RealDictCursor makes rows come back as dicts (row["column"] not row[0])
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # %s is a placeholder — psycopg2 fills it safely (prevents SQL injection)
            cur.execute(
                "SELECT id, filename FROM run_files WHERE kind = %s AND run_date = %s",
                ("output", "2026-06-29")   # values go here as a tuple
            )
            rows = cur.fetchall()   # list of dicts
        conn.commit()   # needed for INSERT/UPDATE/DELETE, harmless for SELECT
        return rows
    finally:
        conn.close()   # ALWAYS close the connection
```

**Why `%s` and not f-strings?**
If you used `f"WHERE kind = '{kind}'"`, a malicious user could type `' OR '1'='1` as the kind and get ALL rows. The `%s` pattern tells psycopg2 to escape the value safely. Never use string formatting for SQL values.

---

## CHAPTER 13: Running SQL Directly — How To Check Your Database

From the command line (activate your venv first):

```bash
# Windows — connect to your database
psql postgresql://user:pass@localhost:5432/rmsdb

# OR use pgAdmin (GUI tool — much easier for beginners)
```

From Python script (quick check):
```python
# Run this from the project root
python -c "
from core.db_postgres import _conn
conn = _conn()
cur = conn.cursor()
cur.execute('SELECT id, tag, kind, filename, run_date FROM run_files ORDER BY id DESC LIMIT 10')
for row in cur.fetchall():
    print(row)
conn.close()
"
```

---

## CHAPTER 14: Indexes — Making Queries Fast

When a table has millions of rows, `WHERE run_date = '2026-06-29'` checks every single row. An **index** is like the index of a book — it tells the database exactly where to jump.

```sql
-- Create an index on run_date for fast date range queries
CREATE INDEX idx_run_files_run_date ON run_files(run_date);

-- Create an index on kind for fast kind filtering
CREATE INDEX idx_run_files_kind ON run_files(kind);

-- Composite index — fast queries filtering on BOTH pipeline AND run_date
CREATE INDEX idx_run_files_pipeline_date ON run_files(pipeline, run_date);
```

PostgreSQL automatically uses indexes when the query matches. You don't need to mention them in your SELECT.

---

## CHAPTER 15: The CREATE TABLE Statement

This is how `run_files` was created. Reading it tells you everything about the table:

```sql
CREATE TABLE IF NOT EXISTS run_files (
    id          BIGSERIAL PRIMARY KEY,    -- auto-number, unique identifier
    pipeline    TEXT NOT NULL,            -- NOT NULL = required, can't be empty
    tag         TEXT,                     -- nullable — optional
    run_date    DATE NOT NULL,
    ran_at      TIMESTAMP NOT NULL,
    kind        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    size_bytes  INTEGER DEFAULT 0,        -- DEFAULT = value if not specified
    content     BYTEA NOT NULL
);
```

- `PRIMARY KEY` = uniquely identifies each row, can't be NULL or duplicate
- `NOT NULL` = this column must always have a value
- `DEFAULT 0` = if you don't provide a value, use 0

---

## CHAPTER 16: DISTINCT ON — Keep Only One Row Per Group (PostgreSQL-specific)

This is one of the most powerful and unique PostgreSQL features. Standard SQL doesn't have it.

**The problem:** After a force re-run of SA-01, the database has TWO sets of input files for the same date and tag:
```
id=1  tag=SA-01  run_date=2026-06-29  kind=input_c_lis  ran_at=12:30  (first run)
id=2  tag=SA-01  run_date=2026-06-29  kind=input_c_lis  ran_at=12:45  (force re-run)
```
If you just `SELECT * FROM run_files WHERE kind LIKE 'input_%'`, you get BOTH rows.
The client peak lookup would then process both files and double-count the peak values for that client — wrong numbers.

**The fix — `DISTINCT ON`:**
```sql
SELECT DISTINCT ON (tag, run_date, kind)
    tag, run_date, kind, filename, content
FROM run_files
WHERE kind LIKE 'input_%'
ORDER BY tag, run_date, kind, ran_at DESC;
```

**How to read this:**
1. `DISTINCT ON (tag, run_date, kind)` — "for each unique combination of these three columns, keep only ONE row"
2. `ORDER BY tag, run_date, kind, ran_at DESC` — "when deciding which one row to keep, pick the one with the latest `ran_at`"
3. Result: for SA-01 / 2026-06-29 / input_c_lis, you only get `id=2` (the force re-run, ran_at=12:45)

**Audit trail is preserved:** Both rows still exist in the database. `DISTINCT ON` only filters WHAT YOU RETURN, it doesn't delete anything. The old row (id=1) is still there if you ever need to investigate what the original run downloaded.

**Important rule for `DISTINCT ON`:** The `ORDER BY` clause MUST start with the same columns listed in `DISTINCT ON`. This is a PostgreSQL requirement.

```sql
-- CORRECT
SELECT DISTINCT ON (tag, run_date, kind) ...
ORDER BY tag, run_date, kind, ran_at DESC;

-- WRONG — will throw an error
SELECT DISTINCT ON (tag, run_date, kind) ...
ORDER BY ran_at DESC;
```

---

## CHAPTER 17: INSERT vs UPSERT — Duplicates vs Overwrite

**Plain INSERT always adds a new row:**
```sql
INSERT INTO run_files (tag, run_date, kind, filename, content)
VALUES ('SA-01', '2026-06-29', 'input_c_lis', 'C_SA01...lis', <bytes>);
```
Run this twice → two rows. Our `save_run_file()` uses plain INSERT. This means:
- Force re-run = new rows added, old rows kept
- Good for audit trail
- Needs `DISTINCT ON` when reading to avoid using stale data

**UPSERT — INSERT or UPDATE if already exists:**
```sql
INSERT INTO run_files (tag, run_date, kind, filename, content)
VALUES ('SA-01', '2026-06-29', 'input_c_lis', 'C_SA01...lis', <bytes>)
ON CONFLICT (tag, run_date, kind)
DO UPDATE SET filename = EXCLUDED.filename, content = EXCLUDED.content, ran_at = NOW();
```
This would REPLACE the existing row instead of creating a new one. Simpler for reads, but you lose the history of what was there before.

**Which approach does this project use and why?**
Plain INSERT with `DISTINCT ON` on reads. Reason: we want audit history. If a re-run produces different peak numbers than the first run, both sets of files are preserved and an analyst can compare them. The `DISTINCT ON` ensures the peak lookup always uses the most recent files.

---

## CHAPTER 18: A Dangerous Bug — % in LIKE with psycopg2

This is a real bug we hit in this project. It's a classic trap.

**The broken code:**
```python
cur.execute(
    "SELECT * FROM run_files WHERE pipeline = %s AND kind LIKE 'input_%'",
    ["short_allocation"]
)
```

**Why it fails:** psycopg2 sees `%s` (pipeline placeholder) and `%'` (the `%` in `LIKE 'input_%'`). It counts 2 `%` format markers but only 1 parameter → `IndexError: list index out of range`.

**The fix — pass LIKE pattern as a parameter:**
```python
cur.execute(
    "SELECT * FROM run_files WHERE pipeline = %s AND kind LIKE %s",
    ["short_allocation", "input_%"]   # psycopg2 handles the % safely
)
```
Now psycopg2 has 2 `%s` placeholders and 2 parameters. It escapes the `%` in the LIKE pattern correctly. ✓

**Rule of thumb:** Never put `%` characters in your SQL string when using psycopg2. Always pass values (including LIKE patterns) as parameters.

---

## CHAPTER 19: Retry Logic — What To Do When the Database Hiccups

Networks are imperfect. A database write might fail because of a brief connection drop, a timeout, or a momentary overload. The solution is retry logic: try again a few times before giving up.

In this project, `_archive_to_db()` retries file uploads to Postgres up to 3 times with a 2-second pause between attempts:

```python
for attempt in range(1, max_retries + 1):    # attempt = 1, 2, 3
    try:
        save_run_file(...)
        return    # success — exit the loop immediately
    except Exception as e:
        if attempt < max_retries:
            print(f"Retry {attempt}/{max_retries}: {e} — retrying in 2s")
            time.sleep(2)           # wait before trying again
        else:
            print(f"WARNING: failed after {max_retries} attempts: {e}")
            # give up, don't raise — don't break the SA run
```

Key design decisions:
- `return` on success — breaks the loop immediately, no more attempts
- `time.sleep(2)` — a short wait often resolves temporary connection issues
- Don't `raise` at the end — a file archiving failure should NOT crash the main process

---

## THINGS TO PRACTICE

1. Run the "last 20 files archived" query and look at what's in your database
2. Count files by kind: `SELECT kind, COUNT(*) FROM run_files GROUP BY kind`
3. Check total storage used: `SELECT SUM(size_bytes)/1024/1024 AS mb FROM run_files`
4. Find all SA-01 files: `SELECT * FROM run_files WHERE tag = 'SA-01' LIMIT 10`
5. Find files from a specific date range:
   ```sql
   SELECT tag, kind, filename, run_date
   FROM run_files
   WHERE run_date BETWEEN '2026-06-01' AND '2026-06-29'
   ORDER BY run_date, tag;
   ```

---

---

## CHAPTER 20: DATE Fields and Freshness Checks

Sometimes you store a date in a file (not the database) to record WHEN something was last done. `run_state.txt` does this:

```
RUN=3
DATE=2026-06-29
```

Code-3 (LASUPTO3.py) reads this file and checks:

```python
today = datetime.today().strftime("%Y-%m-%d")   # "2026-06-29"
state_date = state.get("DATE")                    # reads from file

if state_date != today:
    print("Code-2 not executed today. Aborting.")
    sys.exit(1)
```

**Why this check exists:** LAS funding is time-sensitive. If `run_state.txt` has yesterday's date, it means someone forgot to run today's SA processing. LAS should not run on stale data.

**The bug:** If Code-2 ran for a PAST date (date override), it wrote `DATE=2026-06-25`. The next day when LAS runs: `"2026-06-25" != "2026-06-29"` → aborts. Even though Code-2 actually succeeded.

**The fix in Python:**
```python
# After Code-2 succeeds, force DATE to today in run_state.txt
today_iso = datetime.date.today().isoformat()
lines = open(state_file).readlines()
with open(state_file, "w") as f:
    for line in lines:
        if line.startswith("DATE="):
            f.write(f"DATE={today_iso}\n")   # overwrite with today
        else:
            f.write(line)
```

**The lesson:** When you have a check like `stored_date == today`, make sure the stored date is always updated by the process that just ran — not left as whatever the data's own date is.

---

## CHAPTER 21: File State Machines — When to Re-download vs Just Retry

In the SA pipeline there are three possible failure points:

```
Code-1 (Download)  →  Code-2 (SA Processing)  →  Code-3 (LAS Funding)
```

Each failure type needs a DIFFERENT recovery strategy:

| Failure point | Files on disk? | Correct action |
|---|---|---|
| Code-1 failed | No (download didn't complete) | Full re-run (force) |
| Code-2 failed | YES — backup_and_cleanup didn't run | Retry Code-2 only |
| Code-2 succeeded, Code-3 failed | NO — backup_and_cleanup moved them | Retry Code-3 only |
| Code-2 succeeded, then force-rerun | NO — files are in backup | Full re-run (re-download) |

**Key insight:** `backup_and_cleanup()` runs at the END of a successful Code-2. Once it runs, the input files are GONE from the main folder. You need to re-download if you want to reprocess.

**How the dashboard knows:** It checks `code2` in the status JSON:
- `code2 = "error"` → Code-2 failed → files are probably still on disk → show "Retry SA Processing"
- `code2 = "done"` → Code-2 succeeded (files moved) → show "Retry LAS only"

---

## CHAPTER 22: JavaScript Global Variables and Session State

The dashboard stores some values in JavaScript global variables (on the `window` object):

```javascript
window._saRunDate    // date override for past runs (e.g. "2026-06-25")
window._saRunning    // true if a run is currently active
window._saActiveTag  // which SA tag is running (e.g. "SA-01")
window._saStatus     // the latest status JSON from the server
```

**Why globals can be dangerous:** If `window._saRunDate` is set to a past date and never cleared, every button click passes the wrong date to the backend. The run downloads files for `2026-06-25` instead of today.

**The fix pattern — always clean up after yourself:**
```javascript
// When a run finishes (success or error):
ev.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.type === 'done' || d.type === 'end') {
        ev.close();
        _saRunning = false;
        window._saActiveTag = null;
        window._saRunDate = null;   // ← clear the override so next click uses today
        saLoadStatus();
    }
};
```

**The lesson:** Global variables that hold "session state" (what's currently happening) must be explicitly reset when that session ends — both on success AND on error/disconnect.

---

## THINGS TO PRACTICE

1. Run the "last 20 files archived" query and look at what's in your database
2. Count files by kind: `SELECT kind, COUNT(*) FROM run_files GROUP BY kind`
3. Check total storage used: `SELECT SUM(size_bytes)/1024/1024 AS mb FROM run_files`
4. Find all SA-01 files: `SELECT * FROM run_files WHERE tag = 'SA-01' LIMIT 10`
5. Find files from a specific date range:
   ```sql
   SELECT tag, kind, filename, run_date
   FROM run_files
   WHERE run_date BETWEEN '2026-06-01' AND '2026-06-29'
   ORDER BY run_date, tag;
   ```

---

*Last updated: 2026-06-29 — Added Chapter 20 (DATE freshness checks), Chapter 21 (file state machine for retries), Chapter 22 (JavaScript global variable cleanup).*
