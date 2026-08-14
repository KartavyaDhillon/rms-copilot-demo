"""
core/db.py — RMS History Database (SQLite)
==========================================
Every time a pipeline run completes, call record_run() to snapshot
the key metrics into a local SQLite file (rms_history.db).

This gives you HISTORICAL SQL queries the flat-file approach can't answer:
  - "How many CTCL shortfalls did we have on 15 June vs today?"
  - "Which clients have been in shortfall 3+ days running?"
  - "Show me FIXTAR0001's margin warning trend this week."

All queries run in Python with sqlite3 (stdlib — zero install needed).
The db.py module is also a standalone script you can run to inspect the DB:
    python core/db.py summary
    python core/db.py history ctcl 7
    python core/db.py trend FIXTAR0001

WHY SQLITE (not Postgres/MySQL):
  - Zero setup: one .db file, no server, no service
  - Survives a reboot automatically
  - sqlite3 is Python stdlib — no pip install
  - Can hold years of daily RMS snapshots comfortably (<10 MB/year)
  - You can open rms_history.db in DB Browser for SQLite to explore visually
  - When (if) you need a real DB server, the SQL you learn here transfers exactly
"""

import os
import sqlite3
import pandas as pd
from datetime import datetime, date

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO, "rms_history.db")


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
# Three tables:
#   pipeline_runs   — one row per pipeline execution (summary)
#   ctcl_snapshots  — per-client CTCL data at each run (for trend queries)
#   margin_snapshots— per-client 15-min margin data at each run
#
# SQL DATA TYPES used here:
#   INTEGER  — whole numbers (run_id, shortfall_count)
#   REAL     — decimal numbers (amounts in rupees)
#   TEXT     — strings (ACCOUNTID, pipeline name, date)
#   BOOLEAN  — stored as INTEGER 0/1 in SQLite

_CREATE_SQL = r"""
-- pipeline_runs: one row every time a pipeline completes successfully
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,  -- auto-incrementing unique ID
    pipeline        TEXT    NOT NULL,                   -- 'ctcl', 'margin_shortage', etc.
    run_date        TEXT    NOT NULL,                   -- 'YYYY-MM-DD'
    run_number      INTEGER DEFAULT 1,                  -- 1-5 intraday run counter
    ran_at          TEXT    NOT NULL,                   -- full timestamp 'YYYY-MM-DD HH:MM:SS'
    shortfall_count INTEGER DEFAULT 0,                  -- over-limit rows: CTCL 'LIMIT EXCESS' / MS15 'SHORTFALL'
    virtual_count   INTEGER DEFAULT 0,                  -- how many VIRTUAL ID rows
    ok_count        INTEGER DEFAULT 0,                  -- how many OK rows
    total_rows      INTEGER DEFAULT 0,                  -- total rows in output
    biggest_shortfall_acct TEXT,                        -- account with highest shortfall
    biggest_shortfall_amt  REAL,                        -- that shortfall amount (₹)
    whatsapp_eligible      INTEGER DEFAULT 0,           -- clients with MARGIN WARNING % > 105
    source_file     TEXT                                -- the actual .xlsx file read
);

-- ctcl_snapshots: per-client CTCL row captured at each run
-- Lets you ask: "which clients have been in shortfall every day this week?"
CREATE TABLE IF NOT EXISTS ctcl_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES pipeline_runs(id),  -- links to the run above
    run_date        TEXT    NOT NULL,
    accountid       TEXT    NOT NULL,
    zone            TEXT,
    cpcode          TEXT,                               -- blank = house, value = custodian
    deposit_nmass   REAL,                               -- collateral in NMASS system
    deposit_ctcl    REAL,                               -- CTCL trading limit
    difference_gf   REAL,                               -- CTCL>=0: CTCL-NMASS (positive = LIMIT EXCESS); CTCL<0: CTCL+NMASS (informational)
    remarks         TEXT                                -- CPCODE / VIRTUAL ID / LIMIT EXCESS / OK
);

-- margin_snapshots: per-client 15-min margin row captured at each run
CREATE TABLE IF NOT EXISTS margin_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES pipeline_runs(id),
    run_date        TEXT    NOT NULL,
    accountid       TEXT    NOT NULL,
    zone            TEXT,
    deposit_nmass   REAL,
    total_margin    REAL,
    difference_fg   REAL,                               -- NMASS - margin (negative = shortfall)
    margin_warning_pct REAL,                            -- DERIVATIVE MARGIN / DEPOSIT * 100
    remarks         TEXT
);

-- margin_alert_tracker: per-client-per-day WhatsApp alert dedup state for the
-- Margin Shortage (15-minute) pipeline. Replaces the old local
-- "D:\15 minutes\margin_alert_tracker.json" file — that file only lived on
-- whichever PC last ran the pipeline, so running from a different machine
-- reset the dedup state and could re-trigger alerts. Centralizing it here
-- means any PC running margin_shortage_15min.py sees the same alert history.
-- Logic (unchanged from the original script): first alert when a client's
-- MARGIN WARNING % > 105; subsequent alerts only on a +/-10% swing from the
-- last-sent value, tracked per client per calendar day.
CREATE TABLE IF NOT EXISTS margin_alert_tracker (
    client_code     TEXT    NOT NULL,
    alert_date      TEXT    NOT NULL,                   -- 'YYYY-MM-DD'
    last_pct        REAL    NOT NULL,                   -- last MARGIN WARNING % that triggered an alert
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (client_code, alert_date)
);

-- margin_whatsapp_log: one row per WhatsApp margin alert ACTUALLY sent by the
-- Margin Shortage (15-min) pipeline, tagged with the run number it was sent in.
-- Powers the dashboard's "WhatsApp check" button (pick a date + run number(s),
-- get the list of clients who were messaged). The margin_alert_tracker table
-- above only remembers the LAST % per client per day for dedup; this table is
-- the full per-send audit trail (a client can appear multiple times in a day,
-- once per run where an alert actually went out).
CREATE TABLE IF NOT EXISTS margin_whatsapp_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT    NOT NULL,                   -- 'YYYY-MM-DD'
    run_number      INTEGER,                            -- which of the 9 intraday runs
    client_code     TEXT    NOT NULL,
    mobile          TEXT,
    margin_pct      REAL,                               -- MARGIN WARNING % at send time
    sent_at         TEXT    NOT NULL                    -- full timestamp
);

CREATE INDEX IF NOT EXISTS idx_wa_log_date_run
    ON margin_whatsapp_log(run_date, run_number);

-- run_files: the ACTUAL FILE BYTES for any pipeline run — logs, output Excel/CSV,
-- backups — stored centrally so anyone on any machine can pull the same file the
-- one person who ran it has on their local disk. Generic across all pipelines
-- (pipeline_runs/ctcl_snapshots/margin_snapshots above only ever covered
-- ctcl/margin_shortage; this table is the multi-user "everything is stored" piece).
CREATE TABLE IF NOT EXISTS run_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline        TEXT    NOT NULL,                   -- 'short_allocation', 'ctcl', ...
    tag             TEXT,                                -- e.g. 'SA-01' (which intraday run)
    run_date        TEXT    NOT NULL,                   -- 'YYYY-MM-DD'
    ran_at          TEXT    NOT NULL,                   -- full timestamp
    kind            TEXT    NOT NULL,                   -- 'log' / 'output' / 'backup'
    filename        TEXT    NOT NULL,                   -- original filename
    size_bytes      INTEGER DEFAULT 0,
    content         BLOB    NOT NULL                    -- the actual file bytes
);

-- app_users: who may sign in to the console. Login is by EMPLOYEE ID (issued by
-- HR — unique and unambiguous, unlike a typed name). The password itself is
-- NEVER stored, only a bcrypt hash, which cannot be reversed: a forgotten
-- password is RESET, never recovered.
CREATE TABLE IF NOT EXISTS app_users (
    employee_id     TEXT    PRIMARY KEY,
    display_name    TEXT    NOT NULL,
    password_hash   TEXT    NOT NULL,
    is_active       INTEGER DEFAULT 1,
    must_change     INTEGER DEFAULT 1,
    is_admin        INTEGER DEFAULT 0,   -- can Stop ANY run, not just their own
    created_at      TEXT    NOT NULL,
    last_login_at   TEXT
);

-- Indexes make queries fast (think of them as a sorted lookup table)
-- Without index: DB scans all rows. With index: jumps straight to matching rows.
CREATE INDEX IF NOT EXISTS idx_runs_pipeline_date
    ON pipeline_runs(pipeline, run_date);

CREATE INDEX IF NOT EXISTS idx_ctcl_date_acct
    ON ctcl_snapshots(run_date, accountid);

CREATE INDEX IF NOT EXISTS idx_margin_date_acct
    ON margin_snapshots(run_date, accountid);

CREATE INDEX IF NOT EXISTS idx_files_pipeline_tag_date
    ON run_files(pipeline, tag, run_date);
"""


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION HELPER
# ─────────────────────────────────────────────────────────────────────────────
def _conn():
    """Open (or create) the SQLite database and return a connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows behave like dicts: row["column"]
    conn.execute("PRAGMA foreign_keys = ON") # enforce the REFERENCES constraints
    conn.execute("PRAGMA journal_mode = WAL") # safer for concurrent reads
    return conn


def _init_db():
    """Create tables if they don't exist yet. Safe to call every startup."""
    with _conn() as conn:
        conn.executescript(_CREATE_SQL)
        # CREATE TABLE IF NOT EXISTS never touches an ALREADY-EXISTING table, so
        # a column added to the schema after go-live (is_admin) needs its own
        # migration here. SQLite has no "ADD COLUMN IF NOT EXISTS", so this just
        # tries the ALTER and swallows the "duplicate column" error on every
        # later startup once it has already been added.
        try:
            conn.execute("ALTER TABLE app_users ADD COLUMN is_admin INTEGER DEFAULT 0")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# WRITE — called after a successful pipeline run
# ─────────────────────────────────────────────────────────────────────────────
def _safe(v):
    """Convert numpy/pandas types to native Python so sqlite3 accepts them."""
    if v is None: return None
    try:
        import numpy as np
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, (np.bool_,)): return bool(v)
        if isinstance(v, bytes): return None   # guard against binary blobs
    except ImportError: pass
    return v


def record_run(pipeline: str, df: pd.DataFrame, run_number: int = 1,
               source_file: str = "") -> int:
    """
    Snapshot a pipeline's output DataFrame into the history DB.
    Returns the new run_id so callers can confirm it was saved.

    SQL CONCEPTS USED HERE:
      INSERT INTO table (col1, col2) VALUES (?, ?)
          The ? are placeholders — Python fills them in safely (prevents SQL injection).
      lastrowid
          After an INSERT, this gives you the auto-generated ID of the new row.
      executemany(sql, list_of_tuples)
          Inserts many rows in one efficient batch (much faster than a loop of execute).
    """
    _init_db()

    # ── Normalise column names so we can look them up case-insensitively ──────
    col = {str(c).strip().upper(): c for c in df.columns}

    def get(name, *aliases):
        """Find the first matching column, return its series or None."""
        for a in (name, *aliases):
            if a.upper() in col:
                return df[col[a.upper()]]
        return None

    remarks_s  = get("REMARKS", "REMARK")
    acct_s     = get("ACCOUNTID", "ACCOUNT ID", "CLIENT CODE")
    diff_gf_s  = get("DIFFERENCE(G-F)", "DIFFERENCE_GF")  # CTCL sign
    diff_fg_s  = get("DIFFERENCE(F-G)", "DIFFERENCE_FG")  # 15-min sign
    nmass_s    = get("DEPOSIT NMASS", "DEPOSIT_NMASS")
    ctcl_s     = get("DEPOSIT CTCL",  "DEPOSIT_CTCL")
    warn_s     = get("MARGIN WARNING %", "MARGIN WARNING PCT")
    zone_s     = get("ZONE", "Zone")
    cp_s       = get("CPCODE", "CPCode")
    margin_s   = get("TOTAL MARGIN", "TOTAL_MARGIN")

    def to_num(s):
        return pd.to_numeric(s, errors="coerce") if s is not None else None

    today_str = date.today().isoformat()
    now_str   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Aggregate metrics for the pipeline_runs summary row ──────────────────
    if remarks_s is not None:
        rem_upper = remarks_s.astype(str).str.upper()
        # "LIMIT EXCESS" is what CTCL vs NMASS now calls the over-limit case;
        # it used to say "SHORTFALL". BOTH are matched on purpose:
        #   · Margin Shortage (15-min) is a DIFFERENT pipeline that still emits
        #     SHORTFALL and shares this same archiver — matching only the new
        #     label would zero out its counts.
        #   · Rows archived before the rename still say SHORTFALL, so matching
        #     both keeps multi-day history continuous across the changeover.
        shortfall_count = int(rem_upper.str.contains("LIMIT EXCESS|SHORTFALL", regex=True).sum())
        virtual_count   = int(rem_upper.str.contains("VIRTUAL").sum())
        ok_count        = int(rem_upper.str.contains("^OK").sum())
    else:
        shortfall_count = virtual_count = ok_count = 0

    total_rows = len(df)

    # Biggest shortfall
    big_acct = big_amt = None
    diff_num = to_num(diff_gf_s if diff_gf_s is not None else diff_fg_s)
    if diff_num is not None and acct_s is not None:
        if pipeline == "margin_shortage":          # negative = worst
            idx = diff_num.idxmin()
        else:                                      # positive = worst (CTCL)
            idx = diff_num.idxmax()
        if pd.notna(idx):
            big_acct = str(acct_s.iloc[idx] if hasattr(acct_s,"iloc") else acct_s[idx])
            big_amt  = _safe(float(diff_num.iloc[idx] if hasattr(diff_num,"iloc") else diff_num[idx]))

    wa_eligible = 0
    if warn_s is not None:
        wa_eligible = int((to_num(warn_s) > 105).sum())

    # ── INSERT the summary row ────────────────────────────────────────────────
    # This is SQL: INSERT INTO table (columns) VALUES (placeholders)
    INSERT_RUN = """
        INSERT INTO pipeline_runs
            (pipeline, run_date, run_number, ran_at,
             shortfall_count, virtual_count, ok_count, total_rows,
             biggest_shortfall_acct, biggest_shortfall_amt,
             whatsapp_eligible, source_file)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with _conn() as conn:
        cur = conn.execute(INSERT_RUN, (
            pipeline, today_str, run_number, now_str,
            shortfall_count, virtual_count, ok_count, total_rows,
            big_acct, big_amt, wa_eligible, source_file
        ))
        run_id = cur.lastrowid   # the auto-generated ID of the run we just inserted

        # ── INSERT per-client rows ────────────────────────────────────────────
        if pipeline == "ctcl" and acct_s is not None:
            rows = []
            for i in range(len(df)):
                def v(s, i=i):
                    if s is None: return None
                    val = s.iloc[i] if hasattr(s,"iloc") else s[i]
                    return None if pd.isna(val) else _safe(val)
                rows.append((
                    run_id, today_str,
                    str(v(acct_s) or ""),
                    str(v(zone_s) or ""),
                    str(v(cp_s)   or ""),
                    _safe(v(to_num(nmass_s))) if nmass_s is not None else None,
                    _safe(v(to_num(ctcl_s)))  if ctcl_s  is not None else None,
                    _safe(v(to_num(diff_gf_s))) if diff_gf_s is not None else None,
                    str(v(remarks_s) or ""),
                ))
            conn.executemany("""
                INSERT INTO ctcl_snapshots
                    (run_id, run_date, accountid, zone, cpcode,
                     deposit_nmass, deposit_ctcl, difference_gf, remarks)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, rows)

        elif pipeline == "margin_shortage" and acct_s is not None:
            rows = []
            for i in range(len(df)):
                def v(s, i=i):
                    if s is None: return None
                    val = s.iloc[i] if hasattr(s,"iloc") else s[i]
                    return None if pd.isna(val) else val
                warn_num = to_num(warn_s)
                rows.append((
                    run_id, today_str,
                    str(v(acct_s) or ""),
                    str(v(zone_s) or ""),
                    v(to_num(nmass_s)) if nmass_s is not None else None,
                    v(to_num(margin_s)) if margin_s is not None else None,
                    v(to_num(diff_fg_s)) if diff_fg_s is not None else None,
                    v(warn_num) if warn_num is not None else None,
                    str(v(remarks_s) or ""),
                ))
            conn.executemany("""
                INSERT INTO margin_snapshots
                    (run_id, run_date, accountid, zone,
                     deposit_nmass, total_margin, difference_fg,
                     margin_warning_pct, remarks)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, rows)

    return run_id


# ─────────────────────────────────────────────────────────────────────────────
# run_files — generic file storage (any pipeline, any kind of file)
# ─────────────────────────────────────────────────────────────────────────────
def save_run_file(pipeline: str, kind: str, filename: str, content: bytes,
                  tag: str = None, run_date: str = None) -> int:
    """Store one file's actual bytes so any machine can retrieve it later —
    not just a path on whoever's disk happened to run the pipeline."""
    _init_db()
    run_date = run_date or date.today().isoformat()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO run_files (pipeline, tag, run_date, ran_at, kind, filename, size_bytes, content)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pipeline, tag, run_date, now_str, kind, filename, len(content), content))
        return cur.lastrowid


def list_run_files(pipeline: str = None, tag: str = None, run_date: str = None,
                   kind: str = None, days: int = 30,
                   from_date: str = None, to_date: str = None) -> list[dict]:
    """List stored files (no bytes — use get_run_file for the actual content)."""
    _init_db()
    sql = """SELECT id, pipeline, tag, run_date, ran_at, kind, filename, size_bytes
              FROM run_files WHERE run_date >= date('now', ?)"""
    params = [f"-{days} days"]
    if pipeline:   sql += " AND pipeline = ?";  params.append(pipeline)
    if tag:        sql += " AND tag = ?";        params.append(tag)
    if run_date:   sql += " AND run_date = ?";   params.append(run_date)
    if kind:       sql += " AND kind = ?";       params.append(kind)
    if from_date:  sql += " AND run_date >= ?";  params.append(from_date)
    if to_date:    sql += " AND run_date <= ?";  params.append(to_date)
    sql += " ORDER BY ran_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_run_file(file_id: int):
    """Return (filename, content_bytes) for one stored file, or None."""
    _init_db()
    with _conn() as conn:
        row = conn.execute("SELECT filename, content FROM run_files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        return None
    return row["filename"], row["content"]


def delete_run_file(file_id: int) -> bool:
    """Delete a single file record from run_files. Returns True if a row was deleted."""
    _init_db()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM run_files WHERE id = ?", (file_id,))
        conn.commit()
    return cur.rowcount > 0


# ─────────────────────────────────────────────────────────────────────────────
# MARGIN SHORTAGE (15-MIN) — WHATSAPP ALERT TRACKER
# Replaces the old load_tracker()/save_tracker() local-JSON functions from
# 15minutesfinal_whatsapp.PY. Same dedup semantics, just centralized so any
# PC running the pipeline shares the same per-client-per-day alert state.
# ─────────────────────────────────────────────────────────────────────────────
def get_margin_alert(client_code: str, alert_date: str):
    """Return the last MARGIN WARNING % that triggered a WhatsApp alert for
    this client on this date, or None if no alert has been sent yet today."""
    _init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT last_pct FROM margin_alert_tracker WHERE client_code = ? AND alert_date = ?",
            (client_code, alert_date),
        ).fetchone()
    return row["last_pct"] if row else None


def set_margin_alert(client_code: str, alert_date: str, pct: float):
    """Record that a WhatsApp alert was just sent for this client at this %,
    so the next run can apply the +/-10% swing check correctly."""
    _init_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        conn.execute(
            """INSERT INTO margin_alert_tracker (client_code, alert_date, last_pct, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(client_code, alert_date)
               DO UPDATE SET last_pct = excluded.last_pct, updated_at = excluded.updated_at""",
            (client_code, alert_date, _safe(pct), now),
        )
        conn.commit()


def log_whatsapp_send(run_date: str, run_number, client_code: str, mobile: str, pct: float):
    """Append one row to the per-send WhatsApp audit trail (powers the dashboard's
    'WhatsApp check' report). Called once per alert actually sent."""
    _init_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        conn.execute(
            """INSERT INTO margin_whatsapp_log (run_date, run_number, client_code, mobile, margin_pct, sent_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_date, _safe(run_number), client_code, mobile, _safe(pct), now),
        )
        conn.commit()


def list_whatsapp_sends(run_date: str, runs=None) -> list[dict]:
    """Return the WhatsApp sends for a date (optionally filtered to specific run
    numbers). Each row: run_number, client_code, mobile, margin_pct, sent_at."""
    _init_db()
    sql = ("SELECT run_number, client_code, mobile, margin_pct, sent_at "
           "FROM margin_whatsapp_log WHERE run_date = ?")
    params = [run_date]
    runs = [int(r) for r in (runs or []) if str(r).strip()]
    if runs:
        sql += " AND run_number IN (%s)" % ",".join("?" * len(runs))
        params += runs
    sql += " ORDER BY run_number, margin_pct DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# READ — SQL queries the agent can call
# ─────────────────────────────────────────────────────────────────────────────
def run_summary(pipeline: str = None, days: int = 7) -> list[dict]:
    """
    Recent pipeline run history.

    SQL CONCEPT — SELECT with WHERE and ORDER BY:
      SELECT col1, col2 FROM table
      WHERE condition
      ORDER BY col DESC    -- DESC = highest first, ASC = lowest first
      LIMIT N              -- only return N rows
    """
    _init_db()
    sql = """
        SELECT pipeline, run_date, run_number, ran_at,
               shortfall_count, virtual_count, ok_count, total_rows,
               biggest_shortfall_acct, biggest_shortfall_amt,
               whatsapp_eligible, source_file
        FROM pipeline_runs
        WHERE run_date >= date('now', ?)          -- 'now' minus N days
    """
    params = [f"-{days} days"]
    if pipeline:
        sql += " AND pipeline = ?"
        params.append(pipeline)
    sql += " ORDER BY ran_at DESC"

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def shortfall_trend(account_id: str, days: int = 14) -> list[dict]:
    """
    How has a specific client's CTCL shortfall changed over the last N days?

    SQL CONCEPT — JOIN:
      Two tables can be linked when they share a column.
      ctcl_snapshots.run_id matches pipeline_runs.id
      JOIN merges them so you can filter/select from both in one query.
    """
    _init_db()
    sql = """
        SELECT
            s.run_date,
            r.run_number,
            s.deposit_nmass,
            s.deposit_ctcl,
            s.difference_gf,
            s.remarks
        FROM ctcl_snapshots s
        JOIN pipeline_runs r ON r.id = s.run_id    -- link snapshots → their run
        WHERE s.accountid = ?                       -- filter to one client
          AND s.run_date >= date('now', ?)
        ORDER BY s.run_date ASC, r.run_number ASC
    """
    with _conn() as conn:
        rows = conn.execute(sql, [account_id.upper(), f"-{days} days"]).fetchall()
    return [dict(r) for r in rows]


def persistent_shortfalls(min_days: int = 3, pipeline: str = "ctcl") -> list[dict]:
    """
    Which clients have been over their limit on at least min_days distinct dates?

    CTCL vs NMASS calls this "LIMIT EXCESS" (it said "SHORTFALL" before the
    rename); Margin Shortage still says "SHORTFALL". Both are matched so this
    works for either pipeline AND spans rows archived either side of the
    changeover — otherwise the day of the rename would look like the problem
    simply stopped.

    SQL CONCEPT — GROUP BY + HAVING + COUNT(DISTINCT):
      GROUP BY accountid     — collapse all rows for the same account into one group
      COUNT(DISTINCT date)   — count how many unique dates appear in that group
      HAVING count >= N      — filter GROUPS (like WHERE but after grouping)

    This is the query impossible without a database:
    you'd have to read every day's Excel file manually.
    """
    _init_db()
    table = "ctcl_snapshots" if pipeline == "ctcl" else "margin_snapshots"
    sql = f"""
        SELECT
            accountid,
            COUNT(DISTINCT run_date)  AS days_in_shortfall,
            MIN(run_date)             AS first_seen,
            MAX(run_date)             AS last_seen
        FROM {table}
        WHERE (remarks LIKE '%LIMIT EXCESS%' OR remarks LIKE '%SHORTFALL%')
        GROUP BY accountid                         -- one row per account
        HAVING COUNT(DISTINCT run_date) >= ?       -- only accounts seen on >= N days
        ORDER BY days_in_shortfall DESC            -- worst offenders first
    """
    with _conn() as conn:
        rows = conn.execute(sql, [min_days]).fetchall()
    return [dict(r) for r in rows]


def daily_shortfall_counts(pipeline: str = "ctcl", days: int = 30) -> list[dict]:
    """
    How did the number of shortfall accounts change day by day?
    Good for spotting spikes — e.g. the day after a big market move.

    SQL CONCEPT — aggregate functions:
      SUM(CASE WHEN condition THEN 1 ELSE 0 END)
          Counts how many rows meet a condition within each group.
          Equivalent to pandas: df.groupby("date")["remarks"].apply(lambda x: (x=="SHORTFALL").sum())
    """
    _init_db()
    table = "ctcl_snapshots" if pipeline == "ctcl" else "margin_snapshots"
    sql = f"""
        SELECT
            run_date,
            SUM(CASE WHEN (remarks LIKE '%LIMIT EXCESS%' OR remarks LIKE '%SHORTFALL%')
                     THEN 1 ELSE 0 END)                                 AS shortfall_count,
            SUM(CASE WHEN remarks LIKE '%VIRTUAL%'   THEN 1 ELSE 0 END) AS virtual_count,
            COUNT(*)                                                      AS total_clients
        FROM {table}
        WHERE run_date >= date('now', ?)
        GROUP BY run_date
        ORDER BY run_date ASC
    """
    with _conn() as conn:
        rows = conn.execute(sql, [f"-{days} days"]).fetchall()
    return [dict(r) for r in rows]


def top_shortfalls_on_date(query_date: str, pipeline: str = "ctcl",
                            n: int = 10) -> list[dict]:
    """
    Top N shortfalls on a specific past date (not just today's file).
    'query_date' = 'YYYY-MM-DD'

    SQL CONCEPT — subquery:
      A SELECT inside another SELECT.
      Here: first find the run_id for that date, then get the client rows.
    """
    _init_db()
    if pipeline == "ctcl":
        sql = """
            SELECT s.accountid, s.zone, s.cpcode,
                   s.deposit_nmass, s.deposit_ctcl, s.difference_gf, s.remarks
            FROM ctcl_snapshots s
            JOIN pipeline_runs r ON r.id = s.run_id
            WHERE s.run_date = ?
              AND (s.remarks LIKE '%LIMIT EXCESS%' OR s.remarks LIKE '%SHORTFALL%')
            ORDER BY s.difference_gf DESC
            LIMIT ?
        """
    else:
        sql = """
            SELECT s.accountid, s.zone,
                   s.deposit_nmass, s.total_margin,
                   s.difference_fg, s.margin_warning_pct, s.remarks
            FROM margin_snapshots s
            JOIN pipeline_runs r ON r.id = s.run_id
            WHERE s.run_date = ?
              AND s.remarks LIKE '%SHORTFALL%'
            ORDER BY s.difference_fg ASC
            LIMIT ?
        """
    with _conn() as conn:
        rows = conn.execute(sql, [query_date, n]).fetchall()
    return [dict(r) for r in rows]


def raw_sql(query: str, params: list = None) -> list[dict]:
    """
    Run any SQL query directly. Used by the agent for ad-hoc questions.
    SAFETY: read-only — rejects any query containing INSERT/UPDATE/DELETE/DROP.
    """
    danger = ("insert", "update", "delete", "drop", "alter", "create", "truncate")
    if any(d in query.lower() for d in danger):
        raise ValueError("raw_sql is read-only. Use record_run() to write data.")
    _init_db()
    with _conn() as conn:
        rows = conn.execute(query, params or []).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE — called from pipeline runners after a successful run
# ─────────────────────────────────────────────────────────────────────────────
def snapshot_from_file(pipeline: str, filepath: str, run_number: int = 1) -> dict:
    """
    Read an output Excel/CSV and snapshot it into the DB.
    Call this from the pipeline runner after a successful run:

        from core.db import snapshot_from_file
        result = snapshot_from_file("ctcl", output_filepath, run_number)

    Returns {"run_id": N, "shortfall_count": N, ...}
    """
    try:
        if filepath.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(filepath)
        else:
            df = pd.read_csv(filepath, on_bad_lines="skip", low_memory=False)

        run_id = record_run(pipeline, df, run_number=run_number,
                            source_file=os.path.basename(filepath))
        return {"ok": True, "run_id": run_id,
                "rows": len(df), "pipeline": pipeline}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# CLI — run this file directly for quick DB inspection
# python core/db.py summary
# python core/db.py history ctcl 7
# python core/db.py trend FIXTAR0001
# python core/db.py persistent 3
# python core/db.py sql "SELECT COUNT(*) FROM ctcl_snapshots"
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import json

    def _money(v):
        if v is None: return "n/a"
        a = abs(float(v))
        s = "-" if float(v) < 0 else ""
        if a >= 1e7:  return f"{s}₹{a/1e7:.2f} cr"
        if a >= 1e5:  return f"{s}₹{a/1e5:.2f} lakh"
        return f"{s}₹{a:,.0f}"

    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"

    if cmd == "summary":
        rows = run_summary(days=30)
        if not rows:
            print("No runs recorded yet. Run a pipeline first.")
        for r in rows:
            print(f"{r['run_date']} run#{r['run_number']}  {r['pipeline']:20}"
                  f"  shortfalls={r['shortfall_count']}  rows={r['total_rows']}"
                  f"  biggest={r['biggest_shortfall_acct']} {_money(r['biggest_shortfall_amt'])}")

    elif cmd == "history":
        pipeline = sys.argv[2] if len(sys.argv) > 2 else "ctcl"
        days     = int(sys.argv[3]) if len(sys.argv) > 3 else 7
        rows = daily_shortfall_counts(pipeline, days)
        print(f"\n{pipeline.upper()} shortfall counts (last {days} days):\n")
        for r in rows:
            bar = "█" * r["shortfall_count"]
            print(f"  {r['run_date']}  {r['shortfall_count']:4} shortfalls  {bar[:40]}")

    elif cmd == "trend":
        acct = sys.argv[2].upper() if len(sys.argv) > 2 else "FIXTAR0001"
        rows = shortfall_trend(acct)
        print(f"\nCTCL trend for {acct}:\n")
        for r in rows:
            print(f"  {r['run_date']} run#{r['run_number']}"
                  f"  NMASS={_money(r['deposit_nmass'])}  CTCL={_money(r['deposit_ctcl'])}"
                  f"  diff={_money(r['difference_gf'])}  {r['remarks']}")

    elif cmd == "persistent":
        min_d = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        rows = persistent_shortfalls(min_d)
        print(f"\nClients in SHORTFALL on {min_d}+ distinct dates:\n")
        for r in rows:
            print(f"  {r['accountid']:15}  {r['days_in_shortfall']} days"
                  f"  ({r['first_seen']} → {r['last_seen']})")

    elif cmd == "sql":
        q = sys.argv[2] if len(sys.argv) > 2 else "SELECT COUNT(*) FROM pipeline_runs"
        rows = raw_sql(q)
        print(json.dumps(rows, indent=2, default=str))

    else:
        print("Commands: summary | history <pipeline> <days> | trend <ACCOUNTID>"
              " | persistent <min_days> | sql '<query>'")
