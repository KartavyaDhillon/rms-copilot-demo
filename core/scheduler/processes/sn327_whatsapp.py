"""SN327 WHATSAPP — margin report card for SN327 sent on WhatsApp (from mx.py).

Reads RISK_REPORT_<today>.csv from COMMON FILES (never moves/deletes it). WhatsApp token: Scheduler → Connections.
"""
from core.scheduler import helpers as H, imaging, paths
from core.scheduler.catalog import Param, register

FOLDER = "SN327 WHATSAPP"


def _num(v):
    try:
        return int(float(v))
    except Exception:
        return 0


@H.logged(FOLDER)
def _run(params, ctx):
    account = (params.get("account") or "SN327").strip()
    phones = H.parse_phones(params.get("to"))
    if not phones:
        raise ValueError("No WhatsApp number set in 'Send to'.")
    label = (params.get("label") or "MARGIN_REPORT").strip()
    date_disp = H.today_dash()

    todo = [p for p in phones if not H.already_sent(FOLDER, f"{account}|{p}", ctx)]
    if not todo:
        return "already sent today to everyone — nothing to do"

    risk = H.find_risk_report(ctx)
    df = imaging.load_risk_rows(risk, [account])
    r = df.iloc[0]
    vals = {"ledger": _num(r["FIN_WITHOUT_MGN"]), "fd": _num(r["CASH_LEDGER"]),
            "pledge": _num(r["TOTAL_STOCK_EX_REPLG"]), "nov": _num(r["FNONOV"]), "margin": _num(r["MARGIN"])}
    img = imaging.margin_card_image(account, date_disp, vals, paths.proc_dir(FOLDER, "outputs") / f"Margin_Report_{H.today_ddmmyyyy()}.png")
    ctx.log(f"Margin card built for {account}")

    sent, failures = 0, []
    for p in todo:
        try:
            H.whatsapp_send_image(img, p, label, date_disp, ctx)
            H.mark_sent(FOLDER, f"{account}|{p}")
            sent += 1
        except Exception as e:
            failures.append(f"…{p[-4:]}: {e}")
            ctx.log(f"FAILED for …{p[-4:]}: {e}")
    if failures:
        raise RuntimeError(f"{len(failures)} send(s) failed, {sent} succeeded. " + " | ".join(failures))
    H.backup_files(FOLDER, [img], ctx)
    return f"{sent} WhatsApp message(s) sent"


register(
    key="sn327_whatsapp",
    name="SN327 WHATSAPP",
    description="Margin report card (ledger / FD / pledge / NOV / margin used) for SN327 sent on WhatsApp.",
    run=_run,
    params=[
        Param("to", "Send to (WhatsApp numbers)", "text", required=True, fmt="phones",
              help="International form, separated by commas or new lines, e.g. +919812345678. Add more numbers here for 'CC'."),
        Param("account", "Account", "str", default="SN327"),
        Param("label", "Message heading", "str", default="MARGIN_REPORT", help="First variable of the WhatsApp template."),
    ],
    retry_safe=False,
    parallel_safe=False,
    category="Reports",
    folder=FOLDER,
)
