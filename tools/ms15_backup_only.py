"""Margin Shortage (15-min): back up today's files ONLY - no calculation, no emails, no WhatsApp.

Use after a run that finished its sending but crashed at the backup step. It does exactly what the run's own backup
does: the files in MARGIN_SHORTAGE_FOLDER go to BACKUPFILES AUTOMATED\\<DD-MM-YYYY>\\<NN> (the downloaded-but-unused MCX
file as NOT_USED__<name>, plus USED_FILES.txt), ALLOC_FILES go along, the run counter moves on and the files are archived
to the database.

Run from the console folder:   .venv\\Scripts\\python.exe tools\\ms15_backup_only.py
"""
import os
import shutil
import sys
from datetime import datetime, time as dtime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from core import ctcl_mcx_source as mcxsrc  # noqa: E402
from core import stale_files as stale  # noqa: E402
from core.pipelines import margin_shortage_15min as ms  # noqa: E402

BASE = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
today_str = datetime.now().strftime("%d-%m-%Y")
source = mcxsrc.get_source("ms15")
label = mcxsrc.LABELS[source]

# keep one file per kind (same rule as the run), then find which MCX file was the used one
stale.purge_family(BASE, "CM_ClientLevelCollaterals", (".csv", ".xlsx"), datetime.now().strftime("%d%m%Y"))
stale.purge_family(BASE, "FO_ClientLevelCollaterals", (".csv", ".xlsx"), datetime.now().strftime("%d%m%Y"))
stale.purge_family(BASE, "CO_ClientLevelCollaterals", (".csv", ".xlsx"), datetime.now().strftime("%d%m%Y"))
stale.purge_family(BASE, "ClientNetEffectiveDeposits", (".csv", ".xlsx"), datetime.now().strftime("%d%m%Y"))
mcx = stale.purge_mcx(BASE, datetime.now().strftime("%Y%m%d"))
used = mcx.get(source)
other = mcx.get("deposit" if source == "mcx90" else "mcx90")
unused = [os.path.basename(other)] if other else []

date_root = os.path.join(BASE, "BACKUPFILES AUTOMATED", today_str)
os.makedirs(date_root, exist_ok=True)
runs = [int(d) for d in os.listdir(date_root) if d.isdigit() and os.path.isdir(os.path.join(date_root, d))]
n = max(max(runs) if runs else 0, ms._read_run_counter_n(BASE, today_str)) + 1
folder = os.path.join(date_root, f"{n:02d}")
os.makedirs(folder, exist_ok=True)
last_run = datetime.now().time() >= dtime(14, 55)
print(f"Backing up to {folder}   (MCX used: {label})")

for f in os.listdir(BASE):
    src = os.path.join(BASE, f)
    if os.path.isdir(src) or not f.endswith((".csv", ".xlsx", ".csv.crdownload", ".jpg", ".jpeg")):
        continue
    if "RISK_REPORT" in f and not last_run:
        print(f"skip (not last run): {f}")
        continue
    shutil.copy2(src, os.path.join(folder, stale.mark_unused_name(f, unused)))
    os.remove(src)
    print(f"backed up & removed: {f}")

alloc = os.path.join(BASE, "ALLOC_FILES")
if os.path.isdir(alloc):
    for sub, dst in ((alloc, os.path.join(folder, "ALLOC_FILES")),
                     (os.path.join(alloc, "STATUS"), os.path.join(folder, "ALLOC_FILES", "STATUS"))):
        if not os.path.isdir(sub):
            continue
        for name in os.listdir(sub):
            s = os.path.join(sub, name)
            if os.path.isfile(s):
                os.makedirs(dst, exist_ok=True)
                shutil.copy2(s, os.path.join(dst, name))
                os.remove(s)
                print(f"backed up & removed: ALLOC_FILES\\{name}")

stale.write_used_manifest(folder, {f"MCX file ({label})": used}, unused)
with open(os.path.join(BASE, "run_counter.txt"), "w", encoding="utf-8") as fh:
    fh.write(f"{today_str}|{n}")
print("database archive:", ms.backfill_run_to_db(today_str, n).get("ok"))
print(f"Done - run {n:02d} backed up.")
