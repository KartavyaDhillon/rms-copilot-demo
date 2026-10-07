"""MCX WEB MORNING EMAIL — adds the Zone column to today's MCX RSK383 export, splits it by zone and e-mails it
(stage 2 of MORNING_MCX_WEB_AMIT_SIR.py).

This process NEVER logs in to the MCX portal and never asks for a token.  It only needs the file:
  1. data/scheduler/MCX WEB MORNING EMAIL/inputs/   ← uploaded by hand on the Files tab (checked first), or
  2. the CLI Prudential Limit folder (CLI_FOLDER)    ← left there by the CLI morning run (copied, never moved).
Both names are accepted:  MCX_DEPOSIT_<anything>.xlsx   and the raw export name  <random>-<14 digits>.xlsx
Today's risk report comes from COMMON FILES (read only — never moved or deleted).
"""
import datetime as dt
import html
import os
import re
import shutil
import time
from pathlib import Path

from core.scheduler import helpers as H, paths
from core.scheduler.catalog import Param, register

FOLDER = "MCX WEB MORNING EMAIL"
_DEPOSIT_RE = re.compile(r"^MCX_DEPOSIT_.*\.xlsx$", re.I)
_RAW_RE = re.compile(r"^[A-Za-z0-9]+-(\d{14})\.xlsx$", re.I)


def _is_today(p: Path) -> bool:
    today = dt.date.today()
    m = _RAW_RE.match(p.name)
    if m:
        try:
            if dt.datetime.strptime(m.group(1), "%Y%m%d%H%M%S").date() == today:
                return True
        except ValueError:
            pass
    try:
        return dt.date.fromtimestamp(p.stat().st_mtime) == today
    except OSError:
        return False


def _newest(folder: Path):
    if not folder.is_dir():
        return None
    c = [p for p in folder.iterdir() if p.is_file() and (_DEPOSIT_RE.match(p.name) or _RAW_RE.match(p.name)) and _is_today(p)]
    return max(c, key=lambda p: p.stat().st_mtime) if c else None


def _locate(ctx) -> Path:
    inputs = paths.proc_dir(FOLDER, "inputs")
    f = _newest(inputs)
    if f:
        ctx.log(f"Using file from inputs (uploaded): {f.name}")
        return f
    cli = Path(os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT"))
    f = _newest(cli)
    if f:
        dest = inputs / f.name
        shutil.copy2(f, dest)                          # copy only — the CLI run still owns its file
        ctx.log(f"Picked up from CLI Prudential folder: {f.name} (copied; original left in place)")
        return dest
    if getattr(ctx, "trigger", "") == H.MANUAL:           # Run Now to send again: reuse today's already-processed file
        f = _newest(paths.proc_dir(FOLDER, "backup") / H.today_dash())
        if f:
            dest = inputs / f.name
            shutil.copy2(f, dest)
            ctx.log(f"Run Now: re-using today's file from backup: {f.name}")
            return dest
    raise FileNotFoundError("Today's MCX RSK file was not found — neither uploaded on this process's Files tab nor in the "
                            f"CLI Prudential folder ({cli}). Run the CLI morning run, or upload the file and use Run Now.")


def _autofit(path: Path):
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb.active
    for col in ws.columns:
        longest = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        ws.column_dimensions[col[0].column_letter].width = longest + 2
    wb.save(path)


def _body(zone_text: str, signature: str) -> str:
    sig = "<br>".join(html.escape(l) for l in signature.splitlines() if l.strip())
    return (f"<html><body style=\"font-family:Segoe UI; font-size:13px;\"><p>Hi Team,</p>"
            f"<p>Please find attached the <b>MCX Web Allocation Report for {H.today_dash()}</b>.</p><br>"
            f"<p>Best regards,<br>{sig}</p></body></html>")


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    H.need("openpyxl")
    zones = [("ZOCHND", H.parse_emails(params.get("zochnd_to")), H.parse_emails(params.get("zochnd_cc"))),
             ("ZOLUDH", H.parse_emails(params.get("zoludh_to")), H.parse_emails(params.get("zoludh_cc")))]
    for z, to, _cc in zones:
        if not to:
            raise ValueError(f"No 'To' e-mail address set for {z}.")
    signature = params.get("signature") or "Master Capital Services Ltd"

    pending = [z for z in zones if not H.already_sent(FOLDER, f"zone|{z[0]}", ctx)]
    if not pending:
        raise H.Skipped(H.skip_message(FOLDER, [f"zone|{z[0]}" for z in zones], "e-mailed"))

    # wait for the file (optional) — one run row, no failure noise
    wait_s = max(0, int(params.get("wait_minutes") or 0)) * 60
    t0 = time.time()
    while True:
        try:
            rsk = _locate(ctx)
            break
        except FileNotFoundError:
            if time.time() - t0 >= wait_s or getattr(ctx, "cancelled", False):
                raise
            ctx.log("RSK file not here yet — checking again in 30 s…")
            time.sleep(30)

    risk = H.find_risk_report(ctx)

    raw = pd.read_excel(rsk, header=None)
    hdr = next((i for i in range(len(raw)) if "UCC Code" in raw.iloc[i].astype(str).str.strip().tolist()), None)
    if hdr is None:
        raise RuntimeError("'UCC Code' header row not found in the RSK file — is this the right export?")
    df = pd.read_excel(rsk, header=hdr).dropna(how="all").reset_index(drop=True)
    ctx.log(f"RSK rows: {len(df)}")

    rdf = pd.read_csv(risk, usecols=lambda c: c.strip() in ("ACCOUNTID", "Zone"))
    rdf.columns = rdf.columns.str.strip()
    if "ACCOUNTID" not in rdf.columns or "Zone" not in rdf.columns:
        raise RuntimeError("Risk report has no ACCOUNTID / Zone column.")
    zmap = dict(zip(rdf["ACCOUNTID"].astype(str).str.strip(), rdf["Zone"]))
    df["Zone"] = df["UCC Code"].astype(str).str.strip().map(zmap)
    df["Zone"] = df["Zone"].astype(str).str.strip()

    out = paths.proc_dir(FOLDER, "outputs")
    stamp = H.today_ddmmyyyy()
    created = []
    full = out / f"MCX_WebAllocation_with_Zone_{stamp}.xlsx"
    df.to_excel(full, index=False)
    _autofit(full)
    created.append(full)

    sent, failures = 0, []
    for zone, to, cc in pending:
        part = df[df["Zone"] == zone]
        if part.empty:
            ctx.log(f"No rows for {zone} — nothing to send")
            continue
        f = out / f"Mcx_WebAllocation_{zone}.xlsx"
        part.to_excel(f, index=False)
        _autofit(f)
        created.append(f)
        try:
            H.send_email(f"MCX WebAllocation Report - {zone}", _body(zone, signature), to, cc, [f], ctx, html=True)
            H.mark_sent(FOLDER, f"zone|{zone}")
            sent += 1
        except Exception as e:                              # one zone failing must not block the other
            failures.append(f"{zone}: {e}")
            ctx.log(f"FAILED {zone}: {e}")

    if failures:
        raise RuntimeError(f"{len(failures)} e-mail(s) failed, {sent} sent. " + " | ".join(failures))
    H.backup_files(FOLDER, [rsk] + created, ctx)
    return f"{sent} zone e-mail(s) sent"


register(
    key="mcx_web_morning_email",
    name="MCX WEB MORNING EMAIL",
    description="Adds the Zone column to today's MCX RSK383 file, splits it by zone and e-mails ZOCHND and ZOLUDH. "
                "File-only: never logs in to MCX. Picks the file uploaded with the Upload files button, or the one left by the CLI morning run.",
    run=_run,
    params=[
        Param("zochnd_to", "ZOCHND — To", "text", required=True, fmt="emails", scope="process", help="E-mail addresses, comma or new-line separated."),
        Param("zochnd_cc", "ZOCHND — Cc", "text", fmt="emails", scope="process"),
        Param("zoludh_to", "ZOLUDH — To", "text", required=True, fmt="emails", scope="process"),
        Param("zoludh_cc", "ZOLUDH — Cc", "text", fmt="emails", scope="process"),
        Param("signature", "E-mail signature", "text", default="Kartavya Dhillon\nMaster Capital Services Ltd\nTel: 011-66331000 | Ext. 84"),
        Param("wait_minutes", "Wait for the file (minutes)", "int", default=0,
              help="0 = fail at once if the file is not there. Or wait up to this long for the CLI morning run to leave it."),
    ],
    retry_safe=False,
    parallel_safe=False,
    category="Reports",
    folder=FOLDER,
    uses=("smtp",),
    upload_exts=(".xlsx",),
    upload_help="Upload today's MCX RSK383 export (MCX_DEPOSIT_….xlsx or the raw <name>-<14 digits>.xlsx) if it did not arrive from the CLI morning run.",
)
