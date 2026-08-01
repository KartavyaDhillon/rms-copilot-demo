r"""
clear_ms15_data.py — delete EVERY Margin Shortage (15-min) row from the database.

WHY THIS EXISTS
───────────────
Until now MS15 ran from a batch file and, during development, from repeated test
runs. All of that wrote real rows into the shared database — run history, client
snapshots, alert-tracker state, WhatsApp send logs, and archived copies of the
Excel/image/log files themselves. From tomorrow MS15 goes live on the console,
and that old data is worse than useless: pulling "the backup for date X" out of
the database could hand back a file produced by a test run instead of the real
one, with no way to tell the difference. This clears the slate so that from the
first live run onward, everything in the database for MS15 is genuine.

WHAT IT TOUCHES — MARGIN SHORTAGE ONLY
──────────────────────────────────────
  margin_snapshots       ALL rows      (table exists only for MS15)
  margin_alert_tracker   ALL rows      (table exists only for MS15)
  margin_whatsapp_log    ALL rows      (table exists only for MS15)
  pipeline_runs          only WHERE pipeline = 'margin_shortage'
  run_files              only WHERE pipeline = 'margin_shortage'

WHAT IT NEVER TOUCHES
─────────────────────
  • ctcl_snapshots — CTCL vs NMASS's own table, left completely alone.
  • Every other pipeline's rows in pipeline_runs / run_files — Short Allocation,
    CTCL, CLI Prudential Limit and PROBABLE are filtered out by the WHERE clause,
    not by convention. Their data cannot be reached by this script.
  • ANY FILE ON DISK. Nothing under D:\15 minutes, its BACKUPFILES AUTOMATED
    folder, or the logs/ folder is read, moved or deleted. This is database-only.
    Your local backups stay exactly as they are.

It automatically targets whichever database the app is actually using —
PostgreSQL when USE_POSTGRES=true in .env, otherwise the local SQLite file.

USAGE
─────
From the rms-copilot folder:

    python tools\clear_ms15_data.py              # DRY RUN — counts only, deletes nothing
    python tools\clear_ms15_data.py --delete     # asks you to type DELETE, then does it
    python tools\clear_ms15_data.py --delete --yes   # no prompt (for a scheduled/batch call)

The plain form is a dry run BY DESIGN: running this script by accident, or
double-clicking it out of curiosity, can only ever show you numbers. You have to
pass --delete AND confirm before a single row is removed.
"""

import argparse
import os
import sys

# Make `core.*` importable when this is run as `python tools\clear_ms15_data.py`
# from the repo root (tools/ is a subfolder, so the parent must be on sys.path).
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO, ".env"), override=True)
except Exception:
    pass


PIPELINE = "margin_shortage"

# (table, WHERE clause or None for "whole table", human explanation)
# Order matters: pipeline_runs is deleted LAST because margin_snapshots rows
# carry a run_id REFERENCES pipeline_runs(id) foreign key. Clearing the children
# first means the parent delete can never trip the constraint.
TARGETS = [
    ("margin_snapshots",     None,  "per-client margin rows captured at each run"),
    ("margin_alert_tracker", None,  "per-client-per-day WhatsApp alert dedup state"),
    ("margin_whatsapp_log",  None,  "audit trail of WhatsApp alerts actually sent"),
    ("run_files",            "pipeline = %s", "archived Excel / image / log file bytes"),
    ("pipeline_runs",        "pipeline = %s", "one row per completed MS15 run"),
]


def _use_postgres():
    return os.getenv("USE_POSTGRES", "false").strip().lower() == "true"


def _open():
    """Return (conn, placeholder, backend_label) for whichever DB is in use."""
    if _use_postgres():
        from core.db_postgres import _conn, _pg_config
        cfg = _pg_config()
        return _conn(), "%s", f"PostgreSQL {cfg['dbname']} @ {cfg['host']}:{cfg['port']}"
    from core.db import _conn, DB_PATH
    return _conn(), "?", f"SQLite {DB_PATH}"


def _sql(clause, placeholder):
    """The WHERE clauses above are written with psycopg2's %s; SQLite wants ?."""
    if not clause:
        return "", ()
    return " WHERE " + clause.replace("%s", placeholder), (PIPELINE,)


def _count(cur, table, clause, placeholder):
    where, params = _sql(clause, placeholder)
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}{where}", params)
        row = cur.fetchone()
        return int(row[0] if not isinstance(row, dict) else list(row.values())[0])
    except Exception as e:
        # A table that doesn't exist yet is not an error — it just means this
        # install never wrote to it. Report it rather than aborting the run.
        return f"n/a ({type(e).__name__})"


def main():
    ap = argparse.ArgumentParser(description="Delete all Margin Shortage (15-min) data from the database.")
    ap.add_argument("--delete", action="store_true", help="actually delete (default is a dry run)")
    ap.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    args = ap.parse_args()

    conn, ph, backend = _open()
    print()
    print("=" * 74)
    print("  CLEAR MARGIN SHORTAGE (15-MIN) DATA")
    print("=" * 74)
    print(f"  Database : {backend}")
    print(f"  Mode     : {'DELETE' if args.delete else 'DRY RUN (nothing will be deleted)'}")
    print("=" * 74)
    print()

    cur = conn.cursor()

    counts = {}
    print(f"  {'TABLE':<24}{'ROWS':>12}   WHAT IT IS")
    print(f"  {'-' * 24}{'-' * 12}   {'-' * 30}")
    total = 0
    for table, clause, note in TARGETS:
        n = _count(cur, table, clause, ph)
        counts[table] = n
        scope = "MS15 only" if clause else "whole table"
        if isinstance(n, int):
            total += n
        print(f"  {table:<24}{str(n):>12}   {note} ({scope})")
    print(f"  {'-' * 24}{'-' * 12}")
    print(f"  {'TOTAL':<24}{total:>12}")
    print()

    if total == 0:
        print("  Nothing to delete — there is no Margin Shortage data in this database.")
        print()
        conn.close()
        return

    if not args.delete:
        print("  This was a DRY RUN. Nothing was deleted.")
        print("  To actually clear it:  python tools\\clear_ms15_data.py --delete")
        print()
        conn.close()
        return

    print("  ⚠️  This cannot be undone. Files on disk are NOT affected —")
    print("      only the copies stored inside the database.")
    print()
    if not args.yes:
        try:
            typed = input("  Type DELETE to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            typed = ""
        if typed != "DELETE":
            print("\n  Cancelled — nothing was deleted.\n")
            conn.close()
            return
        print()

    deleted = {}
    try:
        for table, clause, _note in TARGETS:
            if not isinstance(counts.get(table), int) or counts[table] == 0:
                continue
            where, params = _sql(clause, ph)
            cur.execute(f"DELETE FROM {table}{where}", params)
            deleted[table] = cur.rowcount if cur.rowcount is not None else counts[table]
            print(f"  ✓ {table:<24} {deleted[table]:>10,} rows deleted")
        conn.commit()
    except Exception as e:
        # One transaction for the whole job: a failure part-way through rolls
        # everything back rather than leaving MS15 half-cleared, which would be
        # harder to reason about than either state.
        conn.rollback()
        print(f"\n  ✕ FAILED — {type(e).__name__}: {e}")
        print("  Rolled back. Nothing was deleted.\n")
        conn.close()
        sys.exit(1)

    # PostgreSQL keeps the freed pages until a VACUUM; run_files holds file bytes
    # so this can be a meaningful amount of disk. Best-effort only.
    if _use_postgres():
        try:
            conn.set_session(autocommit=True)
            cur.execute("VACUUM (ANALYZE) run_files")
            cur.execute("VACUUM (ANALYZE) pipeline_runs")
            print("  ✓ VACUUM done — disk space reclaimed")
        except Exception:
            pass
    else:
        try:
            conn.isolation_level = None
            conn.execute("VACUUM")
            print("  ✓ VACUUM done — database file shrunk")
        except Exception:
            pass

    conn.close()
    print()
    print("=" * 74)
    print(f"  DONE — {sum(deleted.values()):,} Margin Shortage rows removed.")
    print("  Every other process (Short Allocation, CTCL, CLI, PROBABLE) untouched.")
    print("  From the next run onward this database holds live MS15 data only.")
    print("=" * 74)
    print()


if __name__ == "__main__":
    main()
