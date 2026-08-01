"""
core/pipelines/cli_allocation_new.py — Client Prudential Limit's ALLOCATION DETAIL
step for the NEW pipeline. This is the user's uploaded "new" script
(ALLOCATION_CLI_NMASS_NCDEX-2841612e.py — explicitly described as "the new one is
this") ported in full: a pre-file check plus an updated CLI_PRUDENTIAL_LIMIT.txt
formula that layers RISK_REPORT + the Kambala deposit file on top of the base
allocation sum. This is Step 1 of Run NEW — Download / MCX Web Allocation / Upload
after this are unchanged from OLD (the user only described a new Allocation step),
so Run NEW reuses cli_download.py / cli_mcx_download.py / cli_upload.py as-is.

  STEP 0 — pre-file check (precheck_required_inputs() in the original script):
           today's RISK_REPORT_<DDMMYYYY>.csv and
           Kambala_Omny_Depo_Adhoc_All_level.txt_<DDMMYYYY>.txt MUST both be present
           before anything else runs — this stops the whole Run NEW chain before
           even the Allocation Detail download, exactly like the original script.
           RISK_REPORT: checked in the CLI base folder first, then copied in from
           the shared COMMON FILES folder if it's sitting there instead (the
           dashboard's "Common files" upload button places it there).
           Kambala: fetched straight from FTP (KAMB/DEPO folder) if not already
           local — see core.pipeline_box._pb_download_kambala_from_ftp(), the
           single-file extraction from the user's ftpfiles_newfinal.py.
           If either is still missing after that, Run NEW stops here.
  STEP 1 — Allocation Detail fetch (local-first, FTP fallback) — identical to OLD's
           Step 1, shared via core.pipeline_box._pb_fetch_allocation_detail().
  STEP 2 — processing (run_processing() [UPDATED] in the original script):
             1) base allocation: group by AccountID, sum collateral columns, drop
                zero-sum codes (same columns/math as OLD)
             2) RISK_REPORT: accounts where MTF_STATUS == 'Y' (matched on ACCOUNTID)
             3) Kambala TXT: '|'-delimited, header row skipped, col 0 = AccountID,
                col 25 (Excel column Z) = value; abs(that value) computed per code
             4) abs(Z) is added ONLY to accounts that are both in the base CLI file
                AND in the 'Y' set from RISK_REPORT
           writes Intermediate.csv + CLI_PRUDENTIAL_LIMIT.txt (same filenames as
           OLD — Run NEW overwrites the same output files OLD would have produced).

This is Step 1 of the "Run NEW" chain (core/pipelines/cli_allocation_new.py ->
cli_download.py -> cli_mcx_download.py -> cli_upload.py), called by api.py's
/cli/run-new-stream. Not exposed as its own dashboard button — it's an internal
first step of Run NEW.

Tags printed for core.pipeline_box._status():
  [MISSING_FILE] <name>   — a required pre-check file is absent for today
  [FRIENDLY_MSG] <text>   — plain-English AI fallback for an unrecognized failure
"""
import datetime
import os
import shutil

from core.error_translator import translate_error


def run_cli_allocation_new():
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    common_dir = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    os.makedirs(base_folder, exist_ok=True)
    today_str = datetime.datetime.now().strftime("%d%m%Y")
    _log_buffer = []

    def _p(msg):
        print(msg, flush=True)
        _log_buffer.append(str(msg))

    # ── STEP 0: pre-file check — RISK_REPORT + Kambala must exist for TODAY ───
    _p("🚀 STEP 0: Pre-file check (RISK_REPORT + Kambala)")
    risk_name = f"RISK_REPORT_{today_str}.csv"
    kambala_name = f"Kambala_Omny_Depo_Adhoc_All_level.txt_{today_str}.txt"
    risk_path = os.path.join(base_folder, risk_name)
    kambala_path = os.path.join(base_folder, kambala_name)

    if not os.path.exists(risk_path):
        common_risk_path = os.path.join(common_dir, risk_name)
        if os.path.exists(common_risk_path):
            try:
                shutil.copy2(common_risk_path, risk_path)
                _p(f"✅ Found: {risk_name} (copied from COMMON FILES)")
            except Exception as _ce:
                _p(f"⚠️ Found {risk_name} in COMMON FILES but couldn't copy it in: {_ce}")

    if not os.path.exists(kambala_path):
        _p(f"ℹ️  {kambala_name} not found locally — checking FTP (KAMB/DEPO)")
        try:
            from core.pipeline_box import _pb_download_kambala_from_ftp
            if _pb_download_kambala_from_ftp(kambala_path, kambala_name):
                _p(f"✅ Downloaded from FTP: {kambala_name}")
            else:
                _p(f"⚠️  {kambala_name} not on FTP yet either — place it manually or wait for it to land")
        except Exception as _kfe:
            _p(f"⚠️  Kambala FTP check failed (non-fatal, will fall back to manual): {_kfe}")

    missing_pre = []
    if os.path.exists(risk_path):
        _p(f"✅ Found: {risk_name}")
    else:
        _p(f"❌ Missing: {risk_name}")
        missing_pre.append(risk_name)
    if os.path.exists(kambala_path):
        _p(f"✅ Found: {kambala_name}")
    else:
        _p(f"❌ Missing: {kambala_name}")
        missing_pre.append(kambala_name)

    if missing_pre:
        for m in missing_pre:
            _p(f"[MISSING_FILE] {m}")
        _p(f"🔴 Required file(s) not present — stopping before Allocation Detail. "
           f"Place them in {base_folder} (RISK_REPORT can also be uploaded via the "
           f"COMMON FILES button) and run again.")
        _p(f"[FRIENDLY_MSG] Can't start Run NEW — {', '.join(missing_pre)} missing for "
           f"today ({today_str}). Upload them first, then run again.")
        raise SystemExit(1)
    _p("🟢 All required pre-check files present — continuing")

    # ── STEP 1: FTP download of Allocation_Detail (local-first, shared w/ OLD) ─
    _p("🚀 STEP 1: Allocation Detail (local-first, FTP fallback)")
    try:
        from core.pipeline_box import _pb_fetch_allocation_detail
        alloc_path = _pb_fetch_allocation_detail(base_folder, today_str, _p)
    except Exception as e:
        _p(f"❌ Allocation Detail step failed: {e}")
        _ai = translate_error("Client Prudential Limit (NEW) — Allocation Detail", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or ('Allocation Detail step failed: ' + str(e))}")
        raise SystemExit(1)

    # ── STEP 2: processing — base allocation + RISK_REPORT + Kambala ─────────
    _p("🚀 STEP 2: Building CLI_PRUDENTIAL_LIMIT.txt (base + RISK_REPORT + Kambala)")
    try:
        import pandas as pd

        # 1) base allocation processing
        df = pd.read_csv(alloc_path, dtype={"AccountID": str})
        df.columns = df.columns.str.strip()
        cols = ["CMCreditObligation", "StockDiff", "EpiCredit", "MtfCashColl",
                "ExchRepledgeAdj", "FnoDebitBill", "EPNLimit", "HoldCashMgn", "MTFExcess"]
        for col in cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0) if col in df.columns else 0
        df["AccountID"] = df["AccountID"].astype(str).str.strip()
        df_grouped = df.groupby("AccountID")[cols].sum().reset_index()
        df_grouped["Sum"] = df_grouped[cols].sum(axis=1)
        df_final = df_grouped[["AccountID", "Sum"]].copy()
        df_final = df_final[df_final["Sum"] != 0].copy()

        # 2) RISK_REPORT -> accounts where MTF_STATUS == 'Y' (match col ACCOUNTID)
        y_accounts = set()
        if os.path.exists(risk_path):
            rdf = pd.read_csv(risk_path, dtype=str)
            rdf.columns = rdf.columns.str.strip()
            rdf["ACCOUNTID"] = rdf["ACCOUNTID"].astype(str).str.strip()
            rdf["MTF_STATUS"] = rdf["MTF_STATUS"].astype(str).str.strip().str.upper()
            y_accounts = set(rdf.loc[rdf["MTF_STATUS"] == "Y", "ACCOUNTID"])
            _p(f"✅ Risk report loaded — {len(y_accounts)} codes with MTF_STATUS = Y")
        else:
            _p(f"⚠️ Risk report not found at processing time: {risk_path} — skipping MTF addition")

        # 3) Kambala TXT -> abs(Z column, index 25) per code, '|' delimited, header skipped
        kambala_z = {}
        Z_INDEX = 25
        if os.path.exists(kambala_path):
            with open(kambala_path, "r", encoding="utf-8", errors="ignore") as kf:
                lines = kf.read().splitlines()
            for line in lines[1:]:
                if not line.strip():
                    continue
                fields = line.split("|")
                if len(fields) <= Z_INDEX:
                    continue
                acc = fields[0].strip()
                raw = fields[Z_INDEX].strip()
                try:
                    zval = abs(float(raw)) if raw != "" else 0.0
                except ValueError:
                    zval = 0.0
                kambala_z[acc] = zval
            _p(f"✅ Kambala file loaded — {len(kambala_z)} codes (abs Z column)")
        else:
            _p(f"⚠️ Kambala file not found at processing time: {kambala_path} — skipping MTF addition")

        # 4) add abs(Z) only to 'Y' codes that are already in the CLI file
        added_count, added_total = 0, 0.0

        def _adjust(row):
            nonlocal added_count, added_total
            acc = row["AccountID"]
            base = row["Sum"]
            if acc in y_accounts and acc in kambala_z:
                z = kambala_z[acc]
                if z != 0:
                    added_count += 1
                    added_total += z
                return base + z
            return base

        df_final["Sum"] = df_final.apply(_adjust, axis=1)
        _p(f"✅ Z-value added to {added_count} 'Y' codes (total added = {round(added_total, 2)})")
        df_final["Sum"] = df_final["Sum"].round(2)

        # 5) save
        df_final.to_csv(os.path.join(base_folder, "Intermediate.csv"), index=False)
        with open(os.path.join(base_folder, "CLI_PRUDENTIAL_LIMIT.txt"), "w") as f:
            f.write("ClientPrudentialLimit\n")
            for _, row in df_final.iterrows():
                f.write(f"{row['AccountID']},{row['Sum']}\n")
        _p("✅ Processing done — Intermediate.csv + CLI_PRUDENTIAL_LIMIT.txt written")
    except Exception as e:
        _p(f"❌ Processing failed: {e}")
        _ai = translate_error("Client Prudential Limit (NEW) — Allocation Detail processing", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or ('Processing failed: ' + str(e))}")
        raise SystemExit(1)

    _p("✅ Allocation Detail (NEW) step finished")


if __name__ == "__main__":
    run_cli_allocation_new()
