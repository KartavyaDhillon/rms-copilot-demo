"""
core/db_postgres.py — RMS History Database (PostgreSQL)
========================================================
Drop-in replacement for core/db.py that uses PostgreSQL instead of SQLite.

WHY POSTGRES:
  - Multiple machines read/write simultaneously (SQLite breaks on concurrent writes)
  - All backup files in run_files are accessible from ANY machine via the API
  - Better for large binary storage (output Excel files, log archives)
  - Proper user/role permissions

SETUP (one time):
  1. pip install psycopg2-binary
  2. Add to .env:
       PG_HOST=localhost
       PG_PORT=5432
       PG_DB=rms_copilot
       PG_USER=postgres
       PG_PASSWORD=your_password
       USE_POSTGRES=true
  3. python core/db_postgres.py setup    ← creates all tables + indexes

SAME API as core/db.py — just change the import:
  from core.db_postgres import record_run, save_run_file, list_run_files, get_run_file

CLI COMMANDS (same as db.py):
  python core/db_postgres.py setup
  python core/db_postgres.py summary
  python core/db_postgres.py history ctcl 7
  python core/db_postgres.py trend FIXTAR0001
  python core/db_postgres.py persistent 3
  python core/db_postgres.py sql "SELECT COUNT(*) FROM pipeline_runs"

KEY DIFFERENCE FROM SQLite VERSION:
  - Placeholder is %s (not ?)
  - Connection uses psycopg2.connect() (not sqlite3.connect())
  - AUTOINCREMENT → SERIAL (or BIGSERIAL)
  - BLOB → BYTEA
  - date('now', ?) → CURRENT_DATE - INTERVAL '%s days'
"""

import os
import pandas as pd
from datetime import datetime, date

try:
    import psycopg2
    import psycopg2.extras
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — reads from .env / environment
# ─────────────────────────────────────────────────────────────────────────────
def _pg_config():
    return {
        "host":     os.getenv("PG_HOST",     "localhost"),
        "port":     int(os.getenv("PG_PORT", "5432")),
        "dbname":   os.getenv("PG_DB",       "rms_copilot"),
        "user":     os.getenv("PG_USER",     "postgres"),
        "password": os.getenv("PG_PASSWORD", ""),
    }


def _conn():
    """Open a connection to PostgreSQL."""
    if not _PG_AVAILABLE:
        raise RuntimeError(
            "psycopg2 not installed. Run: pip install psycopg2-binary"
        )
    cfg = _pg_config()
    conn = psycopg2.connect(**cfg)
    conn.autocommit = False          # we manage transactions manually
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA — PostgreSQL version
# Key differences from SQLite:
#   INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL PRIMARY KEY
#   BLOB                              → BYTEA
#   date('now', ?)                    → CURRENT_DATE - INTERVAL
# ─────────────────────────────────────────────────────────────────────────────
_CREATE_SQL = r"""
-- pipeline_runs: one row every time a pipeline completes successfully
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              BIGSERIAL PRIMARY KEY,
    pipeline        TEXT      NOT NULL,
    run_date        DATE      NOT NULL,
    run_number      INTEGER   DEFAULT 1,
    ran_at          TIMESTAMP NOT NULL,
    shortfall_count INTEGER   DEFAULT 0,
    virtual_count   INTEGER   DEFAULT 0,
    ok_count        INTEGER   DEFAULT 0,
    total_rows      INTEGER   DEFAULT 0,
    biggest_shortfall_acct TEXT,
    biggest_shortfall_amt  DOUBLE PRECISION,
    whatsapp_eligible      INTEGER DEFAULT 0,
    source_file     TEXT
);

-- ctcl_snapshots: per-client CTCL row captured at each run
CREATE TABLE IF NOT EXISTS ctcl_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT REFERENCES pipeline_runs(id),
    run_date        DATE      NOT NULL,
    accountid       TEXT      NOT NULL,
    zone            TEXT,
    cpcode          TEXT,
    deposit_nmass   DOUBLE PRECISION,
    deposit_ctcl    DOUBLE PRECISION,
    difference_gf   DOUBLE PRECISION,
    remarks         TEXT
);

-- margin_snapshots: per-client 15-min margin row captured at each run
CREATE TABLE IF NOT EXISTS margin_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT REFERENCES pipeline_runs(id),
    run_date        DATE      NOT NULL,
    accountid       TEXT      NOT NULL,
    zone            TEXT,
    deposit_nmass   DOUBLE PRECISION,
    total_margin    DOUBLE PRECISION,
    difference_fg   DOUBLE PRECISION,
    margin_warning_pct DOUBLE PRECISION,
    remarks         TEXT
);

-- margin_alert_tracker: per-client-per-day WhatsApp alert dedup state for the
-- Margin Shortage (15-minute) pipeline. Replaces the old local
-- "D:\15 minutes\margin_alert_tracker.json" file so the dedup state is shared
-- across any PC running the pipeline, not reset when run from a different machine.
CREATE TABLE IF NOT EXISTS margin_alert_tracker (
    client_code     TEXT      NOT NULL,
    alert_date      DATE      NOT NULL,
    last_pct        DOUBLE PRECISION NOT NULL,
    updated_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (client_code, alert_date)
);

-- margin_whatsapp_log: one row per WhatsApp margin alert ACTUALLY sent by the
-- Margin Shortage (15-min) pipeline, tagged with its run number. Powers the
-- dashboard's "WhatsApp check" button. Full per-send audit trail (a client can
-- appear multiple times a day, once per run where an alert went out).
CREATE TABLE IF NOT EXISTS margin_whatsapp_log (
    id              BIGSERIAL PRIMARY KEY,
    run_date        DATE      NOT NULL,
    run_number      INTEGER,
    client_code     TEXT      NOT NULL,
    mobile          TEXT,
    margin_pct      DOUBLE PRECISION,
    sent_at         TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wa_log_date_run
    ON margin_whatsapp_log(run_date, run_number);

-- run_files: actual file bytes for any pipeline run (logs, outputs, backups)
-- Anyone on ANY machine that hits this server can pull these — not just the
-- person whose local disk has the file.
CREATE TABLE IF NOT EXISTS run_files (
    id              BIGSERIAL PRIMARY KEY,
    pipeline        TEXT      NOT NULL,
    tag             TEXT,
    run_date        DATE      NOT NULL,
    ran_at          TIMESTAMP NOT NULL,
    kind            TEXT      NOT NULL,    -- 'log' / 'output' / 'backup'
    filename        TEXT      NOT NULL,
    size_bytes      INTEGER   DEFAULT 0,
    content         BYTEA     NOT NULL     -- actual file bytes
);

-- app_users: who may sign in to the console.
-- Login is by EMPLOYEE ID (issued by HR — unique, unambiguous, and it survives
-- two people sharing a first name, which a typed display name does not).
-- The password itself is NEVER stored; only a bcrypt hash, which cannot be
-- reversed — a forgotten password is RESET, never recovered.
CREATE TABLE IF NOT EXISTS app_users (
    employee_id     TEXT      PRIMARY KEY,   -- e.g. 'MT01234'
    display_name    TEXT      NOT NULL,      -- shown in lock strips + audit trail
    password_hash   TEXT      NOT NULL,      -- bcrypt
    is_active       BOOLEAN   DEFAULT TRUE,  -- disable a leaver, keep their history
    must_change     BOOLEAN   DEFAULT TRUE,  -- force a reset on first sign-in
    is_admin        BOOLEAN   DEFAULT FALSE, -- can Stop ANY run, not just their own
    created_at      TIMESTAMP NOT NULL,
    last_login_at   TIMESTAMP
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_runs_pipeline_date
    ON pipeline_runs(pipeline, run_date);

CREATE INDEX IF NOT EXISTS idx_ctcl_date_acct
    ON ctcl_snapshots(run_date, accountid);

CREATE INDEX IF NOT EXISTS idx_margin_date_acct
    ON margin_snapshots(run_date, accountid);

CREATE INDEX IF NOT EXISTS idx_files_pipeline_tag_date
    ON run_files(pipeline, tag, run_date);
"""


def setup_db():
    """Create all tables and indexes. Safe to run multiple times (IF NOT EXISTS)."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
            # CREATE TABLE IF NOT EXISTS never touches an ALREADY-EXISTING table,
            # so a column added to the schema after go-live (is_admin) needs its
            # own migration — unlike SQLite, Postgres 9.6+ supports "IF NOT
            # EXISTS" directly on ADD COLUMN, so this is safe to run every boot.
            cur.execute("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE")
        conn.commit()
        print("✅ PostgreSQL tables created / verified.")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# HELPER — convert numpy/pandas types to native Python so psycopg2 accepts them
# ─────────────────────────────────────────────────────────────────────────────
def _safe(v):
    if v is None: return None
    try:
        import numpy as np
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, (np.bool_,)): return bool(v)
        if isinstance(v, bytes): return None
    except ImportError:
        pass
    return v


# ─────────────────────────────────────────────────────────────────────────────
# WRITE — record a pipeline run
# ─────────────────────────────────────────────────────────────────────────────
def record_run(pipeline: str, df: pd.DataFrame, run_number: int = 1,
               source_file: str = "") -> int:
    """
    Snapshot a pipeline's output DataFrame into PostgreSQL.
    Returns the new run_id.

    POSTGRES NOTE: placeholders are %s (not ? like SQLite).
    """
    # Normalise column names
    col = {str(c).strip().upper(): c for c in df.columns}

    def get(name, *aliases):
        for a in (name, *aliases):
            if a.upper() in col:
                return df[col[a.upper()]]
        return None

    remarks_s  = get("REMARKS", "REMARK")
    acct_s     = get("ACCOUNTID", "ACCOUNT ID", "CLIENT CODE")
    diff_gf_s  = get("DIFFERENCE(G-F)", "DIFFERENCE_GF")
    diff_fg_s  = get("DIFFERENCE(F-G)", "DIFFERENCE_FG")
    nmass_s    = get("DEPOSIT NMASS", "DEPOSIT_NMASS")
    ctcl_s     = get("DEPOSIT CTCL",  "DEPOSIT_CTCL")
    warn_s     = get("MARGIN WARNING %", "MARGIN WARNING PCT")
    zone_s     = get("ZONE", "Zone")
    cp_s       = get("CPCODE", "CPCode")
    margin_s   = get("TOTAL MARGIN", "TOTAL_MARGIN")

    def to_num(s):
        return pd.to_numeric(s, errors="coerce") if s is not None else None

    today_dt = date.today()
    now_dt   = datetime.now()

    # Aggregate metrics
    if remarks_s is not None:
        rem_upper = remarks_s.astype(str).str.upper()
        # CTCL vs NMASS now says "LIMIT EXCESS" where it used to say "SHORTFALL".
        # Both matched: Margin Shortage (15-min) still emits SHORTFALL and shares
        # this archiver, and rows written before the rename must stay countable
        # so multi-day history doesn't break at the changeover. Mirrors core/db.py.
        shortfall_count = int(rem_upper.str.contains("LIMIT EXCESS|SHORTFALL", regex=True).sum())
        virtual_count   = int(rem_upper.str.contains("VIRTUAL").sum())
        ok_count        = int(rem_upper.str.contains("^OK").sum())
    else:
        shortfall_count = virtual_count = ok_count = 0

    total_rows = len(df)

    big_acct = big_amt = None
    diff_num = to_num(diff_gf_s if diff_gf_s is not None else diff_fg_s)
    if diff_num is not None and acct_s is not None:
        if pipeline == "margin_shortage":
            idx = diff_num.idxmin()
        else:
            idx = diff_num.idxmax()
        if pd.notna(idx):
            big_acct = str(acct_s.iloc[idx] if hasattr(acct_s,"iloc") else acct_s[idx])
            big_amt  = _safe(float(diff_num.iloc[idx] if hasattr(diff_num,"iloc") else diff_num[idx]))

    wa_eligible = 0
    if warn_s is not None:
        wa_eligible = int((to_num(warn_s) > 105).sum())

    conn = _conn()
    try:
        with conn.cursor() as cur:
            # Insert summary row — note %s placeholders (not ? like SQLite)
            cur.execute("""
                INSERT INTO pipeline_runs
                    (pipeline, run_date, run_number, ran_at,
                     shortfall_count, virtual_count, ok_count, total_rows,
                     biggest_shortfall_acct, biggest_shortfall_amt,
                     whatsapp_eligible, source_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                pipeline, today_dt, run_number, now_dt,
                shortfall_count, virtual_count, ok_count, total_rows,
                big_acct, big_amt, wa_eligible, source_file
            ))
            run_id = cur.fetchone()[0]

            # Insert per-client rows
            if pipeline == "ctcl" and acct_s is not None:
                rows = []
                for i in range(len(df)):
                    def v(s, i=i):
                        if s is None: return None
                        val = s.iloc[i] if hasattr(s,"iloc") else s[i]
                        return None if pd.isna(val) else _safe(val)
                    rows.append((
                        run_id, today_dt,
                        str(v(acct_s) or ""),
                        str(v(zone_s) or ""),
                        str(v(cp_s)   or ""),
                        _safe(v(to_num(nmass_s))) if nmass_s is not None else None,
                        _safe(v(to_num(ctcl_s)))  if ctcl_s  is not None else None,
                        _safe(v(to_num(diff_gf_s))) if diff_gf_s is not None else None,
                        str(v(remarks_s) or ""),
                    ))
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO ctcl_snapshots
                        (run_id, run_date, accountid, zone, cpcode,
                         deposit_nmass, deposit_ctcl, difference_gf, remarks)
                    VALUES %s
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
                        run_id, today_dt,
                        str(v(acct_s) or ""),
                        str(v(zone_s) or ""),
                        v(to_num(nmass_s)) if nmass_s is not None else None,
                        v(to_num(margin_s)) if margin_s is not None else None,
                        v(to_num(diff_fg_s)) if diff_fg_s is not None else None,
                        v(warn_num) if warn_num is not None else None,
                        str(v(remarks_s) or ""),
                    ))
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO margin_snapshots
                        (run_id, run_date, accountid, zone,
                         deposit_nmass, total_margin, difference_fg,
                         margin_warning_pct, remarks)
                    VALUES %s
                """, rows)

        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# run_files — store/retrieve actual file bytes
# ─────────────────────────────────────────────────────────────────────────────
def save_run_file(pipeline: str, kind: str, filename: str, content: bytes,
                  tag: str = None, run_date: str = None) -> int:
    """Store one file's bytes in PostgreSQL. Returns the new file id."""
    run_date_dt = date.fromisoformat(run_date) if run_date else date.today()
    now_dt = datetime.now()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO run_files
                    (pipeline, tag, run_date, ran_at, kind, filename, size_bytes, content)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (pipeline, tag, run_date_dt, now_dt, kind, filename, len(content),
                  psycopg2.Binary(content)))
            file_id = cur.fetchone()[0]
        conn.commit()
        return file_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_cli_backup_rows(run_date: str = None, confirm: bool = False) -> dict:
    """Delete the Client Prudential Limit ('cli') BACKUP rows for ONE date from
    run_files (default: today). Touches ONLY pipeline='cli' AND kind='backup'
    — never logs/outputs, never any other pipeline.

    Safe by default: with confirm=False it deletes nothing and just reports
    what WOULD be deleted (count + filenames), so it can be reviewed first."""
    d = date.fromisoformat(run_date) if run_date else date.today()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, filename, tag FROM run_files "
                        "WHERE pipeline = %s AND kind = %s AND run_date = %s ORDER BY id",
                        ("cli", "backup", d))
            rows = cur.fetchall()
            files = [{"id": r[0], "filename": r[1], "tag": r[2]} for r in rows]
            if not confirm:
                conn.rollback()
                return {"ok": True, "dry_run": True, "date": d.isoformat(),
                        "would_delete": len(files), "files": files}
            cur.execute("DELETE FROM run_files WHERE pipeline = %s AND kind = %s AND run_date = %s",
                        ("cli", "backup", d))
            deleted = cur.rowcount
        conn.commit()
        return {"ok": True, "dry_run": False, "date": d.isoformat(), "deleted": deleted, "files": files}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def fix_cli_run_dates() -> dict:
    """One-off repair: every CLI row archived via backfill_date_to_db() /
    the live backup step's own save_run_file() call BEFORE run_date was
    passed explicitly got stamped with date.today() (save_run_file()'s
    default) regardless of which date the file actually belonged to — the
    real date only ever lived in `tag` ("DD-MM-YYYY" or "DD-MM-YYYY/Slot").
    This is why backfilled historical files were showing up as "today" in
    the Client Data lookup, Get Files' date range, and the merged-download
    Date column.

    Fixes it IN PLACE (UPDATE, not a new row — unlike re-running the
    backfill, which would just add duplicate correctly-dated rows on top of
    the stale ones) by re-deriving run_date from each row's own tag and
    correcting any mismatch. Safe to run more than once — a row whose
    run_date already matches its tag is left untouched."""
    import re as _re
    conn = _conn()
    checked = fixed = skipped_unparseable = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, tag, run_date FROM run_files WHERE pipeline = %s", ("cli",))
            rows = cur.fetchall()
            for file_id, tag, run_date_val in rows:
                checked += 1
                m = _re.match(r"^(\d{2})-(\d{2})-(\d{4})", tag or "")
                if not m:
                    skipped_unparseable += 1
                    continue
                dd, mm, yyyy = m.groups()
                try:
                    correct = date(int(yyyy), int(mm), int(dd))
                except Exception:
                    skipped_unparseable += 1
                    continue
                if run_date_val != correct:
                    cur.execute("UPDATE run_files SET run_date = %s WHERE id = %s", (correct, file_id))
                    fixed += 1
        conn.commit()
        return {"ok": True, "checked": checked, "fixed": fixed,
                "skipped_unparseable": skipped_unparseable}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def list_run_files(pipeline: str = None, tag: str = None, run_date: str = None,
                   kind: str = None, days: int = 30,
                   from_date: str = None, to_date: str = None) -> list:
    """List stored files (metadata only, no bytes)."""
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sql = """
                SELECT id, pipeline, tag, run_date, ran_at, kind, filename, size_bytes
                FROM run_files
                WHERE run_date >= CURRENT_DATE - INTERVAL %s
            """
            params = [f"{days} days"]
            if pipeline:
                sql += " AND pipeline = %s"
                params.append(pipeline)
            if tag:
                sql += " AND tag = %s"
                params.append(tag)
            if run_date:
                sql += " AND run_date = %s"
                params.append(run_date)
            if kind:
                sql += " AND kind = %s"
                params.append(kind)
            if from_date:
                sql += " AND run_date >= %s"
                params.append(from_date)
            if to_date:
                sql += " AND run_date <= %s"
                params.append(to_date)
            sql += " ORDER BY ran_at DESC"
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_run_file(file_id: int):
    """Return (filename, content_bytes) or None."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT filename, content FROM run_files WHERE id = %s", (file_id,))
            row = cur.fetchone()
        if not row:
            return None
        filename, content = row
        # psycopg2 returns BYTEA as memoryview — convert to bytes
        return filename, bytes(content)
    finally:
        conn.close()


def delete_run_file(file_id: int) -> bool:
    """Delete a single file record from run_files. Returns True if a row was deleted."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM run_files WHERE id = %s", (file_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_margin_alert(client_code: str, alert_date: str):
    """Return the last MARGIN WARNING % that triggered a WhatsApp alert for
    this client on this date, or None if no alert has been sent yet today."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_pct FROM margin_alert_tracker WHERE client_code = %s AND alert_date = %s",
                (client_code, alert_date),
            )
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_margin_alert(client_code: str, alert_date: str, pct: float):
    """Record that a WhatsApp alert was just sent for this client at this %,
    so the next run can apply the +/-10% swing check correctly."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO margin_alert_tracker (client_code, alert_date, last_pct, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (client_code, alert_date)
                   DO UPDATE SET last_pct = EXCLUDED.last_pct, updated_at = NOW()""",
                (client_code, alert_date, _safe(pct)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log_whatsapp_send(run_date: str, run_number, client_code: str, mobile: str, pct: float):
    """Append one row to the per-send WhatsApp audit trail (powers the dashboard's
    'WhatsApp check' report). Called once per alert actually sent."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO margin_whatsapp_log (run_date, run_number, client_code, mobile, margin_pct, sent_at)
                   VALUES (%s, %s, %s, %s, %s, NOW())""",
                (run_date, _safe(run_number), client_code, mobile, _safe(pct)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_whatsapp_sends(run_date: str, runs=None) -> list:
    """Return the WhatsApp sends for a date (optionally filtered to specific run
    numbers). Each row: run_number, client_code, mobile, margin_pct, sent_at."""
    runs = [int(r) for r in (runs or []) if str(r).strip()]
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sql = ("SELECT run_number, client_code, mobile, margin_pct, sent_at "
                   "FROM margin_whatsapp_log WHERE run_date = %s")
            params = [run_date]
            if runs:
                sql += " AND run_number IN (%s)" % ",".join(["%s"] * len(runs))
                params += runs
            sql += " ORDER BY run_number, margin_pct DESC"
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_peak_files_range(from_date: str = None, to_date: str = None) -> list:
    """
    Fetch all SA input peak files (kind LIKE 'input_%') between two ISO dates.
    Returns list of dicts: {tag, run_date, kind, filename, content (bytes)}.
    Used by client_peak_lookup() to work cross-machine.
    """
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Pass LIKE pattern as a proper %s parameter so psycopg2 never
            # misinterprets the % wildcard as a placeholder.
            params = ["short_allocation", "input_%"]
            where = ["pipeline = %s", "kind LIKE %s"]
            if from_date:
                where.append("run_date >= %s")
                params.append(from_date)
            if to_date:
                where.append("run_date <= %s")
                params.append(to_date)
            # DISTINCT ON keeps only the LATEST file per (tag, run_date, kind) slot.
            # If SA-01 was force-re-run, we get the re-run's files, not the originals.
            # Old rows are kept for audit but not used for peak lookup.
            cur.execute(
                f"SELECT DISTINCT ON (tag, run_date, kind) tag, run_date, kind, filename, content "
                f"FROM run_files WHERE {' AND '.join(where)} "
                f"ORDER BY tag, run_date, kind, ran_at DESC",
                params
            )
            rows = cur.fetchall()
        return [
            {
                "tag": r["tag"], "run_date": str(r["run_date"]),
                "kind": r["kind"], "filename": r["filename"],
                "content": bytes(r["content"]) if r["content"] else b"",
            }
            for r in rows
        ]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# READ — query functions
# ─────────────────────────────────────────────────────────────────────────────
def run_summary(pipeline: str = None, days: int = 7) -> list:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sql = """
                SELECT pipeline, run_date, run_number, ran_at,
                       shortfall_count, virtual_count, ok_count, total_rows,
                       biggest_shortfall_acct, biggest_shortfall_amt,
                       whatsapp_eligible, source_file
                FROM pipeline_runs
                WHERE run_date >= CURRENT_DATE - INTERVAL %s
            """
            params = [f"{days} days"]
            if pipeline:
                sql += " AND pipeline = %s"
                params.append(pipeline)
            sql += " ORDER BY ran_at DESC"
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def shortfall_trend(account_id: str, days: int = 14) -> list:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT s.run_date, r.run_number,
                       s.deposit_nmass, s.deposit_ctcl, s.difference_gf, s.remarks
                FROM ctcl_snapshots s
                JOIN pipeline_runs r ON r.id = s.run_id
                WHERE UPPER(s.accountid) = %s
                  AND s.run_date >= CURRENT_DATE - INTERVAL %s
                ORDER BY s.run_date ASC, r.run_number ASC
            """, (account_id.upper(), f"{days} days"))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def persistent_shortfalls(min_days: int = 3, pipeline: str = "ctcl") -> list:
    table = "ctcl_snapshots" if pipeline == "ctcl" else "margin_snapshots"
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT accountid,
                       COUNT(DISTINCT run_date) AS days_in_shortfall,
                       MIN(run_date)             AS first_seen,
                       MAX(run_date)             AS last_seen
                FROM {table}
                WHERE (remarks ILIKE '%%LIMIT EXCESS%%' OR remarks ILIKE '%%SHORTFALL%%')
                GROUP BY accountid
                HAVING COUNT(DISTINCT run_date) >= %s
                ORDER BY days_in_shortfall DESC
            """, (min_days,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def daily_shortfall_counts(pipeline: str = "ctcl", days: int = 30) -> list:
    table = "ctcl_snapshots" if pipeline == "ctcl" else "margin_snapshots"
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT run_date,
                       SUM(CASE WHEN (remarks ILIKE '%%LIMIT EXCESS%%' OR remarks ILIKE '%%SHORTFALL%%')
                                THEN 1 ELSE 0 END)                                    AS shortfall_count,
                       SUM(CASE WHEN remarks ILIKE '%%VIRTUAL%%'   THEN 1 ELSE 0 END) AS virtual_count,
                       COUNT(*) AS total_clients
                FROM {table}
                WHERE run_date >= CURRENT_DATE - INTERVAL %s
                GROUP BY run_date
                ORDER BY run_date ASC
            """, (f"{days} days",))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def top_shortfalls_on_date(query_date: str, pipeline: str = "ctcl", n: int = 10) -> list:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if pipeline == "ctcl":
                cur.execute("""
                    SELECT s.accountid, s.zone, s.cpcode,
                           s.deposit_nmass, s.deposit_ctcl, s.difference_gf, s.remarks
                    FROM ctcl_snapshots s
                    JOIN pipeline_runs r ON r.id = s.run_id
                    WHERE s.run_date = %s
                      AND (s.remarks ILIKE '%%LIMIT EXCESS%%' OR s.remarks ILIKE '%%SHORTFALL%%')
                    ORDER BY s.difference_gf DESC
                    LIMIT %s
                """, (query_date, n))
            else:
                cur.execute("""
                    SELECT s.accountid, s.zone,
                           s.deposit_nmass, s.total_margin,
                           s.difference_fg, s.margin_warning_pct, s.remarks
                    FROM margin_snapshots s
                    JOIN pipeline_runs r ON r.id = s.run_id
                    WHERE s.run_date = %s AND s.remarks ILIKE '%%SHORTFALL%%'
                    ORDER BY s.difference_fg ASC
                    LIMIT %s
                """, (query_date, n))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def raw_sql(query: str, params: list = None) -> list:
    """Run any read-only SQL query. Rejects write operations."""
    danger = ("insert", "update", "delete", "drop", "alter", "create", "truncate")
    if any(d in query.lower() for d in danger):
        raise ValueError("raw_sql is read-only.")
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or [])
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def snapshot_from_file(pipeline: str, filepath: str, run_number: int = 1) -> dict:
    """Read an output Excel/CSV and snapshot into Postgres. Same as db.py version."""
    try:
        if filepath.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(filepath)
        else:
            df = pd.read_csv(filepath, on_bad_lines="skip", low_memory=False)
        run_id = record_run(pipeline, df, run_number=run_number,
                            source_file=os.path.basename(filepath))
        return {"ok": True, "run_id": run_id, "rows": len(df), "pipeline": pipeline}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# TEST CONNECTION
# ─────────────────────────────────────────────────────────────────────────────
def test_connection() -> dict:
    """Test if Postgres is reachable with the current .env settings."""
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            ver = cur.fetchone()[0]
        conn.close()
        return {"ok": True, "version": ver, "config": {k: v for k, v in _pg_config().items() if k != "password"}}
    except Exception as e:
        return {"ok": False, "error": str(e), "config": {k: v for k, v in _pg_config().items() if k != "password"}}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, json
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

    def _money(v):
        if v is None: return "n/a"
        a = abs(float(v))
        s = "-" if float(v) < 0 else ""
        if a >= 1e7: return f"{s}₹{a/1e7:.2f} cr"
        if a >= 1e5: return f"{s}₹{a/1e5:.2f} lakh"
        return f"{s}₹{a:,.0f}"

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "setup":
        setup_db()

    elif cmd == "test":
        result = test_connection()
        if result["ok"]:
            print(f"✅ Connected to PostgreSQL")
            print(f"   Server: {result['version'][:60]}")
            print(f"   Config: {result['config']}")
        else:
            print(f"❌ Connection failed: {result['error']}")
            print(f"   Config: {result['config']}")

    elif cmd == "summary":
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
            bar = "█" * int(r["shortfall_count"])
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
        print("""
Commands:
  python core/db_postgres.py setup              — create tables in Postgres
  python core/db_postgres.py test               — test connection
  python core/db_postgres.py summary            — recent pipeline runs
  python core/db_postgres.py history ctcl 7     — shortfall counts last 7 days
  python core/db_postgres.py trend FIXTAR0001   — client shortfall trend
  python core/db_postgres.py persistent 3       — clients short 3+ days
  python core/db_postgres.py sql "SELECT ..."   — run any read-only SQL
""")
