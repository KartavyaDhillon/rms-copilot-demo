"""CTCL DEPOSIT IMAGE — DEPOSIT CTCL (in crores) of the chosen accounts from the CTCL vs NMASS run-1 output, as an
image on WhatsApp (from NEW_AMIT_SIR_FINAL.py).  Starts when run 1's output exists."""
from core.scheduler import helpers as H, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _ctcl as C

FOLDER = "CTCL DEPOSIT IMAGE"


@H.logged(FOLDER)
def _run(params, ctx):
    phones = H.parse_phones(params.get("phones"))
    if not phones:
        raise ValueError("No WhatsApp number set (Recipients button).")
    accounts = H.parse_list(params.get("accounts"))
    if not accounts:
        raise ValueError("No client codes set.")
    if H.already_sent(FOLDER, "image", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["image"]))
    src = C.get_run1(params, ctx)
    df = C.read_run1(src, ["ACCOUNTID", "DEPOSIT CTCL"])
    df = df[["ACCOUNTID", "DEPOSIT CTCL"]].copy()
    df["ACCOUNTID"] = df["ACCOUNTID"].astype(str).str.strip()
    df = df[df["ACCOUNTID"].isin(accounts)]
    missing = [a for a in accounts if a not in set(df["ACCOUNTID"])]
    if missing:
        ctx.log(f"Not in the file: {', '.join(missing)}")
    if df.empty:
        raise RuntimeError("None of the configured client codes is in the CTCL vs NMASS file.")
    df["DEPOSIT CTCL"] = C.num(df["DEPOSIT CTCL"])
    df["cr"] = (df["DEPOSIT CTCL"] / 1e7).apply(lambda x: int(x * 100) / 100 if x == x else float("nan"))
    rows = [[a, f"{v:.2f}" if v == v else ""] for a, v in zip(df["ACCOUNTID"], df["cr"])]
    date = H.today_dash()
    out = paths.proc_dir(FOLDER, "outputs")
    xlsx = out / "MCX_DEPOSIT.xlsx"
    C.save_xlsx(xlsx, f"DEPOSIT - {date}", ["Client Code", "DEPOSIT (Cr)"],
                [[a, v] for a, v in zip(df["ACCOUNTID"], df["cr"])], fmt2=True)
    img = C.render_auto(f"DEPOSIT - {date}", ["Client Code", "DEPOSIT (Cr)"], rows, out / "MCX_DEPOSIT.jpg")
    failed = []
    for ph in phones:
        try:
            H.whatsapp_send_image(img, ph, "DEPOSIT", date, ctx)
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
    key="ctcl_deposit_image",
    name="CTCL DEPOSIT IMAGE",
    description="After CTCL vs NMASS run 1: sends the DEPOSIT CTCL (in Cr) of selected accounts as an image on WhatsApp.",
    run=_run,
    params=[
        Param("phones", "WhatsApp numbers", "text", required=True, fmt="phones", scope="process",
              default="9871410617, 9811184735, 9910867544", help="10-digit numbers, optional #name after each."),
        Param("accounts", "Client codes", "text", scope="process", default="SN246, DL110052, DL1000046, AP1145, SN380"),
        C.wait_param(),
    ],
    retry_safe=False, parallel_safe=False, category="CTCL reports", folder=FOLDER, uses=("whatsapp",),
)
