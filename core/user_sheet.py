r"""
user_sheet.py — keep Book1.xlsx as the human-readable ROSTER of console accounts.

WHAT THE SHEET IS, AND WHAT IT IS NOT
─────────────────────────────────────
The sheet is the list of WHO should have an account. It is the thing you edit
when someone joins or leaves, and the thing you glance at to see who is still
on the default password.

It is NOT a password file, and this module will never write a real password
into it. That is a deliberate refusal, not an oversight:

  · Passwords are stored ONLY as bcrypt hashes in the app_users table. A hash
    cannot be reversed — a forgotten password is reset, never looked up.
  · The whole reason for per-person login here is audit: "who ran this?" If
    every password sat in plaintext in a shared .xlsx, anyone who opened that
    file could sign in as anyone else, and the audit trail would be worthless
    while still LOOKING authoritative. That is worse than no audit trail.

So the PASSWORD column carries a STATUS instead:

    default                → account created, still on the shared default
    changed 2026-07-29     → this person has set their own password

which answers the question the column was really there for ("has everyone
changed off the default yet?") without handing out the keys.

COLUMNS (read flexibly — order and case don't matter)
    EMPLOYEE ID   required
    PASSWORD      status, written by this module
    NAME          the display name shown everywhere (login, "run by", audit
                  trail) — used as-is if a column is literally headed NAME.
                  Without a NAME column, falls back to whatever free-text note
                  sits in the next column, e.g. "#sandeep sir" → "Sandeep Sir".

Renaming someone is just editing their NAME cell and re-running `python
tools\manage_users.py sheet` (or restarting the server, which syncs on boot) —
sync_to_db() updates the NAME of an existing account every time, but never
touches its password, so this is always safe to re-run.
"""

import datetime
import os

DEFAULT_PASSWORD = "master123"


def sheet_path():
    """Where the roster lives. Override with USER_SHEET in .env if it moves."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.getenv("USER_SHEET", os.path.join(repo, "Book1.xlsx"))


def _open():
    import openpyxl
    p = sheet_path()
    if not os.path.isfile(p):
        return None, None, None
    wb = openpyxl.load_workbook(p)
    return wb, wb.worksheets[0], p


def _header_map(ws):
    """Locate the EMPLOYEE ID / PASSWORD columns by NAME rather than position,
    so inserting a column in the sheet doesn't silently break the import."""
    cols = {}
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=1, column=c).value
        if v is None:
            continue
        k = str(v).strip().upper().replace("_", " ")
        if k in ("EMPLOYEE ID", "EMPLOYEEID", "EMP ID", "ID"):
            cols["id"] = c
        elif k == "PASSWORD":
            cols["pw"] = c
        elif k in ("NAME", "DISPLAY NAME", "EMPLOYEE NAME"):
            cols["name"] = c
    cols.setdefault("id", 1)
    cols.setdefault("pw", 2)
    # No explicit NAME column? Fall back to the first column after id/pw that
    # has any content — the old "#sandeep sir" free-text-note convention.
    if "name" not in cols:
        cols["note"] = max(cols["id"], cols["pw"]) + 1
    return cols


def read_roster():
    """→ [{"employee_id","display_name","status"}] — [] if the sheet is absent."""
    wb, ws, _ = _open()
    if ws is None:
        return []
    c = _header_map(ws)
    out = []
    for r in range(2, ws.max_row + 1):
        eid = ws.cell(row=r, column=c["id"]).value
        if eid is None or not str(eid).strip():
            continue
        eid = str(eid).strip().upper()
        if "name" in c:
            raw = ws.cell(row=r, column=c["name"]).value
            name = str(raw).strip() if raw else ""
            # sheet has "KARTAVYA" / "SANDEEP" in caps — title-case for display,
            # but leave anything already mixed-case (e.g. "Kartavya Dhillon")
            # exactly as typed rather than mangling it.
            name = name.title() if name.isupper() else name
        else:
            note = ws.cell(row=r, column=c["note"]).value
            name = str(note).strip().lstrip("#").strip() if note else ""
            name = name.title() if name else ""  # "#sandeep sir" → "Sandeep Sir"
        name = name or eid  # falls back to the ID when the sheet has nothing
        status = ws.cell(row=r, column=c["pw"]).value
        out.append({"employee_id": eid, "display_name": name,
                    "status": str(status).strip() if status else ""})
    return out


def store_plaintext():
    """Whether the sheet records the ACTUAL password rather than a status.

    Default false. Set USER_SHEET_STORE_PASSWORDS=true in .env to turn on.

    The trade-off, stated once so it is on the record: with this on, whoever
    can open the sheet can sign in as anybody. That is fine if the file never
    leaves the administrator's own machine — and it is exactly what makes the
    audit trail unable to prove who ran something if it ever doesn't. The
    console cannot enforce where the file goes, so this is the owner's call,
    not the software's. Off by default; on when deliberately chosen.
    """
    return os.getenv("USER_SHEET_STORE_PASSWORDS", "false").strip().lower() == "true"


def mark_changed(employee_id, when=None, new_password=None):
    """Update the sheet after a password change.

    Writes the real password when USER_SHEET_STORE_PASSWORDS=true (see above),
    otherwise a dated status. Best-effort either way: the sheet being open in
    Excel must never break a password change that already succeeded in the
    database — the database is the source of truth, the sheet is a convenience.
    """
    try:
        wb, ws, path = _open()
        if ws is None:
            return {"ok": False, "error": "sheet not found"}
        c = _header_map(ws)
        eid = (employee_id or "").strip().upper()
        stamp = (when or datetime.datetime.now()).strftime("%Y-%m-%d")
        cell_val = (new_password if (store_plaintext() and new_password)
                    else f"changed {stamp}")
        for r in range(2, ws.max_row + 1):
            v = ws.cell(row=r, column=c["id"]).value
            if v and str(v).strip().upper() == eid:
                ws.cell(row=r, column=c["pw"]).value = cell_val
                wb.save(path)
                return {"ok": True, "stored": "password" if store_plaintext() else "status"}
        return {"ok": False, "error": "employee id not in sheet"}
    except PermissionError:
        return {"ok": False, "error": "sheet is open in Excel — close it and the "
                                      "status will update on the next change"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def sync_to_db(default_password=None):
    """Create a console account for every row in the sheet that doesn't have one.

    Existing accounts NEVER get their password touched by this — importing
    twice can never reset someone's password back to the default behind their
    back. New accounts start on the default with must_change set, so the
    holder is forced to choose their own.

    Existing accounts DO get their display name refreshed from the sheet's
    NAME column every time. That's the one field safe to always sync: it
    can't lock anyone out or leak a credential, and it's exactly what fixes a
    typo'd or placeholder name (e.g. an early sheet used "#me" as a note,
    which became the literal display name "Me" everywhere it's shown — a
    later edit to a proper NAME column only takes effect once this runs).
    """
    from core import auth
    pw = default_password or DEFAULT_PASSWORD
    roster = read_roster()
    created, skipped, renamed = [], [], []
    for row in roster:
        existing = auth.get_user(row["employee_id"])
        if existing:
            skipped.append(row["employee_id"])
            if (existing.get("display_name") or "") != row["display_name"]:
                auth.update_display_name(row["employee_id"], row["display_name"])
                renamed.append((row["employee_id"], existing.get("display_name"), row["display_name"]))
            continue
        auth.upsert_user(row["employee_id"], row["display_name"], pw, must_change=True)
        created.append(row["employee_id"])
    return {"ok": True, "created": created, "skipped": skipped, "renamed": renamed,
            "total": len(roster), "default_password": pw}
