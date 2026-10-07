"""NMASS DEPOSIT IMAGE — DEPOSIT NMASS of the chosen accounts from the CTCL vs NMASS run-1 output, as a
'GOOD MORNING SIR' image on WhatsApp (from Amit_final.py).  Starts when run 1's output exists."""
import pandas as pd

from core.scheduler import helpers as H, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _ctcl as C

FOLDER = "NMASS DEPOSIT IMAGE"


@H.logged(FOLDER)
def _run(params, ctx):
    phones = H.parse_phones(params.get("phones"))
    if not phones:
        raise ValueError("No WhatsApp number set (Recipients button).")
    accounts = H.parse_list(params.get("accounts"))
    if not accounts:
        raise ValueError("No account ids set.")
    if H.already_sent(FOLDER, "image", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["image"]))
    src = C.get_run1(params, ctx)
    df = C.read_run1(src, ["ACCOUNTID", "DEPOSIT NMASS"])
    df["ACCOUNTID"] = df["ACCOUNTID"].astype(str).str.strip()
    sel = df[df["ACCOUNTID"].isin(accounts)][["ACCOUNTID", "DEPOSIT NMASS"]].copy()
    missing = [a for a in accounts if a not in set(sel["ACCOUNTID"])]
    if missing:
        ctx.log(f"Not in the file: {', '.join(missing)}")
    if sel.empty:
        raise RuntimeError("None of the configured accounts is in the CTCL vs NMASS file.")
    sel["DEPOSIT NMASS"] = C.num(sel["DEPOSIT NMASS"])
    adj_acc, adj_val = (params.get("adjust_account") or "").strip(), params.get("adjust_amount")
    try:
        adj_val = float(adj_val or 0)
    except ValueError:
        raise ValueError("'Subtract amount' must be a number.")
    if adj_acc and adj_val:
        sel.loc[sel["ACCOUNTID"] == adj_acc, "DEPOSIT NMASS"] -= adj_val
        ctx.log(f"{adj_acc}: {adj_val:,.0f} subtracted.")
    sel["DEPOSIT NMASS"] = sel["DEPOSIT NMASS"].map(lambda x: f"{x:.2f}" if pd.notnull(x) else "")
    rows = sel.values.tolist()
    date = H.today_dash()
    out = paths.proc_dir(FOLDER, "outputs")
    xlsx = out / "UPDATED_FULL_RECO_REPORT.xlsx"
    C.save_xlsx(xlsx, f"DEPOSIT_{date}", ["ACCOUNTID", "DEPOSIT"], rows, footer="GOOD MORNING SIR")
    img = C.render_equal(f"DEPOSIT_{date}", ["ACCOUNTID", "DEPOSIT"], rows, "GOOD MORNING SIR", out / "MCX_DEPOSIT.jpg")
    failed = []
    for ph in phones:
        try:
            H.whatsapp_send_image(img, ph, "FULL_RECO_REPORT", date, ctx)
        except Exception as e:
            failed.append(ph[-4:])
            ctx.log(f"WhatsApp to …{ph[-4:]} failed: {e}")
    if len(failed) == len(phones):
        raise RuntimeError("WhatsApp failed for every number.")
    H.mark_sent(FOLDER, "image")
    H.backup_files(FOLDER, [xlsx, img], ctx)
    if failed:
        raise RuntimeError(f"Sent, but WhatsApp failed for number(s) ending {', '.join(failed)}.")
    return f"image sent to {len(phones)} number(s)"


register(
    key="nmass_deposit_image",
    name="NMASS DEPOSIT IMAGE",
    description="After CTCL vs NMASS run 1: sends the DEPOSIT NMASS of selected accounts as an image on WhatsApp.",
    run=_run,
    params=[
        Param("phones", "WhatsApp numbers", "text", required=True, fmt="phones", scope="process",
              default="7541827310", help="10-digit numbers, optional #name after each."),
        Param("accounts", "Account ids", "text", scope="process", default="SN229, SN231, SN32, 9LD345, QX77, SN349"),
        Param("adjust_account", "Reduce deposit of account", "str", scope="process", default="SN32"),
        Param("adjust_amount", "…by this amount", "str", scope="process", default="10500000"),
        C.wait_param(),
    ],
    retry_safe=False, parallel_safe=False, category="CTCL reports", folder=FOLDER, uses=("whatsapp",),
)
