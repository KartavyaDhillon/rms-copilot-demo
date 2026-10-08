"""PERSISTENT NOISE CREATORS — previous trading day's PNC_OPT file (NSE surveillance) → one e-mail row per client
code to two groups (from Neeraj_final.py)."""
import datetime as dt
import html as _h

from core.scheduler import connections, helpers as H, nse, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "PERSISTENT NOISE CREATORS"

INTRO = ("Dear Sir/Ma’am,<br><br>This is to inform you that the rolling instance count for Order Based Surveillance "
         "Measure- Persistent Noise Creators specified in circular no. 52992 dated July 15, 2022 is as below:")


def _table(row):
    t = '<table style="border-collapse: collapse; width: 100%;"><tr>'
    t += "".join(f'<th style="border:1px solid black;padding:5px;text-align:center;">{_h.escape(str(c))}</th>' for c in row.columns)
    t += "</tr>"
    for r in row.itertuples(index=False):
        t += "<tr>" + "".join(f'<td style="border:1px solid black;padding:5px;text-align:center;">{_h.escape(str(v))}</td>' for v in r) + "</tr>"
    return t + "</table>"


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    groups = []
    for n in ("1", "2"):
        code = (params.get(f"code{n}") or "").strip()
        to = H.parse_emails(params.get(f"to{n}"))
        if code and to:
            groups.append((n, code, to, H.parse_emails(params.get(f"cc{n}"))))
    if not groups:
        raise ValueError("Set at least one client code with its To list (Recipients button).")
    K.require_trading_day(ctx)
    inp = paths.proc_dir(FOLDER, "inputs")
    mem = connections.require("nse", "member_code")["member_code"]
    bday = H.prev_trading_day(dt.date.today())
    name = f"PNC_OPT_{mem}_{bday:%d%m%Y}.csv"
    try:
        f = nse.download_wait("member", "FO", "Reports/../Surveillance/Dnld", name, inp, ctx, K.wait_of(params))
    except FileNotFoundError:
        # NSE publishes this file only on days there is something to report, so "not there" is the normal answer.
        # (Login problems, network problems, a bad file, e-mail errors still FAIL — only this case is a success.)
        ctx.log(f"{name} is not on NSE — nothing to report for {bday:%d-%m-%Y}, so no e-mail is needed.")
        return "no PNC file on NSE today — nothing to send"
    df = pd.read_csv(f)
    sent = 0
    for n, code, to, cc in groups:
        if H.already_sent(FOLDER, f"g{n}", ctx):
            ctx.log(f"Group {n} already e-mailed today — skipped.")
            continue
        row = df[df["Client Code"].astype(str).str.strip() == code]
        if row.empty:
            ctx.log(f"No row for client code {code} in {name} — e-mail for group {n} skipped.")
            continue
        body = f"<p>{INTRO}</p><p>{_table(row)}</p><p>Best regards,<br>{K.sig(params)}</p>"
        H.send_email("Persistent Noise Creators", body, to, cc, [], ctx, html=True)
        H.mark_sent(FOLDER, f"g{n}")
        sent += 1
    H.backup_files(FOLDER, [f], ctx)
    if not sent and not any(H.already_sent(FOLDER, f"g{n}", ctx) for n, *_ in groups):
        raise H.Skipped("None of the configured client codes appears in today's file — nothing to send.")
    return f"{sent} e-mail(s) sent"


register(
    key="persistent_noise_creators",
    name="PERSISTENT NOISE CREATORS",
    description="Reads NSE's PNC_OPT surveillance file for the previous trading day and e-mails the matching client "
                "row(s) to each group.",
    run=_run,
    params=[
        Param("code1", "Group 1 — client code in file", "str", default="8DAA61"),
        Param("to1", "Group 1 — To", "text", fmt="emails", scope="process",
              default="sunny.kichloo@mastertrust.co.in,rishi.kumar@mastertrust.co.in"),
        Param("cc1", "Group 1 — Cc", "text", fmt="emails", scope="process", default="rms_del@mastertrust.co.in"),
        Param("code2", "Group 2 — client code in file", "str", default="6436"),
        Param("to2", "Group 2 — To", "text", fmt="emails", scope="process",
              default="kamya@mastertrust.co.in,taranjeet@mastertrust.co.in,kamal.mitter@mastertrust.co.in,maninders@mastertrust.co.in"),
        Param("cc2", "Group 2 — Cc", "text", fmt="emails", scope="process", default="rms_del@mastertrust.co.in"),
        Param("signature", "E-mail signature", "text", default=K.SIGNATURE),
        Param("wait_minutes", "Wait for the file (minutes)", "int", default=0),
    ],
    retry_safe=False, parallel_safe=False, category="NSE files", folder=FOLDER, uses=("nse", "smtp"),
)
