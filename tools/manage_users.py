r"""
manage_users.py — create and manage RMS Copilot sign-in accounts.

This is the ONLY way accounts are created, on purpose. It runs directly on the
server and needs no working UI, which makes it the break-glass path too: if
sign-in itself is broken at 09:25, whoever has server access can fix an account
here rather than being locked out by the very thing that's broken.

USAGE  (from the rms-copilot folder)
────────────────────────────────────
    python tools\manage_users.py sheet                   (load Book1.xlsx)
    python tools\manage_users.py add M0214478 "Kartavya Dhillon"
    python tools\manage_users.py add M0214478 "Kartavya Dhillon" --password Temp@1234
    python tools\manage_users.py reset M0214478
    python tools\manage_users.py disable M0214478
    python tools\manage_users.py enable  M0214478
    python tools\manage_users.py admin   M0214478   (grant admin — can Stop ANY run)
    python tools\manage_users.py unadmin M0214478   (revoke admin)
    python tools\manage_users.py list
    python tools\manage_users.py bulk employees.csv

BULK
────
`bulk` reads a 2-column CSV — employee_id,display_name — one per line, and
generates a random temporary password for each. Use it for the initial 15-20
member load, then hand each person their password.

    M0214478,Kartavya Dhillon
    M0214483,Abhishek

PASSWORDS
─────────
Stored as a bcrypt hash and never in plain text, so a forgotten password can
only be RESET, never looked up. Generated temporary passwords are printed ONCE
here — copy them before closing the window; there is no way to retrieve them
afterwards. Every account starts with must_change set, so the holder is
expected to set their own on first sign-in.
"""

import argparse
import csv
import os
import secrets
import string
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO, ".env"), override=True)
except Exception:
    pass

from core import auth  # noqa: E402


def _ensure_tables():
    """Create app_users if this is the first run — same self-healing pattern the
    rest of the schema uses, so nobody has to remember a separate setup step."""
    try:
        if os.getenv("USE_POSTGRES", "false").strip().lower() == "true":
            from core.db_postgres import setup_db
            setup_db()
        else:
            from core.db import _init_db
            _init_db()
    except Exception as e:
        print(f"⚠️  Could not verify tables: {type(e).__name__}: {e}")


def _temp_password(n=12):
    """Readable but random. Excludes look-alike characters (O/0, l/1/I) because
    these get read aloud and typed by hand."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n)) + secrets.choice("@#$%")


def cmd_add(args):
    pw = args.password or _temp_password()
    auth.upsert_user(args.employee_id, args.name, pw, must_change=True)
    print()
    print("  ✓ Account created / updated")
    print(f"    Employee ID : {args.employee_id.upper()}")
    print(f"    Name        : {args.name}")
    print(f"    Password    : {pw}")
    print()
    print("  Give this password to the holder. It is shown ONCE — it is stored")
    print("  hashed and cannot be retrieved later, only reset.")
    print()


def cmd_reset(args):
    u = auth.get_user(args.employee_id)
    if not u:
        print(f"  ✕ No account for {args.employee_id.upper()}")
        return 1
    pw = args.password or _temp_password()
    auth.upsert_user(u["employee_id"], u["display_name"], pw, must_change=True)
    print()
    print(f"  ✓ Password reset for {u['employee_id']} ({u['display_name']})")
    print(f"    New password: {pw}")
    print()


def cmd_disable(args):
    r = auth.set_active(args.employee_id, False)
    print("  ✓ Disabled" if r["ok"] else "  ✕ No such account")
    if r["ok"]:
        print("    Their past runs stay in the audit trail — only sign-in is blocked.")


def cmd_enable(args):
    r = auth.set_active(args.employee_id, True)
    print("  ✓ Enabled" if r["ok"] else "  ✕ No such account")


def cmd_admin(args):
    r = auth.set_admin(args.employee_id, True)
    if r["ok"]:
        print(f"  ✓ {args.employee_id.upper()} is now an admin — can Stop ANY run, not just their own.")
        print("    They must sign out and back in for this to take effect (it's baked")
        print("    into the signed session token, not checked against the DB per request).")
    else:
        print("  ✕ No such account")


def cmd_unadmin(args):
    r = auth.set_admin(args.employee_id, False)
    print(f"  ✓ Admin revoked for {args.employee_id.upper()}" if r["ok"] else "  ✕ No such account")


def cmd_list(_args):
    users = auth.list_users()
    if not users:
        print("\n  No accounts yet. Create one:")
        print('    python tools\\manage_users.py add MT01234 "Name Here"\n')
        return
    print()
    print(f"  {'EMPLOYEE ID':<16}{'NAME':<26}{'STATUS':<14}{'ADMIN':<7}{'LAST SIGN-IN'}")
    print(f"  {'-'*16}{'-'*26}{'-'*14}{'-'*7}{'-'*20}")
    for u in users:
        status = "active" if u["is_active"] else "DISABLED"
        if u["is_active"] and u["must_change"]:
            status = "must change"
        print(f"  {u['employee_id']:<16}{(u['display_name'] or '')[:24]:<26}"
              f"{status:<14}{('yes' if u.get('is_admin') else ''):<7}{u['last_login_at'] or '—'}")
    print(f"\n  {len(users)} account(s)\n")


def cmd_bulk(args):
    if not os.path.isfile(args.csvfile):
        print(f"  ✕ File not found: {args.csvfile}")
        return 1
    made = []
    with open(args.csvfile, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip() or row[0].strip().startswith("#"):
                continue
            eid = row[0].strip()
            name = (row[1].strip() if len(row) > 1 else eid)
            pw = _temp_password()
            auth.upsert_user(eid, name, pw, must_change=True)
            made.append((eid.upper(), name, pw))
    print()
    print(f"  {'EMPLOYEE ID':<16}{'NAME':<26}{'TEMPORARY PASSWORD'}")
    print(f"  {'-'*16}{'-'*26}{'-'*22}")
    for eid, name, pw in made:
        print(f"  {eid:<16}{name[:24]:<26}{pw}")
    print(f"\n  {len(made)} account(s) created.")
    print("  These passwords are shown ONCE — copy this list before closing.\n")


def cmd_sheet(args):
    """Create accounts for everyone listed in the roster sheet (Book1.xlsx)."""
    from core import user_sheet
    roster = user_sheet.read_roster()
    if not roster:
        print(f"\n  ✕ No rows found in {user_sheet.sheet_path()}")
        print("    Expected a header row with EMPLOYEE ID and PASSWORD columns.\n")
        return 1
    print(f"\n  Roster: {user_sheet.sheet_path()}")
    print(f"  {len(roster)} row(s) found\n")
    res = user_sheet.sync_to_db(args.password)
    for eid in res["created"]:
        who = next((r["display_name"] for r in roster if r["employee_id"] == eid), eid)
        print(f"  ✓ created  {eid:<12} {who}")
    for eid in res["skipped"]:
        print(f"  · exists   {eid:<12} (left untouched — password NOT reset)")
    for eid, old, new in res.get("renamed", []):
        print(f"  ~ renamed  {eid:<12} {old!r} -> {new!r}")
    renamed_n = len(res.get("renamed", []))
    tail = f", {renamed_n} renamed" if renamed_n else ""
    print(f"\n  {len(res['created'])} created, {len(res['skipped'])} already existed{tail}.")
    if res["created"]:
        print(f"  Default password for the new accounts: {res['default_password']}")
        print("  Everyone is prompted to set their own on first sign-in.\n")
    else:
        print()


def main():
    ap = argparse.ArgumentParser(description="Manage RMS Copilot sign-in accounts.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="create or update an account")
    a.add_argument("employee_id"); a.add_argument("name")
    a.add_argument("--password", help="set a specific password (default: generated)")
    a.set_defaults(fn=cmd_add)

    r = sub.add_parser("reset", help="reset someone's password")
    r.add_argument("employee_id")
    r.add_argument("--password", help="set a specific password (default: generated)")
    r.set_defaults(fn=cmd_reset)

    d = sub.add_parser("disable", help="block sign-in (e.g. a leaver)")
    d.add_argument("employee_id"); d.set_defaults(fn=cmd_disable)

    e = sub.add_parser("enable", help="re-enable a disabled account")
    e.add_argument("employee_id"); e.set_defaults(fn=cmd_enable)

    ga = sub.add_parser("admin", help="grant admin — can Stop ANY run, not just their own")
    ga.add_argument("employee_id"); ga.set_defaults(fn=cmd_admin)

    ua = sub.add_parser("unadmin", help="revoke admin")
    ua.add_argument("employee_id"); ua.set_defaults(fn=cmd_unadmin)

    l = sub.add_parser("list", help="show all accounts")
    l.set_defaults(fn=cmd_list)

    b = sub.add_parser("bulk", help="create many accounts from a CSV")
    b.add_argument("csvfile"); b.set_defaults(fn=cmd_bulk)

    sh = sub.add_parser("sheet", help="create accounts from the roster sheet (Book1.xlsx)")
    sh.add_argument("--password", help="default password (default: master123)")
    sh.set_defaults(fn=cmd_sheet)

    args = ap.parse_args()
    _ensure_tables()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
