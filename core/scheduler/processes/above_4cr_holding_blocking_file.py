"""ABOVE 4 CR HOLDING BLOCKING FILE — daily selling-block list for big holdings, from today's HOLDING file.

Selection (holding file HOLDING_<DDMMYYYY>.csv, same place as the risk report):
    PledgeQty = 0  and  RePledgeQty = 0  and  HoldingFull >= 4 Cr (40,000,000)  and  ExchVar = 100
Every selected row gives up to two lines (like the master file):
    ||||<AccountID>|S|CNC|ALL|NSE|<Symbol>|||||Y|||Selling blocked high value selling require early payin␠
    ||||<AccountID>|S|CNC|ALL|BSE|<Boscrip>|||||Y|||Selling blocked high value selling require early payin␠

The result is compared with the MASTER file (MASTER_MORE_THAN_4CR_BLOCKING_FINAL.txt, uploaded once, kept in the inputs
folder and moved forward after every run):
    • lines that are new   → MORE_THAN_4CR_BLOCKING_FINAL.txt (output: header + ONLY the new lines) — e-mailed, and
      uploaded to the Kambala FTP when the switch in Credentials › Kambala FTP is ON;
    • lines no longer qualifying → UNBLOCK CODES.csv (AccountID, Boscrip, Symbol) — e-mailed only.
Nothing new and nothing to unblock → a short "no change" e-mail and no upload."""
from __future__ import annotations

import csv
import datetime as dt
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.scheduler import helpers as H, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "ABOVE 4 CR HOLDING BLOCKING FILE"
MASTER = "MASTER_MORE_THAN_4CR_BLOCKING_FINAL.txt"      # the file you upload (inputs folder)
OUT_NAME = "MORE_THAN_4CR_BLOCKING_FINAL.txt"           # the file this process creates, e-mails and uploads to Kambala
UNBLOCK = "UNBLOCK CODES.csv"
HEADER_TEXT = "RMS Blocking/UnBlocking"
REMARK = "Selling blocked high value selling require early payin "          # note the trailing space (as in the master)
MIN_VALUE = 40_000_000                                                       # 4 Cr
SIGNATURE = "Regards,\nKartavya Dhillon\nMaster Capital Services Ltd\nTel: 011-66331000 | Ext. 84"
Key = Tuple[str, str, str]                                                   # (account, exchange, scrip)


# ───────────────────────────── helpers ─────────────────────────────
def upload_is_on() -> bool:
    from core.scheduler import connections
    return str(connections.get("kambala_ftp").get("upload_above4cr", "")).strip().lower() in ("yes", "y", "on", "true", "1")


def master_path() -> Path:
    return paths.proc_dir(FOLDER, "inputs") / MASTER


def find_holding(ctx=None) -> Path:
    """Today's HOLDING_<DDMMYYYY>.csv from COMMON FILES (or the console's dated backup copy). Read-only."""
    name = f"HOLDING_{H.today_ddmmyyyy()}.csv"
    for c in (H.common_dir() / name, H.common_dir() / "BACKUP FILES AUTOMATED" / H.today_dash() / name):
        if c.is_file():
            if ctx:
                ctx.log(f"Holding file: {c}")
            return c
    raise FileNotFoundError(f"Today's holding file ({name}) was not found in {H.common_dir()} — upload it to COMMON FILES first.")


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def line_for(acct: str, exch: str, scrip: str) -> str:
    return f"||||{acct}|S|CNC|ALL|{exch}|{scrip}|||||Y|||{REMARK}"


def select_holdings(path: Path) -> Tuple[List[Tuple[Key, str]], int]:
    """-> ([(key, line)] without duplicates in file order, number of qualifying holding rows)."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rd = csv.reader(f)
                head = next(rd)
                col = {h.strip().lower(): i for i, h in enumerate(head)}
                need = ["accountid", "boscrip", "symbol", "pledgeqty", "repledgeqty", "holdingfull", "exchvar"]
                miss = [n for n in need if n not in col]
                if miss:
                    raise ValueError(f"{path.name}: column(s) not found: {', '.join(miss)}")
                i = {n: col[n] for n in need}
                top = max(i.values())
                seen, out, rows = set(), [], 0
                for r in rd:
                    if len(r) <= top:
                        continue
                    if _num(r[i["pledgeqty"]]) != 0 or _num(r[i["repledgeqty"]]) != 0:
                        continue
                    hv, ev = _num(r[i["holdingfull"]]), _num(r[i["exchvar"]])
                    if hv is None or ev is None or hv < MIN_VALUE or ev != 100:
                        continue
                    acct = r[i["accountid"]].strip().upper()
                    if not acct:
                        continue
                    rows += 1
                    for exch, scrip in (("NSE", r[i["symbol"]].strip().upper()), ("BSE", r[i["boscrip"]].strip().upper())):
                        if scrip and (acct, exch, scrip) not in seen:
                            seen.add((acct, exch, scrip))
                            out.append(((acct, exch, scrip), line_for(acct, exch, scrip)))
                return out, rows
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not read {path.name}.")


def read_master(path: Path) -> Tuple[str, str, List[str]]:
    raw = path.read_bytes().decode("latin-1")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines[0].startswith("RMS Blocking"):
        raise ValueError(f"{path.name}: the first line must be “{HEADER_TEXT}”.")
    return lines[0], nl, [l for l in lines[1:] if l.strip()]


def key_of(line: str) -> Optional[Key]:
    f = line.split("|")
    if len(f) < 10 or not f[4].strip() or f[8].strip() not in ("NSE", "BSE") or not f[9].strip():
        return None
    return f[4].strip().upper(), f[8].strip(), f[9].strip().upper()


def compare(master_lines: List[str], today: List[Tuple[Key, str]]):
    """-> (new [(key,line)], removed [key], kept master lines (still qualifying, unparsable lines too))."""
    tkeys = {k for k, _ in today}
    mkeys = {k for k in (key_of(l) for l in master_lines) if k}
    new = [(k, l) for k, l in today if k not in mkeys]
    removed, kept, seen_removed, seen_kept = [], [], set(), set()
    for l in master_lines:
        k = key_of(l)
        if k is None:
            kept.append(l)
        elif k in tkeys:
            if k not in seen_kept:                         # a repeated line in the master is kept only once
                seen_kept.add(k)
                kept.append(l)
        elif k not in seen_removed:
            seen_removed.add(k)
            removed.append(k)
    return new, removed, kept


def unblock_csv(removed: List[Key]) -> str:
    rows = ["AccountID,Boscrip,Symbol"]
    for acct, exch, scrip in removed:
        rows.append(f"{acct},{scrip if exch == 'BSE' else ''},{scrip if exch == 'NSE' else ''}")
    return "\r\n".join(rows) + "\r\n"


# ───────────────────────────── the run ─────────────────────────────
@H.logged(FOLDER)
def _run(params, ctx):
    K.require_trading_day(ctx)
    to, cc = K.recipients(params)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    m = master_path()
    if not m.is_file():
        found = sorted(p.name for p in m.parent.iterdir() if p.is_file())
        raise FileNotFoundError(f"The master file “{MASTER}” is not in the {FOLDER} inputs folder (found: "
                                f"{', '.join(repr(n) for n in found) or 'nothing'}). Upload it with the “Upload files” button "
                                f"of this process first — the name must match exactly.")
    ctx.log(f"Step 1/6: master file {MASTER} found.")

    today, rows = select_holdings(find_holding(ctx))
    ctx.log(f"Step 2/6: {rows:,} holding row(s) qualify (Pledge 0, RePledge 0, HoldingFull ≥ 4 Cr, ExchVar 100) → "
            f"{len(today):,} NSE/BSE line(s).")

    header, nl, mlines = read_master(m)
    if not today:
        ctx.log("No holding qualifies today → nothing to block; every code left in the master file will be unblocked.")
    new, removed, kept = compare(mlines, today)
    ctx.log(f"Step 3/6: compared with the master ({len(mlines):,} lines): {len(new)} NEW to block, {len(removed)} to UNBLOCK, "
            f"{len(kept):,} unchanged.")
    for k, _ in new:
        ctx.log(f"   BLOCK    {k[0]} | {k[1]} | {k[2]}")
    for k in removed:
        ctx.log(f"   UNBLOCK  {k[0]} | {k[1]} | {k[2]}")

    out_dir = paths.proc_dir(FOLDER, "outputs")
    block_f, unblock_f = out_dir / OUT_NAME, out_dir / UNBLOCK
    for f in (block_f, unblock_f):
        if f.exists():
            f.unlink()
    files: List[Path] = []
    if new:
        block_f.write_bytes((nl.join([header] + [l for _, l in new]) + nl).encode("latin-1"))
        files.append(block_f)
    if removed:
        unblock_f.write_bytes(unblock_csv(removed).encode("latin-1"))
        files.append(unblock_f)

    d = dt.date.today()
    nochange = not files
    subject = f"More than 4 Cr Holding Blocking - {d:%d-%m-%Y}" + (" - NO CHANGE" if nochange else "")
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs[:100]) + (f"<li>… and {len(xs) - 100} more</li>" if len(xs) > 100 else "")
    body = ("<html><body><p>Hi Team,</p>" +
            ("<p>No change in the above 4 Cr holding blocking list for " + f"{d:%d-%m-%Y}. Nothing to block or unblock.</p>" if nochange else
             f"<p>Please find attached the files for {d:%d-%m-%Y}.</p>"
             f"<p><b>New codes to block ({len(new)}) — {OUT_NAME}:</b></p><ul>{li([f'{k[0]} | {k[1]} | {k[2]}' for k, _ in new]) or '<li>None</li>'}</ul>"
             f"<p><b>Codes to unblock ({len(removed)}) — {UNBLOCK}:</b></p><ul>{li([f'{k[0]} | {k[1]} | {k[2]}' for k in removed]) or '<li>None</li>'}</ul>") +
            f"<p>{K.sig(params)}</p></body></html>")
    ctx.log(f"Step 4/6: e-mailing to {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else "") + "…")
    H.send_email(subject, body, to, cc, files, ctx, html=True)
    H.mark_sent(FOLDER, "mail")
    ctx.log(f"E-mail sent: “{subject}”.")

    # the master moves forward only after the e-mail went out: still-qualifying old lines + the new lines
    bdir = paths.proc_dir(FOLDER, "backup") / H.today_dash()
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(m, bdir / f"before_{dt.datetime.now():%H%M%S}_{MASTER}")
    tmp = m.with_suffix(".tmp")
    tmp.write_bytes((nl.join([header] + kept + [l for _, l in new]) + nl).encode("latin-1"))
    tmp.replace(m)
    ctx.log(f"Master file updated: {len(kept) + len(new):,} lines.")

    ok = None
    if not new:
        ctx.log("Step 5/6: no new codes to block — nothing to upload.")
    elif upload_is_on():
        ctx.log("Step 5/6: uploading the blocking file to the Kambala FTP…")
        ok = H.kambala_upload([block_f], ctx)
    else:
        ctx.log("Step 5/6: Kambala upload is switched OFF (Credentials › Kambala FTP) — e-mail only, nothing uploaded.")
    for f in files:
        shutil.copy2(f, bdir / f"{dt.datetime.now():%H%M%S}_{f.name}")
    ctx.log("Step 6/6: files backed up. Done.")
    tail = "" if not new else (" and uploaded" if ok else "; Kambala upload FAILED" if ok is False else "; NOT uploaded (upload is off)")
    return f"{len(new)} to block, {len(removed)} to unblock — e-mailed{tail}" if not nochange else "No change — e-mail sent"


def _params():
    ps = K.mail_params("kartavya.dhillon@mastertrust.co.in", "", with_wait=False)
    for p in ps:
        if p.name == "signature":
            p.default = SIGNATURE
    return ps


register(
    key="above_4cr_holding_blocking_file",
    name="ABOVE 4 CR HOLDING BLOCKING FILE",
    description="Daily from today's HOLDING file: holdings of 4 Cr or more (Pledge 0, RePledge 0, ExchVar 100) → selling-block "
                "lines. Compared with the master MASTER_MORE_THAN_4CR_BLOCKING_FINAL.txt: only NEW codes go into MORE_THAN_4CR_BLOCKING_FINAL.txt, "
                "codes that no longer qualify go into UNBLOCK CODES. E-mailed; the blocking file is uploaded to Kambala when "
                "that switch is ON. Upload the master file once with “Upload files”.",
    run=_run,
    params=_params(),
    retry_safe=False, parallel_safe=False, category="Reports", folder=FOLDER, uses=("smtp", "kambala_ftp"),
    upload_exts=(".txt",), upload_names=(MASTER,),
    upload_help=f"Upload the master file; it must be named exactly {MASTER}.",
)
