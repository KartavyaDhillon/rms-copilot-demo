"""
core/pipelines/cli_allocation.py — Client Prudential Limit's ALLOCATION DETAIL step
(OLD pipeline). This is the legacy math, unchanged from ALLOCATION_CLI_NMASS_NCDEX_old.py
— no RISK_REPORT / Kambala pre-check or adjustment. That belongs to the NEW pipeline
(core/pipelines/cli_allocation_new.py) — the user's uploaded "new" script explicitly
labeled itself as the NEW process, so it's wired to Run NEW, not folded into Run OLD.

  STEP 1 — check locally first. If Allocation_Detail_<DDMMYYYY>.csv is already sitting
           in the base folder, skip the FTP download entirely. Only if it's genuinely
           missing locally do we go to FTP, look for it, and download it. If the FTP
           folder doesn't even exist yet but we already have the file locally, we
           create that folder and push our local copy up (replaces manual SFTP).
           (Shared with cli_allocation_new.py via core.pipeline_box._pb_fetch_allocation_detail
           — this part is identical between OLD and NEW.)
  STEP 2 — processing: group by AccountID, sum collateral columns, drop zero-sum
           codes, write Intermediate.csv + CLI_PRUDENTIAL_LIMIT.txt. Same math as
           the legacy ALLOCATION_CLI_NMASS_NCDEX_old.py's run_processing().

This is Step 1 of the "Run OLD" chain (core/pipelines/cli_allocation.py ->
cli_download.py -> cli_mcx_download.py -> cli_upload.py), called by api.py's
/cli/run-old-stream. Not exposed as its own dashboard button — it's an internal
first step of Run, per explicit request.

Tags printed for core.pipeline_box._status():
  [FRIENDLY_MSG] <text>   — plain-English AI fallback for an unrecognized failure
"""
import datetime
import os

from core.error_translator import translate_error


def run_cli_allocation():
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    os.makedirs(base_folder, exist_ok=True)
    today_str = datetime.datetime.now().strftime("%d%m%Y")
    _log_buffer = []

    def _p(msg):
        print(msg, flush=True)
        _log_buffer.append(str(msg))

    # ── STEP 1: FTP download of Allocation_Detail (local-first) ──────────────
    _p("🚀 STEP 1: Allocation Detail (local-first, FTP fallback)")
    try:
        from core.pipeline_box import _pb_fetch_allocation_detail
        alloc_path = _pb_fetch_allocation_detail(base_folder, today_str, _p)
    except Exception as e:
        _p(f"❌ Allocation Detail step failed: {e}")
        _ai = translate_error("Client Prudential Limit — Allocation Detail", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or ('Allocation Detail step failed: ' + str(e))}")
        raise SystemExit(1)

    # ── STEP 2: processing — build CLI_PRUDENTIAL_LIMIT.txt ──────────────────
    _p("🚀 STEP 2: Building CLI_PRUDENTIAL_LIMIT.txt")
    try:
        import pandas as pd
        df = pd.read_csv(alloc_path)
        df.columns = df.columns.str.strip()
        cols = ["CMCreditObligation", "StockDiff", "EpiCredit", "MtfCashColl",
                "ExchRepledgeAdj", "FnoDebitBill", "EPNLimit", "HoldCashMgn", "MTFExcess"]
        for col in cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0) if col in df.columns else 0
        df_grouped = df.groupby("AccountID")[cols].sum().reset_index()
        df_grouped["Sum"] = df_grouped[cols].sum(axis=1)
        df_final = df_grouped[["AccountID", "Sum"]]
        df_final = df_final[df_final["Sum"] != 0]
        df_final["Sum"] = df_final["Sum"].round(2)
        df_final.to_csv(os.path.join(base_folder, "Intermediate.csv"), index=False)
        with open(os.path.join(base_folder, "CLI_PRUDENTIAL_LIMIT.txt"), "w") as f:
            f.write("ClientPrudentialLimit\n")
            for _, row in df_final.iterrows():
                f.write(f"{row['AccountID']},{row['Sum']}\n")
        _p("✅ Processing done — Intermediate.csv + CLI_PRUDENTIAL_LIMIT.txt written")
    except Exception as e:
        _p(f"❌ Processing failed: {e}")
        _ai = translate_error("Client Prudential Limit — Allocation Detail processing", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or ('Processing failed: ' + str(e))}")
        raise SystemExit(1)

    _p("✅ Allocation Detail step finished")


if __name__ == "__main__":
    run_cli_allocation()
