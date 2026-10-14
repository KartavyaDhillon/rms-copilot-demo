"""RMS LIMITS MAND KAMBALA — keeps the master file `RMS Limits MAND_KAMBALA.txt` up to date and sends it out.

File layout (pipe separated, first line fixed):

    RMS Limits<x>MAND                                                          <- header, kept byte-for-byte
    09110005||||||||||||||||MTM_SINGLE_LEVEL_EXP|no|||||||||||||||||||||||||||||280000
    ^code    ^16 pipes      ^CATEGORY            ^no                          ^limit (last value)

Daily (scheduled): every client's CATEGORY is compared with the CATEGORY column of today's risk report (matched on
ACCOUNTID) and changed only when different.  Codes are compared WITHOUT leading zeros — the risk report has 9110005
where the file has 09110005 — but the file always keeps its own spelling of the code.

Add codes (button on the process card): ACCOUNT ID + LIMIT (+ CATEGORY, optional → taken from the risk report) are
appended at the END of the file, then the same refresh runs.  Either way the file is e-mailed first, then uploaded to
the Kambala FTP.  The master file lives in this process's `inputs` folder (upload it once with "Upload files")."""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.scheduler import helpers as H, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "RMS LIMITS MAND KAMBALA"
MASTER = "RMS Limits MAND_KAMBALA.txt"
SIGNATURE = "Regards,\nKartavya Dhillon\nMaster Capital Services Ltd\nTel: 011-66331000 | Ext. 84"
CAT_IDX, FLAG_IDX = 16, 17           # fields: 0 = code, 1..15 empty, 16 = CATEGORY, 17 = "no", … last = limit
TAIL_PIPES = 29                       # pipes between "no" and the limit (copied from the file when it has data)


# ───────────────────────────── helpers ─────────────────────────────
def norm(code) -> str:
    """Key used only to MATCH codes: trimmed, upper case, no leading zeros (09110005 == 9110005)."""
    s = str(code or "").strip().upper().lstrip("0")
    return s


def upload_is_on() -> bool:
    """The Kambala FTP credentials has a yes/no switch for this file (default no)."""
    from core.scheduler import connections
    return str(connections.get("kambala_ftp").get("upload_enabled", "")).strip().lower() in ("yes", "y", "on", "true", "1")


def master_path() -> Path:
    """Only the file named exactly `RMS Limits MAND_KAMBALA.txt` in the inputs folder is used."""
    return paths.proc_dir(FOLDER, "inputs") / MASTER


def _pending_file() -> Path:
    return paths.proc_dir(FOLDER, "state") / "pending_clients.json"


def load_pending() -> List[dict]:
    try:
        return json.loads(_pending_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_pending(rows: List[dict]):
    f = _pending_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows), encoding="utf-8")


def clean_rows(rows) -> Tuple[List[str], List[dict]]:
    """Validates what the user typed in the Add-codes form -> (errors, clean rows)."""
    errs, out, seen = [], [], set()
    for i, r in enumerate(rows or [], 1):
        code = str((r or {}).get("account") or "").strip().upper()
        lim = str((r or {}).get("limit") or "").replace(",", "").strip()
        cat = str((r or {}).get("category") or "").strip().upper()
        if not code and not lim and not cat:
            continue
        if not re.fullmatch(r"[A-Z0-9_\-]{1,20}", code):
            errs.append(f"Row {i}: ACCOUNT ID “{code}” is not valid (letters and digits only).")
        if not re.fullmatch(r"\d{1,12}", lim) or int(lim) <= 0:
            errs.append(f"Row {i}: LIMIT “{lim}” must be a whole number greater than 0.")
        if cat and not re.fullmatch(r"[A-Z0-9_]{2,40}", cat):
            errs.append(f"Row {i}: CATEGORY “{cat}” is not valid (letters, digits and _ only).")
        if code and norm(code) in seen:
            errs.append(f"Row {i}: {code} is entered twice.")
        seen.add(norm(code))
        out.append({"account": code, "limit": str(int(lim)) if lim.isdigit() else lim, "category": cat})
    if not out and not errs:
        errs.append("Enter at least one ACCOUNT ID with its LIMIT.")
    return errs, out


def risk_categories(path: Path) -> Dict[str, str]:
    """{norm(ACCOUNTID): CATEGORY} from the risk report (last non-empty value wins)."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rd = csv.reader(f)
                head = next(rd)
                cols = {h.strip().upper(): i for i, h in enumerate(head)}
                if "ACCOUNTID" not in cols or "CATEGORY" not in cols:
                    raise ValueError(f"{path.name} has no ACCOUNTID / CATEGORY column.")
                ia, ic = cols["ACCOUNTID"], cols["CATEGORY"]
                out: Dict[str, str] = {}
                for row in rd:
                    if len(row) <= max(ia, ic):
                        continue
                    k, c = norm(row[ia]), row[ic].strip().upper()
                    if k and c:
                        out[k] = c
                return out
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not read {path.name}.")


def read_master(path: Path) -> Tuple[str, str, List[str]]:
    """-> (header line, newline style, data lines) — read as latin-1 so every byte of the header survives."""
    raw = path.read_bytes().decode("latin-1")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines[0].startswith("RMS Limits"):
        raise ValueError(f"{path.name}: the first line must be “RMS Limits MAND”.")
    return lines[0], nl, [l for l in lines[1:] if l.strip()]


def build_line(code: str, category: str, limit: str, tail: int = TAIL_PIPES) -> str:
    return code + "|" * CAT_IDX + category + "|no" + "|" * tail + str(limit)


def apply(lines: List[str], risk: Dict[str, str], add: List[dict], ctx) -> Tuple[List[str], List[str], List[str], List[str]]:
    """-> (new lines, changed, added, notes).  Raises ValueError (nothing written) when a new code has no category."""
    changed, added, notes, have = [], [], [], set()
    out: List[str] = []
    tail, tail_set = TAIL_PIPES, False
    bad = 0
    for ln in lines:
        f = ln.split("|")
        if len(f) <= FLAG_IDX + 1 or not f[0].strip():
            bad += 1
            out.append(ln)                                     # unknown shape: never touched
            continue
        if not tail_set:
            tail, tail_set = len(f) - FLAG_IDX - 1, True       # pipes between "no" and the limit, as in this file
        have.add(norm(f[0]))
        new = risk.get(norm(f[0]))
        if new and new != f[CAT_IDX]:
            changed.append(f"{f[0]}: {f[CAT_IDX] or '(blank)'} → {new}")
            f[CAT_IDX] = new
            ln = "|".join(f)
        out.append(ln)
    if bad:
        notes.append(f"{bad} line(s) with an unexpected layout were left exactly as they are.")
    missing = []
    for r in add:
        k = norm(r["account"])
        if k in have:
            notes.append(f"{r['account']} is already in the file — not added again (change its limit in the master file if needed).")
            continue
        cat = r.get("category") or risk.get(k, "")
        if not cat:
            missing.append(r["account"])
            continue
        out.append(build_line(r["account"], cat, r["limit"], tail))
        have.add(k)
        added.append(f"{r['account']}  limit {r['limit']}  {cat}" + ("" if r.get("category") else "  (category from risk report)"))
    if missing:
        raise ValueError("No CATEGORY given and not found in the risk report for: " + ", ".join(missing)
                         + ". Enter the CATEGORY and run again. Nothing was changed.")
    return out, changed, added, notes


# ───────────────────────────── the run ─────────────────────────────
def run_core(params, ctx, add: Optional[List[dict]] = None, manual_add: bool = False):
    if not manual_add:
        K.require_trading_day(ctx)
    to, cc = K.recipients(params)
    if not manual_add and H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    m = master_path()
    if not m.is_file():
        found = sorted(p.name for p in m.parent.iterdir() if p.is_file())
        raise FileNotFoundError(f"The master file “{MASTER}” is not in the {FOLDER} inputs folder (found: "
                                f"{', '.join(repr(n) for n in found) or 'nothing'}). Remove the wrong file and upload it again "
                                f"with the “Upload files” button of this process — the name must match exactly.")
    pending = load_pending()
    add = (add or []) + [r for r in pending if norm(r["account"]) not in {norm(x["account"]) for x in (add or [])}]
    ctx.log(f"Step 1/5: master file {MASTER} found; {len(add)} new code(s) to add.")

    risk_file = H.find_risk_report(ctx)
    risk = risk_categories(risk_file)
    ctx.log(f"Step 2/5: risk report read — {len(risk):,} accounts with a CATEGORY.")

    header, nl, lines = read_master(m)
    new_lines, changed, added, notes = apply(lines, risk, add, ctx)
    ctx.log(f"Step 3/5: {len(lines)} clients in the file · {len(changed)} category change(s) · {len(added)} code(s) added.")
    for c in changed:
        ctx.log(f"   CATEGORY changed  {c}")
    for a in added:
        ctx.log(f"   ADDED             {a}")
    for n in notes:
        ctx.log(f"   Note: {n}")

    # keep a copy of the master as it was, then write the new master and the file that goes out
    bdir = paths.proc_dir(FOLDER, "backup") / H.today_dash()
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(m, bdir / f"before_{dt.datetime.now():%H%M%S}_{MASTER}")
    data = (nl.join([header] + new_lines) + nl).encode("latin-1")
    tmp = m.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(m)
    _save_pending([])
    out = paths.proc_dir(FOLDER, "outputs") / MASTER
    out.write_bytes(data)

    subject = f"RMS Limits MAND - {dt.date.today():%d-%m-%Y}"
    li = "".join(f"<li>{x}</li>" for x in added) or "<li>None</li>"
    ci = "".join(f"<li>{x}</li>" for x in changed[:60]) or "<li>None</li>"
    body = ("<html><body><p>Hi Team,</p><p>Please find attached the RMS Limits MAND file for "
            f"{dt.date.today():%d-%m-%Y}.</p><p><b>New codes added ({len(added)}):</b></p><ul>{li}</ul>"
            f"<p><b>Category changes ({len(changed)}):</b></p><ul>{ci}</ul><p>{K.sig(params)}</p></body></html>")
    ctx.log(f"Step 4/5: e-mailing {MASTER} to {', '.join(to)}…")
    H.send_email(subject, body, to, cc, [out], ctx, html=True)
    H.mark_sent(FOLDER, "mail")          # today's file is out — a later SCHEDULED run skips; Run Now still works
    ctx.log(f"E-mail sent: “{subject}”.")
    up = upload_is_on()
    ok = None
    if up:
        ctx.log("Step 5/5: uploading to the Kambala FTP…")
        ok = H.kambala_upload([out], ctx)
    else:
        ctx.log("Step 5/5: Kambala upload is switched OFF (Credentials › Kambala FTP) — e-mail + file only, nothing uploaded.")
    shutil.copy2(out, bdir / MASTER) if not (bdir / MASTER).exists() else shutil.copy2(out, bdir / f"{dt.datetime.now():%H%M%S}_{MASTER}")
    ctx.log("Done: file updated, e-mailed" + (", uploaded" if ok else " (Kambala upload did NOT go through — see above)" if ok is False else " (not uploaded — upload is off)") + " and backed up.")
    return (f"{len(changed)} category change(s), {len(added)} code(s) added — e-mailed"
            + (" and uploaded to Kambala" if ok else "; Kambala upload FAILED" if ok is False else "; NOT uploaded (upload is off)"))


@H.logged(FOLDER)
def _run(params, ctx):
    return run_core(params, ctx)


# ───────────────────────────── "Add client codes" button ─────────────────────────────
class _Ctx:
    cancelled = False
    trigger = H.MANUAL

    def __init__(self):
        self.lines: List[str] = []
        self._l = threading.Lock()

    def log(self, msg):
        with self._l:
            self.lines.append(f"[{dt.datetime.now():%H:%M:%S}] {msg}")

    def text(self):
        with self._l:
            return "\n".join(self.lines)


_JOBS: Dict[str, dict] = {}


def start_add(rows, params: dict) -> dict:
    """Validates the typed codes and runs the process once in the background (adds them, e-mails, uploads if the
    Kambala switch is ON)."""
    errs, clean = clean_rows(rows)
    if errs:
        raise ValueError("; ".join(errs))
    jid = uuid.uuid4().hex[:10]
    ctx = _Ctx()
    rec = {"id": jid, "ctx": ctx, "done": False, "ok": None, "result": "", "started": dt.datetime.now()}
    for k in [k for k, v in _JOBS.items() if (dt.datetime.now() - v["started"]).total_seconds() > 3600]:
        _JOBS.pop(k, None)
    _JOBS[jid] = rec

    def work():
        try:
            rec["result"] = H.logged(FOLDER)(lambda p, c: run_core(p, c, clean, manual_add=True))(params, ctx)
            rec["ok"] = True
        except Exception as e:
            ctx.log(f"FAILED: {type(e).__name__}: {e}")
            rec.update(ok=False, result=f"{type(e).__name__}: {e}")
        finally:
            rec["done"] = True
    threading.Thread(target=work, daemon=True, name=f"rms-limits-add-{jid}").start()
    return {"id": jid}


def add_state(jid: str) -> Optional[dict]:
    rec = _JOBS.get(jid)
    if not rec:
        return None
    return {"id": jid, "done": rec["done"], "ok": rec["ok"], "result": rec["result"], "log": rec["ctx"].text()}


def _params():
    ps = K.mail_params("kartavya.dhillon@mastertrust.co.in", "", with_wait=False)
    for p in ps:
        if p.name == "signature":
            p.default = SIGNATURE
    return ps


register(
    key="rms_limits_mand_kambala",
    name="RMS LIMITS MAND KAMBALA",
    description="Daily: refreshes the CATEGORY of every client in the master file RMS Limits MAND_KAMBALA.txt from the "
                "risk report (matched on ACCOUNTID, leading zeros ignored), e-mails the file and uploads it to the Kambala "
                "FTP. The “Add client codes” button appends new codes (ACCOUNT ID, LIMIT, CATEGORY) at the end first. "
                "Upload the master file once with “Upload files”.",
    run=_run,
    params=_params(),
    retry_safe=False, parallel_safe=False, category="Reports", folder=FOLDER, uses=("smtp", "kambala_ftp"),
    upload_exts=(".txt",), upload_names=(MASTER,),
    upload_help=f"Upload the master file; it must be named exactly {MASTER}.",
)
