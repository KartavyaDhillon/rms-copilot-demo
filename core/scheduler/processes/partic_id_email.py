"""PARTIC ID EMAIL — validates the MASTER-PARTICID file against today's risk-report CP codes and e-mails the Excel result
(from PARTIC_ID_AUTOMATION.py).

Partic id file:  data/scheduler/PARTIC ID EMAIL/inputs/  — if it is already there (uploaded on the Files tab) the mailbox
is not touched; otherwise it is downloaded from the mailbox (POP3, Scheduler → Connections).
Risk report:     today's RISK_REPORT_<DDMMYYYY>.csv from COMMON FILES — read only, never moved or deleted.
"""
import time
from datetime import time as dtime

from core.scheduler import connections, helpers as H, paths, partic_lib as P
from core.scheduler.catalog import Param, register

FOLDER = "PARTIC ID EMAIL"


def _hhmm(s: str) -> dtime:
    try:
        h, m = (s or "06:00").strip().split(":")
        return dtime(int(h), int(m))
    except Exception:
        raise ValueError(f"'{s}' is not a time like 06:00")


@H.logged(FOLDER)
def _run(params, ctx):
    H.need("openpyxl")
    to, cc = H.parse_emails(params.get("to")), H.parse_emails(params.get("cc"))
    if not to:
        raise ValueError("No 'To' e-mail address set.")
    if H.already_sent(FOLDER, "report", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["report"], "e-mailed"))

    inputs = paths.proc_dir(FOLDER, "inputs")
    sender = (params.get("sender") or "support@mastertrust.co.in").strip()
    subject = (params.get("subject") or "MASTER-PARTICID details").strip()
    scan_start = _hhmm(params.get("scan_start"))
    wait_s = max(0, int(params.get("wait_minutes") or 0)) * 60
    t0 = time.time()

    partic = P.find_partic_file(inputs)
    if partic:
        ctx.log(f"Partic id file already present: {partic.name} — mailbox not used.")
    while not partic:
        try:
            c = connections.require("pop3", "server", "port", "username", "password")
            partic = P.download_partic_file(inputs, c["server"], int(c["port"]), c["username"], c["password"],
                                            sender, subject, scan_start, ctx.log)
        except RuntimeError:
            raise                                           # missing settings — waiting won't help
        except Exception as e:
            ctx.log(f"Mailbox error: {type(e).__name__}: {e}")
        partic = partic or P.find_partic_file(inputs)
        if partic:
            break
        if time.time() - t0 >= wait_s or getattr(ctx, "cancelled", False):
            raise FileNotFoundError("Partic id file is not in the folder and was not found in the mailbox. "
                                    "Upload it on this process's Files tab, or try Run Now later.")
        ctx.log("Not there yet — checking again in 60 s…")
        time.sleep(60)

    risk_path = H.find_risk_report(ctx)
    rows = P.read_partic_rows(partic)
    risk = P.read_risk_cpcodes(risk_path)
    ctx.log(f"Read {len(rows)} partic id row(s); {len(risk)} risk-report client(s).")
    if not rows:
        raise RuntimeError("The partic id file has no usable rows.")
    matched, mismatched, notfound = P.run_matching(rows, risk)

    report = paths.proc_dir(FOLDER, "outputs") / f"PARTIC_ID_MATCH_REPORT_{H.today_ddmmyyyy()}.xlsx"
    real = P.build_report(matched, mismatched, notfound, rows, risk, report, partic.name, risk_path.name)
    ctx.log(f"Report: matched {len(matched)}, mismatched {len(mismatched)}, not found {len(notfound)}, real conflicts {len(real)}")

    sig = (params.get("signature") or "Master Capital Services Ltd").strip()
    d = H.today_dash()
    body = (f"Hi,\n\nPlease find attached the daily Partic ID validation report for {d}.\n\n"
            f"This automated process cross-checks the Partic IDs in the MASTER-PARTICID file against the CP Codes in the "
            f"day's Risk Report. For every client-segment record it maps the segment (NSE/BSE/NFO/BFO) to its CP-code type "
            f"(NSECAP/BSECAP/NSEFNO/BSEFNO) and confirms the Partic ID matches.\n\n"
            f"The attached Excel report has four sheets:\n"
            f"  - Summary    : totals for matched / mismatched / not-found and real conflicts\n"
            f"  - Matched    : records where the Partic ID matches the risk report CP code\n"
            f"  - Mismatched : segment/CP mismatches and real Partic ID conflicts (need attention)\n"
            f"  - Not Found  : client IDs missing or with a blank CP code in the risk report\n\n"
            f"Today's figures - Matched: {len(matched)} | Mismatched: {len(mismatched)} | Not Found: {len(notfound)} "
            f"| Real Partic_ID conflicts: {len(real)}.\n\nBest regards,\n{sig}")
    H.send_email(f"PARTIC ID vs CP Code - Match Report - {d}", body, to, cc, [report], ctx)
    H.mark_sent(FOLDER, "report")
    H.backup_files(FOLDER, [partic, report], ctx)
    return f"report e-mailed (matched {len(matched)}, mismatched {len(mismatched)}, not found {len(notfound)})"


register(
    key="partic_id_email",
    name="PARTIC ID EMAIL",
    description="Matches the MASTER-PARTICID file against today's risk-report CP codes and e-mails the Excel report. "
                "Uses the file uploaded with the Upload files button if present, otherwise downloads it from the mailbox.",
    run=_run,
    params=[
        Param("to", "To", "text", required=True, fmt="emails", scope="process", help="E-mail addresses, comma or new-line separated."),
        Param("cc", "Cc", "text", fmt="emails", scope="process"),
        Param("sender", "Mail from", "str", default="support@mastertrust.co.in",
              help="Only mail from this address is used. Several addresses can be given, separated by commas."),
        Param("subject", "Mail subject (exact)", "str", default="MASTER-PARTICID details"),
        Param("scan_start", "Look for today's mail from", "str", default="06:00", help="HH:MM, India time."),
        Param("signature", "E-mail signature", "text", default="Kartavya Dhillon\nMaster Capital Services Ltd\nTel: 011-66331000 | Ext. 84"),
        Param("wait_minutes", "Wait for the file (minutes)", "int", default=0,
              help="0 = try once. Or keep checking the mailbox every minute for up to this long."),
    ],
    retry_safe=False,
    parallel_safe=False,
    category="Reports",
    folder=FOLDER,
    uses=("pop3", "smtp"),
    upload_exts=(".txt", ".csv", ".xlsx", ".xls"),
    upload_help="Upload the partic id file (output / PARTIC_ID / Partic_Id …) if the mailbox download is not working.",
)
