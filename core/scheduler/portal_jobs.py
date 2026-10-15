"""Portal Downloads — file jobs that are defined on screen instead of in code.

A *job* says: which file to fetch from the NSE member / common portal (segment, folder, file-name pattern with date
placeholders, which day), what to do with it afterwards (unzip, e-mail it, upload to the Kambala FTP) and what to do when
NSE has no file that day.  Every saved job is registered in the Scheduler catalogue as its own process
(key  portal_<id>), so schedules, Run Now, retries, the holiday calendar, history, files and logs all work exactly like
for the hand-written processes.

Jobs live in  data/scheduler/_portal_jobs.json  (no secrets — logins stay in Scheduler → Connections).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import shutil
import threading
import time
import zipfile
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.scheduler import catalog, connections, helpers as H, nse, paths

KEY_PREFIX = "portal_"
CATEGORY = "Portal Downloads"
DEFAULT_SIGNATURE = "Regards,\nKartavya Dhillon\nMaster Capital Services Ltd\nTel: 011-66331000 | Ext. 84"
DEFAULT_BODY = "Hi Team,\n\nPlease find attached {FILE}."
DEFAULT_NOFILE_BODY = "Hi Team,\n\nNo file is available on the NSE member portal for {DD-MM-YYYY}.\n\nFile looked for: {FILE}"

_LOCK = threading.RLock()

# ───────────────────────────── date placeholders ─────────────────────────────
TOKEN_HELP = [
    ("{member}", "your NSE member code (from the saved NSE login)"),
    ("{YYYYMMDD}", "20261013"), ("{DDMMYYYY}", "13102026"), ("{DDMMYY}", "131026"), ("{YYMMDD}", "261013"),
    ("{DD-MM-YYYY}", "13-10-2026"), ("{DD-MON-YYYY}", "13-OCT-2026"), ("{DD}", "13"), ("{MM}", "10"),
    ("{YYYY}", "2026"), ("{YY}", "26"), ("{MON}", "OCT"), ("{Mon}", "Oct"),
]
_TOKEN_RE = re.compile(r"\{([A-Za-z\-]+)\}")


def tokens_for(d: dt.date, member: str = "") -> Dict[str, str]:
    return {
        "member": member,
        "YYYYMMDD": f"{d:%Y%m%d}", "DDMMYYYY": f"{d:%d%m%Y}", "DDMMYY": f"{d:%d%m%y}", "YYMMDD": f"{d:%y%m%d}",
        "DD-MM-YYYY": f"{d:%d-%m-%Y}", "DD-MON-YYYY": f"{d:%d-%b-%Y}".upper(), "DD": f"{d:%d}", "MM": f"{d:%m}",
        "YYYY": f"{d:%Y}", "YY": f"{d:%y}", "MON": f"{d:%b}".upper(), "Mon": f"{d:%b}",
    }


def unknown_tokens(template: str, extra=()) -> List[str]:
    ok = set(tokens_for(dt.date.today())) | set(extra)
    return sorted({t for t in _TOKEN_RE.findall(template or "") if t not in ok})


def fill(template: str, toks: Dict[str, str]) -> str:
    return _TOKEN_RE.sub(lambda m: toks.get(m.group(1), m.group(0)), template or "")


def file_date(job: dict, today: Optional[dt.date] = None) -> dt.date:
    """The date that goes into the file name: today, or N trading days back (weekends + NSE holidays skipped)."""
    today = today or dt.date.today()
    n = int(job.get("days_back") or 0)
    return today if n <= 0 else H.prev_trading_day(today, n)


# ───────────────────────────── store ─────────────────────────────
def _file() -> Path:
    return paths.sched_root() / "_portal_jobs.json"


def load_jobs() -> List[dict]:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return [j for j in d if isinstance(j, dict) and j.get("id")]
    except Exception:
        return []


def _save_jobs(jobs: List[dict]):
    f = _file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def get_job(job_id: str) -> Optional[dict]:
    return next((j for j in load_jobs() if j["id"] == job_id), None)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:40] or "job"


# ───────────────────────────── column filter ─────────────────────────────
FILTER_OPS = ("eq", "neq", "in", "not_in", "contains", "not_contains", "starts", "gt", "gte", "lt", "lte")


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _vals(text: str) -> List[str]:
    return [v.strip() for v in re.split(r"[,\n;]", text or "") if v.strip()]


def _match_one(cell: str, op: str, values: List[str]) -> bool:
    c = (cell or "").strip()
    cl = c.lower()
    if op in ("eq", "neq"):                    # exact: whole value, same upper/lower case, no number tolerance
        return (c == values[0]) if op == "eq" else (c != values[0])
    if op in ("in", "not_in"):
        cn = _num(c)
        hit = any(cl == v.lower() or (cn is not None and _num(v) is not None and cn == _num(v)) for v in values)
        return hit if op == "in" else not hit
    if op == "contains":
        return any(v.lower() in cl for v in values)
    if op == "not_contains":
        return not any(v.lower() in cl for v in values)
    if op == "starts":
        return any(cl.startswith(v.lower()) for v in values)
    cn, vn = _num(c), _num(values[0]) if values else None
    if cn is None or vn is None:
        return False
    return {"gt": cn > vn, "gte": cn >= vn, "lt": cn < vn, "lte": cn <= vn}[op]


def filter_csv(src: Path, dst: Path, flt: dict, ctx) -> Tuple[int, int]:
    """Writes the header + only the rows that pass `flt` to dst.  -> (rows kept, rows read).
    Column names are matched ignoring case and spaces; a missing column stops the run with the list of real columns."""
    import csv
    raw = src.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.split("\n", 1)[0]
    delim = max((",", "|", ";", "\t"), key=first.count)
    rd = csv.reader(text.splitlines(), delimiter=delim)
    try:
        head = next(rd)
    except StopIteration:
        raise RuntimeError(f"{src.name} is empty — nothing to filter.")
    norm = lambda s: re.sub(r"\s+", "", str(s)).lower()
    pos = {norm(h): i for i, h in enumerate(head)}
    rules = []
    for c in flt["conditions"]:
        i = pos.get(norm(c["column"]))
        if i is None:
            raise RuntimeError(f"Filter column “{c['column']}” is not in {src.name}. Columns in the file: "
                               + ", ".join(h.strip() for h in head if h.strip()) + ". Fix the column name in the job (Edit).")
        rules.append((i, c["op"], [c["values"].strip()] if c["op"] in ("eq", "neq") else _vals(c["values"])))
    any_mode = flt.get("match") == "any"
    kept = total = 0
    with open(dst, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo, delimiter=delim, lineterminator="\r\n")
        w.writerow(head)
        for row in rd:
            if not row or not any(x.strip() for x in row):
                continue
            total += 1
            res = [_match_one(row[i] if i < len(row) else "", op, v) for i, op, v in rules]
            if any(res) if any_mode else all(res):
                w.writerow(row)
                kept += 1
    ctx.log(f"Filter: kept {kept:,} of {total:,} row(s) in {src.name} (" + (" OR " if any_mode else " AND ").join(
        f"{c['column']} {c['op']} {c['values']}" for c in flt["conditions"]) + f") → {dst.name}.")
    return kept, total


# ───────────────────────────── validation ─────────────────────────────
def _bool(v, default=False):
    if v is None:
        return default
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")


def clean_job(raw: dict, existing_id: str = "") -> Tuple[List[str], dict]:
    """-> (errors, normalised job)."""
    errs: List[str] = []
    r = raw or {}
    name = str(r.get("name") or "").strip()
    if not name:
        errs.append("Give the job a name.")
    elif re.search(r'[\\/:*?"<>|]', name) or name.strip(". ") != name or len(name) > 60:
        errs.append("The name cannot contain  \\ / : * ? \" < > |  and must be 60 characters or fewer.")
    segment = str(r.get("segment") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]{1,12}", segment):
        errs.append("Segment must be letters / digits, e.g. CO, FO, CM, CD, SLB.")
    folder = str(r.get("folder") or "").strip().strip("\\")
    if not folder or re.search(r"[?&#\x00-\x1f]", folder):
        errs.append("Enter the portal folder, e.g. Reports.")
    filename = str(r.get("filename") or "").strip()
    if not filename or re.search(r'[\\/:*?"<>|\x00-\x1f]', filename.replace("{", "").replace("}", "")):
        errs.append("Enter the file name (a pattern with placeholders is fine), e.g. Position_NCL_CO_0_CM_{member}_{YYYYMMDD}_F_0000.csv.gz")
    bad = unknown_tokens(filename)
    if bad:
        errs.append("Unknown placeholder in the file name: " + ", ".join("{" + b + "}" for b in bad))
    source = str(r.get("source") or "member").strip().lower()
    if source not in ("member", "common"):
        errs.append("Source must be the member portal or the common portal.")
    try:
        days_back = int(r.get("days_back") if r.get("days_back") not in (None, "") else 1)
        if not 0 <= days_back <= 10:
            raise ValueError
    except (TypeError, ValueError):
        days_back = 1
        errs.append("'Which day' must be between today and 10 trading days back.")
    try:
        wait = int(r.get("wait_minutes") or 0)
        if not 0 <= wait <= 600:
            raise ValueError
    except (TypeError, ValueError):
        wait = 0
        errs.append("'Wait for the file' must be 0 – 600 minutes.")

    e_in = r.get("email") if isinstance(r.get("email"), dict) else {}
    email = {
        "enabled": _bool(e_in.get("enabled")),
        "to": str(e_in.get("to") or "").strip(), "cc": str(e_in.get("cc") or "").strip(),
        "subject": str(e_in.get("subject") or "").strip() or "{NAME} - {DD-MM-YYYY}",
        "body": str(e_in.get("body") or "").strip() or DEFAULT_BODY,
        "signature": str(e_in.get("signature") or "").strip() or DEFAULT_SIGNATURE,
        "notify_no_file": _bool(e_in.get("notify_no_file"), True),
    }
    if email["enabled"]:
        try:
            if not H.parse_emails(email["to"]):
                errs.append("E-mail is switched on: add at least one 'To' address.")
            H.parse_emails(email["cc"])
        except Exception as ex:
            errs.append(f"E-mail addresses: {ex}")
        for fld in ("subject", "body"):
            bad = unknown_tokens(email[fld], extra=("NAME", "FILE"))
            if bad:
                errs.append(f"Unknown placeholder in the e-mail {fld}: " + ", ".join("{" + b + "}" for b in bad))
    k_in = r.get("kambala") if isinstance(r.get("kambala"), dict) else {}
    kambala = {"enabled": _bool(k_in.get("enabled")), "name": str(k_in.get("name") or "").strip()}
    if kambala["name"]:
        if re.search(r'[\\/:*?"<>|\x00-\x1f]', kambala["name"].replace("{", "").replace("}", "")):
            errs.append("The name on the Kambala FTP cannot contain  \\ / : * ? \" < > |")
        bad = unknown_tokens(kambala["name"], extra=("FILE",))
        if bad:
            errs.append("Unknown placeholder in the Kambala file name: " + ", ".join("{" + b + "}" for b in bad))

    f_in = r.get("filter") if isinstance(r.get("filter"), dict) else {}
    conds = []
    for c in (f_in.get("conditions") or []):
        if not isinstance(c, dict):
            continue
        col, op = str(c.get("column") or "").strip(), str(c.get("op") or "in").strip().lower()
        vals = str(c.get("values") or "").strip()
        if not col and not vals:
            continue                                   # an untouched blank row
        if op not in FILTER_OPS:
            errs.append(f"Filter on “{col}”: unknown condition “{op}”.")
            op = "in"
        if not col:
            errs.append("Filter: a row has values but no column name.")
        elif not vals:
            errs.append(f"Filter on “{col}”: enter the value(s) to keep.")
        elif op in ("gt", "gte", "lt", "lte") and _num(vals) is None:
            errs.append(f"Filter on “{col}”: “{vals}” is not a number.")
        conds.append({"column": col, "op": op, "values": vals})
    flt = {"enabled": _bool(f_in.get("enabled")), "keep_name": _bool(f_in.get("keep_name"), True),
           "mail_note": _bool(f_in.get("mail_note"), False), "match": "any" if str(f_in.get("match")).lower() == "any" else "all",
           "conditions": conds}
    if flt["enabled"]:
        if not conds:
            errs.append("Filter is switched on: add at least one column and value.")
        if not re.sub(r"\.(gz|zip)$", "", filename.lower()).endswith((".csv", ".txt", ".dat")):
            errs.append("Filtering works on .csv / .txt files only (the file name must end in .csv, .csv.gz, .txt …).")

    job = {
        "id": existing_id or str(r.get("id") or "").strip() or slug(name),
        "name": name, "description": str(r.get("description") or "").strip()[:300],
        "source": source, "segment": segment, "folder": folder, "filename": filename,
        "days_back": days_back, "trading_day_only": _bool(r.get("trading_day_only"), True),
        "wait_minutes": wait, "unzip": _bool(r.get("unzip"), filename.lower().endswith((".gz", ".zip"))),
        "email": email, "kambala": kambala, "filter": flt,
    }
    if not re.fullmatch(r"[a-z0-9_]{1,40}", job["id"]):
        errs.append("Bad job id.")
    return errs, job


def save_job(raw: dict, job_id: str = "") -> dict:
    """Create (job_id empty) or update.  Raises ValueError(list of messages joined)."""
    catalog.load_builtin()                       # before taking our lock (the catalogue import takes its own first)
    with _LOCK:
        jobs = load_jobs()
        errs, job = clean_job(raw, existing_id=job_id)
        if job_id and not any(j["id"] == job_id for j in jobs):
            raise KeyError("Unknown job.")
        if not job_id and any(j["id"] == job["id"] for j in jobs):
            n = 2
            while any(j["id"] == f"{job['id']}_{n}" for j in jobs):
                n += 1
            job["id"] = f"{job['id']}_{n}"
        others = [j for j in jobs if j["id"] != job["id"]]
        if job["name"] and any(j["name"].strip().lower() == job["name"].lower() for j in others):
            errs.append("Another job already has this name.")
        if job["name"]:
            clash =next((d for d in catalog.all_defs() if d.name.strip().lower() == job["name"].lower()
                          and not d.key.startswith(KEY_PREFIX)), None)
            if clash:
                errs.append(f"A built-in process is already called “{clash.name}” — choose another name.")
        if errs:
            raise ValueError("; ".join(errs))
        job["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        _save_jobs(others + [job])
    sync()
    return job


def delete_job(job_id: str):
    with _LOCK:
        jobs = load_jobs()
        if not any(j["id"] == job_id for j in jobs):
            raise KeyError("Unknown job.")
        _save_jobs([j for j in jobs if j["id"] != job_id])
    sync()


# ───────────────────────────── running a job ─────────────────────────────
def _member() -> str:
    return str(connections.require("nse", "member_code", "login_id")["member_code"]).strip()


def resolve(job: dict, today: Optional[dt.date] = None) -> dict:
    """What this job would fetch today: the date, the file name, the e-mail subject."""
    d = file_date(job, today)
    try:
        member = _member()
    except Exception:
        member = "<member>"
    toks = tokens_for(d, member)
    name = fill(job["filename"], toks)
    return {"date": d.isoformat(), "date_text": f"{d:%d-%m-%Y}", "filename": name, "member": member,
            "subject": fill(job["email"]["subject"], {**toks, "NAME": job["name"], "FILE": name}) if job.get("email") else ""}


def _final_file(job: dict, gz: Path, out: Path, ctx) -> Path:
    low = gz.name.lower()
    if job.get("unzip") and low.endswith(".gz"):
        from core.scheduler.processes import _kit as K
        p = K.gunzip(gz, out / gz.name[:-3])
        ctx.log(f"Unzipped → {p.name} ({p.stat().st_size / 1024:,.0f} KB).")
        return p
    if job.get("unzip") and low.endswith(".zip"):
        from core.scheduler.processes import _kit as K
        p = K.unzip_first(gz, out)
        ctx.log(f"Unzipped → {p.name} ({p.stat().st_size / 1024:,.0f} KB).")
        return p
    p = out / gz.name
    shutil.copy2(gz, p)
    return p


def _html_body(text: str, signature: str) -> str:
    def paras(t):
        return "".join(f"<p>{html.escape(x).replace(chr(10), '<br>')}</p>" for x in re.split(r"\n\s*\n", t.strip()) if x.strip())
    return f"<html><body>{paras(text)}<p>{html.escape(signature).replace(chr(10), '<br>')}</p></body></html>"


def run_job(job: dict, ctx) -> str:
    from core.scheduler.processes import _kit as K
    folder = job["name"]
    em, kb = job["email"], job["kambala"]
    if job.get("trading_day_only"):
        K.require_trading_day(ctx)
    to, cc = (H.parse_emails(em["to"]), H.parse_emails(em["cc"])) if em["enabled"] else ([], [])
    if em["enabled"] and H.already_sent(folder, "mail", ctx):
        raise H.Skipped(H.skip_message(folder, ["mail"], "e-mailed"))
    r = resolve(job)
    d, name = dt.date.fromisoformat(r["date"]), r["filename"]
    toks = {**tokens_for(d, r["member"]), "NAME": job["name"], "FILE": name}
    inp, out = paths.proc_dir(folder, "inputs"), paths.proc_dir(folder, "outputs")
    ctx.log(f"Step 1: file date {d:%d-%m-%Y} ("
            + ("today" if job["days_back"] <= 0 else f"{job['days_back']} trading day(s) back") + f") → looking for {name} "
            f"on the NSE {job['source']} portal ({job['segment']} › {job['folder']}).")
    try:
        # A download that arrives damaged (cut short, not a real gzip/zip) is thrown away and fetched again, 4 times.
        for bad_n in range(1, 5):
            gz = nse.download_wait(job["source"], job["segment"], job["folder"], name, inp, ctx, int(job.get("wait_minutes") or 0))
            try:
                final = _final_file(job, gz, out, ctx)
                break
            except (OSError, EOFError, zlib.error, zipfile.BadZipFile) as ex:
                ctx.log(f"[!] {gz.name} is damaged or incomplete ({type(ex).__name__}: {ex}) — deleting it and downloading again ({bad_n}/4).")
                try:
                    gz.unlink()
                except OSError:
                    pass
                if bad_n == 4:
                    raise RuntimeError(f"{name} kept arriving damaged from NSE (4 downloads). Try Run Now again in a few minutes.")
                time.sleep(5)
    except FileNotFoundError:
        ctx.log(f"NO FILE: the NSE {job['source']} portal has no {name} for {d:%d-%m-%Y}. Nothing was uploaded.")
        if em["enabled"] and em["notify_no_file"]:
            subj = fill(em["subject"], toks) + " - NO FILE"
            body = _html_body(fill(DEFAULT_NOFILE_BODY, toks), em["signature"])
            try:
                H.send_email(subj, body, to, cc, [], ctx, html=True, attempts=0)     # not marked "sent": a later run may still get the file
                ctx.log(f"“No file” e-mail sent: “{subj}”.")
            except Exception as ex:
                ctx.log(f"[!] Could not send the “no file” e-mail: {type(ex).__name__}: {ex}")
        ctx.log("This is normal on days when NSE does not publish the file. The run is marked SUCCESS.")
        return f"No file on the portal for {d:%d-%m-%Y}" + (" — “no file” e-mail sent" if em["enabled"] and em["notify_no_file"] else "")

    full = final
    done = [f"downloaded {final.name}"]
    flt = job.get("filter") or {}
    extra = ""
    if flt.get("enabled"):
        if flt.get("keep_name", True):               # same name as the original; the untouched full file is kept as <name>_FULL
            keep = full.with_name(f"{full.stem}_FULL{full.suffix}")
            full.replace(keep)
            fout = out / full.name
            kept, total = filter_csv(keep, fout, flt, ctx)
            full = keep
        else:
            fout = out / f"{full.stem}_filtered{full.suffix}"
            kept, total = filter_csv(full, fout, flt, ctx)
        final = fout
        done.append(f"filtered ({kept:,} of {total:,} rows)")
        if kept == 0:
            ctx.log("[!] No row matched the filter — the file has the header only.")
        if flt.get("mail_note"):
            extra = f"\n\nFiltered file: {kept:,} of {total:,} rows kept." + (" No row matched the filter." if kept == 0 else "")
    if em["enabled"]:
        subj = fill(em["subject"], toks)
        body = _html_body(fill(em["body"], toks) + extra, em["signature"])
        ctx.log(f"Step 2: e-mailing {final.name} to {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else "") + "…")
        H.send_email(subj, body, to, cc, [final], ctx, html=True, attempts=0)       # 0 = keep retrying on server errors
        H.mark_sent(folder, "mail")
        ctx.log(f"E-mail sent: “{subj}”.")
        done.append("e-mailed")
    up_ok = None
    if kb["enabled"]:
        send = final
        if kb["name"]:
            send = out / fill(kb["name"], toks)
            if send != final:
                shutil.copy2(final, send)
        ctx.log(f"Step 3: uploading {send.name} to the Kambala FTP…")
        up_ok = H.kambala_upload([send], ctx)
        done.append("uploaded to Kambala" if up_ok else "Kambala upload FAILED — see the log")
        if send != final:
            H.backup_files(folder, [send], ctx)
    H.backup_files(folder, [gz, full, final] if final != full else [gz, full], ctx)
    ctx.log("Done: " + ", ".join(done) + ".")
    return f"{final.name} — " + ", ".join(done[1:] or ["downloaded"]) + f" ({d:%d-%m-%Y})"


# ───────────────────────────── test download (no e-mail, no upload) ─────────────────────────────
class _TestCtx:
    cancelled = False
    trigger = "TEST"

    def __init__(self):
        self.lines: List[str] = []
        self._l = threading.Lock()

    def log(self, msg):
        with self._l:
            self.lines.append(f"[{dt.datetime.now():%H:%M:%S}] {msg}")

    def text(self):
        with self._l:
            return "\n".join(self.lines)


_TESTS: Dict[str, dict] = {}


def start_test(raw: dict) -> dict:
    """Validates the form and tries ONE download into a scratch folder.  Nothing is e-mailed, uploaded or kept."""
    probe = dict(raw or {})
    probe["email"], probe["kambala"] = {}, {}            # a test only downloads: e-mail / Kambala settings don't matter
    probe["name"] = probe.get("name") or "test"
    errs, job = clean_job(probe, existing_id=str((raw or {}).get("id") or "") or "test")
    if errs:
        raise ValueError("; ".join(errs))
    import uuid
    tid = uuid.uuid4().hex[:10]
    ctx = _TestCtx()
    rec = {"id": tid, "ctx": ctx, "done": False, "ok": None, "result": "", "started": dt.datetime.now()}
    _TESTS[tid] = rec
    for k in [k for k, v in _TESTS.items() if (dt.datetime.now() - v["started"]).total_seconds() > 3600]:
        _TESTS.pop(k, None)

    def work():
        try:
            r = resolve(job)
            scratch = paths.sched_root() / "_portal_test"
            scratch.mkdir(parents=True, exist_ok=True)
            ctx.log(f"Test download — nothing will be e-mailed or uploaded.")
            ctx.log(f"File date {r['date_text']} → {r['filename']}  ({job['source']} portal, {job['segment']} › {job['folder']})")
            p = nse.download(job["source"], job["segment"], job["folder"], r["filename"], scratch, ctx)
            if p:
                rec.update(ok=True, result=f"Found: {p.name} ({p.stat().st_size:,} bytes). The file is NOT kept — a real run saves it properly.")
                try:
                    p.unlink()
                except OSError:
                    pass
            else:
                rec.update(ok=True, found=False,
                           result=f"Reachable, but the portal has no {r['filename']} (yet). On a real run this counts as “no file”.")
        except Exception as e:
            ctx.log(f"FAILED: {type(e).__name__}: {e}")
            rec.update(ok=False, result=f"{type(e).__name__}: {e}")
        finally:
            rec["done"] = True

    threading.Thread(target=work, daemon=True, name=f"portal-test-{tid}").start()
    return {"id": tid}


def test_state(tid: str) -> Optional[dict]:
    rec = _TESTS.get(tid)
    if not rec:
        return None
    return {"id": tid, "done": rec["done"], "ok": rec["ok"], "found": rec.get("found", rec["ok"] and rec["done"]),
            "result": rec["result"], "log": rec["ctx"].text()}


# ───────────────────────────── catalogue sync ─────────────────────────────
def _make_run(job_id: str):
    def _run(params, ctx):
        job = get_job(job_id)
        if not job:
            raise RuntimeError("This portal download job has been deleted.")
        try:
            return H.logged(job["name"])(lambda p, c: run_job(job, c))(params, ctx)
        finally:
            _backup_log(job["name"])
    return _run


def _backup_log(folder: str):
    """The run log is written to <process>/logs/ by H.logged; keep a copy next to the backed-up files too."""
    try:
        logs = sorted(paths.proc_dir(folder, "logs").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            dest = paths.proc_dir(folder, "backup") / H.today_dash()
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(logs[0], dest / logs[0].name)
    except Exception:
        pass


def sync():
    """(Re)register every saved job in the catalogue; jobs that no longer exist are removed."""
    catalog.load_builtin()
    with _LOCK:
        for d in [d for d in catalog.all_defs() if d.key.startswith(KEY_PREFIX)]:
            catalog.unregister(d.key)
        for j in load_jobs():
            uses = ["nse"] + (["smtp"] if j["email"]["enabled"] else []) + (["kambala_ftp"] if j["kambala"]["enabled"] else [])
            what = ["unzip"] * bool(j.get("unzip")) + ["e-mail"] * bool(j["email"]["enabled"]) + ["Kambala upload"] * bool(j["kambala"]["enabled"])
            catalog.register(
                key=KEY_PREFIX + j["id"], name=j["name"],
                description=(j.get("description") or f"Downloads {j['filename']} from the NSE {j['source']} portal "
                             f"({j['segment']} › {j['folder']})") + (" → " + ", ".join(what) if what else "") + ".",
                run=_make_run(j["id"]), params=[],
                retry_safe=not (j["email"]["enabled"] or j["kambala"]["enabled"]), parallel_safe=False,
                category=CATEGORY, folder=j["name"], uses=tuple(uses))
