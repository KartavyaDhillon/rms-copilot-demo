r"""
test_ctcl_logic.py — verify the CTCL vs NMASS calculation WITHOUT running it.

WHY THIS EXISTS
───────────────
The parts of CTCL vs NMASS that decide money — the sign-aware DIFFERENCE, the
REMARKS priority, the KAMBALA/VIRTUAL ID matching — used to be buried inside
the 400-line run function, where the only way to exercise them was a full live
run that downloads files and emails four zones. That is not a test you can
repeat, so in practice it never got run.

They now live as pure functions in core/pipelines/ctcl_vs_nmass.py:
    compute_difference(df)                     → rounds + DIFFERENCE(G-F)
    apply_remarks(df, kambala_ids, virtual_ids)→ KAMBALA STATUS + REMARKS
    normalize_account_id(value)                → the ID match key
    load_kambala_reference(path)               → reads the real workbook

This script calls them directly with hand-built rows covering the cases that
actually go wrong, and checks the real KAMBALA workbook loads.

Nothing here touches the network, the database, or email. Safe to run any time,
including during market hours.

USAGE  (from the rms-copilot folder)
────────────────────────────────────
    python tools\test_ctcl_logic.py
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO, ".env"), override=True)
except Exception:
    pass

import polars as pl
from core.pipelines.ctcl_vs_nmass import (
    compute_difference, apply_remarks, normalize_account_id,
    load_kambala_reference, kambala_name,
)

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label:<52} got={got!r}")
    if not ok:
        _fails.append(f"{label}: expected {want!r}, got {got!r}")


# ── 1. ACCOUNTID normalisation ──────────────────────────────────────────────
# Excel stores some IDs as numbers, so pandas renders them '499.0'. Without
# normalising, '499.0' never matches '499' and the account is silently marked
# N / not-virtual — a wrong answer that looks like a right one.
print("\n1. normalize_account_id")
check("Excel float artifact stripped", normalize_account_id("499.0"), "499")
check("negative float artifact stripped", normalize_account_id("-12.0"), "-12")
check("real decimal NOT stripped", normalize_account_id("4.5"), "4.5")
check("text uppercased", normalize_account_id(" zv88 "), "ZV88")
check("nan → blank", normalize_account_id("nan"), "")
check("None → blank", normalize_account_id(None), "")
check("leading zeros preserved", normalize_account_id("09110032"), "09110032")


# ── 2. Sign-aware DIFFERENCE ────────────────────────────────────────────────
print("\n2. compute_difference")
df = pl.DataFrame({
    "ACCOUNTID":     ["POS", "NEG", "ZERO", "RESIDUE"],
    "DEPOSIT NMASS": [100.0, 15474.71, 100.0, 50.0],
    # RESIDUE: a float artefact that must round to 0.00 and take the >= 0 branch
    "DEPOSIT CTCL":  [130.0, -11533.71, 100.0, -0.0000001],
})
out = compute_difference(df)
d = {r["ACCOUNTID"]: r for r in out.to_dicts()}
check("CTCL>=0 → CTCL - NMASS", d["POS"]["DIFFERENCE(G-F)"], 30.0)
check("CTCL<0  → CTCL + NMASS", d["NEG"]["DIFFERENCE(G-F)"], 3941.0)
check("equal figures → 0", d["ZERO"]["DIFFERENCE(G-F)"], 0.0)
check("float residue rounds to 0.00", d["RESIDUE"]["DEPOSIT CTCL"], 0.0)
check("residue takes the >=0 branch", d["RESIDUE"]["DIFFERENCE(G-F)"], -50.0)


# ── 3. REMARKS priority ─────────────────────────────────────────────────────
print("\n3. apply_remarks")
kambala_ids = {"KB1", "499"}
virtual_ids = {"ZV88"}
rows = pl.DataFrame({
    "ACCOUNTID":     ["CPC1", "ZV88", "ZV88RICH", "EXCESS", "OKNEG", "DEBIT", "BLANKZONE", "499.0"],
    "Zone":          ["ZODELH", "ZODELH", "ZODELH", "ZODELH", "ZODELH", "ZODELH", "", "ZODELH"],
    "CPCode":        ["CP123", "", "", "", "", "", "", ""],
    "MTF_STATUS":    ["", "", "", "", "", "", "", ""],
    "DEPOSIT NMASS": [10.0, 0.0, 500.0, 100.0, 100.0, 15474.71, 0.0, 10.0],
    "DEPOSIT CTCL":  [999.0, 0.0, 100.0, 130.0, 80.0, -11533.71, 0.0, 10.0],
})
rows = compute_difference(rows)
# ZV88RICH shares the virtual-list ID but has real money against it
rows = rows.with_columns(
    pl.when(pl.col("ACCOUNTID") == "ZV88RICH").then(pl.lit("ZV88"))
      .otherwise(pl.col("ACCOUNTID")).alias("ACCOUNTID_SRC"))
res = apply_remarks(rows, kambala_ids, virtual_ids)
r = {x["ACCOUNTID"]: x for x in res.to_dicts()}

check("CPCode set → CPCODE (beats everything)", r["CPC1"]["REMARKS"], "CPCODE")
check("on virtual list, no money → VIRTUAL ID", r["ZV88"]["REMARKS"], "VIRTUAL ID")
check("blank Zone → VIRTUAL ID", r["BLANKZONE"]["REMARKS"], "VIRTUAL ID")
check("CTCL>=0 and diff>0 → LIMIT EXCESS", r["EXCESS"]["REMARKS"], "LIMIT EXCESS")
check("CTCL>=0 and diff<0 → OK", r["OKNEG"]["REMARKS"], "OK")
check("debit balance → OK (never excess)", r["DEBIT"]["REMARKS"], "OK")
check("debit balance diff is CTCL+NMASS", r["DEBIT"]["DIFFERENCE(G-F)"], 3941.0)

# The debit row is the trap: its DIFFERENCE is a big POSITIVE number, so any
# code that ranks the column without filtering REMARKS will report it as the
# biggest excess. Assert it is genuinely not one.
check("debit row not flagged as excess",
      r["DEBIT"]["REMARKS"] == "LIMIT EXCESS", False)

check("KAMBALA hit via float artifact ('499.0'→'499')",
      r["499.0"]["KAMBALA ALLOCATION STATUS"], "Y")
check("KAMBALA miss → N", r["EXCESS"]["KAMBALA ALLOCATION STATUS"], "N")
check("ACCOUNTID printed unchanged", r["499.0"]["ACCOUNTID"], "499.0")


# ── 4. VIRTUAL ID cleared when real money is present ────────────────────────
print("\n4. false VIRTUAL ID cleared when NMASS > 0")
one = pl.DataFrame({
    "ACCOUNTID": ["ZV88"], "Zone": ["ZODELH"], "CPCode": [""], "MTF_STATUS": [""],
    "DEPOSIT NMASS": [500.0], "DEPOSIT CTCL": [100.0],
})
one = apply_remarks(compute_difference(one), set(), {"ZV88"})
check("virtual ID with deposit is re-classified",
      one.to_dicts()[0]["REMARKS"], "OK")


# ── 5. The real KAMBALA workbook ────────────────────────────────────────────
print("\n5. KAMBALA reference file")
folder = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
path = os.path.join(folder, kambala_name())
try:
    kb, vi = load_kambala_reference(path)
    print(f"  ok    loaded {os.path.basename(path)}")
    print(f"        Sheet1     : {len(kb):,} allocation IDs")
    print(f"        VIRTUAL ID : {len(vi):,} virtual IDs")
    if not kb or not vi:
        _fails.append("KAMBALA workbook loaded but a sheet was empty")
except Exception as e:
    print(f"  FAIL  {type(e).__name__}: {e}")
    print(f"        expected at: {path}")
    _fails.append(f"KAMBALA load failed: {e}")


# ── result ──────────────────────────────────────────────────────────────────
print()
if _fails:
    print(f"{len(_fails)} CHECK(S) FAILED — do NOT run CTCL until these are fixed:\n")
    for f in _fails:
        print(f"   - {f}")
    sys.exit(1)
print("All checks passed.\n")
