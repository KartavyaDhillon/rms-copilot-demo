"""SN359 WHATSAPP — risk-report image(s) sent on WhatsApp (from X.PY).

Group 1 (default SN359, with FNO_NOV column) → numbers in "Send to".
Group 2 (default SN232 + SN233)              → numbers in "Second group send to" (leave blank to skip it).
Reads RISK_REPORT_<today>.csv from COMMON FILES (never moves/deletes it). WhatsApp token: Scheduler → Connections.
"""
from core.scheduler import helpers as H, imaging, paths
from core.scheduler.catalog import Param, register

FOLDER = "SN359 WHATSAPP"


@H.logged(FOLDER)
def _run(params, ctx):
    date_disp = H.today_dash()
    stamp = H.today_ddmmyyyy()
    groups = []
    main_acc, main_to = H.parse_list(params.get("main_accounts") or "SN359"), H.parse_phones(params.get("main_to"))
    groups.append(("SN359", main_acc, main_to, True))
    if H.parse_list(params.get("second_to")):
        groups.append(("SN232_SN233", H.parse_list(params.get("second_accounts") or "SN232, SN233"),
                       H.parse_phones(params.get("second_to")), False))
    if not main_to:
        raise ValueError("No WhatsApp number set in 'Send to'.")

    risk = H.find_risk_report(ctx)
    label = (params.get("label") or "DEPOSIT").strip()
    out_dir = paths.proc_dir(FOLDER, "outputs")
    images, sent, skipped, failures = [], 0, 0, []

    for gname, accounts, phones, with_nov in groups:
        todo = [p for p in phones if not H.already_sent(FOLDER, f"{gname}|{p}", ctx)]
        skipped += len(phones) - len(todo)
        if not todo:
            ctx.log(f"{gname}: already sent today to everyone — skipped")
            continue
        df = imaging.load_risk_rows(risk, accounts)
        img = imaging.risk_table_image(df, date_disp, out_dir / f"{gname}_REPORT_{stamp}.jpg", with_nov)
        images.append(img)
        ctx.log(f"{gname}: image built for {', '.join(accounts)} ({len(df)} row(s))")
        for p in todo:
            try:
                H.whatsapp_send_image(img, p, label, date_disp, ctx)
                H.mark_sent(FOLDER, f"{gname}|{p}")
                sent += 1
            except Exception as e:                      # one bad number must not stop the others
                failures.append(f"{gname} → …{p[-4:]}: {e}")
                ctx.log(f"FAILED for …{p[-4:]}: {e}")

    if failures:
        raise RuntimeError(f"{len(failures)} send(s) failed, {sent} succeeded. " + " | ".join(failures))
    if images:
        H.backup_files(FOLDER, images, ctx)
    return f"{sent} WhatsApp message(s) sent" + (f", {skipped} skipped (already sent today)" if skipped else "")


register(
    key="sn359_whatsapp",
    name="SN359 WHATSAPP",
    description="Risk-report image for SN359 (and optionally SN232 + SN233) sent on WhatsApp.",
    run=_run,
    params=[
        Param("main_to", "Send to (WhatsApp numbers)", "text", required=True, fmt="phones",
              help="International form, separated by commas or new lines, e.g. +919812345678. Add more numbers here for 'CC'."),
        Param("main_accounts", "Accounts in the first image", "str", default="SN359"),
        Param("second_to", "Second group send to", "text", fmt="phones", help="Numbers that get the SN232 + SN233 image. Leave blank to skip it."),
        Param("second_accounts", "Accounts in the second image", "str", default="SN232, SN233"),
        Param("label", "Message heading", "str", default="DEPOSIT", help="First variable of the WhatsApp template."),
    ],
    retry_safe=False,
    parallel_safe=False,
    category="Reports",
    folder=FOLDER,
)
