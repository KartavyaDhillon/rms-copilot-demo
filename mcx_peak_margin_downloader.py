"""
MCX Peak Margin Standalone Downloader — Runs 01 to 04 only
===========================================================
Replaces the Selenium-based MCX peak download.
Uses the same requests-session approach as the full downloader.

HOW TO TEST:
  1. Set OUTPUT_DIR below to wherever you want the files saved.
  2. Run: python mcx_peak_margin_downloader.py
  3. Pick the trading date in the popup.
  4. If successful, 4 files like MCX_PeakMargin56720_20260702_01.csv ... _04.csv appear.

Once confirmed working, we'll integrate this into the SA downloader.
"""

import os
import time
import requests
import urllib3
from datetime import date
import tkinter as tk
from tkinter import ttk

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================================
# CONFIG — change OUTPUT_DIR for testing
# =============================================
BASE_URL  = "https://sftp.mcxindia.com"
USERNAME  = "56720"
PASSWORDS = ["M56720@123", "M56720@789"]

OUTPUT_DIR  = r"C:\MCX_PEAK_TEST"   # ← where downloaded files land
PEAK_RUNS   = ["01", "02", "03", "04"]  # only these runs

session = requests.Session()


# =============================================
# DATE PICKER
# =============================================
def get_date_from_popup() -> date | None:
    selected = {"value": None}

    def submit(event=None):
        try:
            d = date(int(year_var.get()), int(month_var.get()), int(day_var.get()))
            if d.weekday() >= 5:
                status_lbl.config(text="⚠ Weekend selected — pick a trading day")
                return
            selected["value"] = d
            root.destroy()
        except Exception:
            status_lbl.config(text="❌ Invalid date")

    def on_exit(event=None):
        root.destroy()

    root = tk.Tk()
    root.title("MCX Peak Margin Downloader")
    root.geometry("400x210")
    root.resizable(False, False)
    root.configure(bg="#eef2f7")

    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - 200
    y = (root.winfo_screenheight() // 2) - 105
    root.geometry(f"400x210+{x}+{y}")

    tk.Label(root, text="Select Trading Date", font=("Segoe UI", 13, "bold"),
             bg="#eef2f7", fg="#111827").pack(pady=12)

    today = date.today()
    day_var   = tk.StringVar(value=f"{today.day:02d}")
    month_var = tk.StringVar(value=f"{today.month:02d}")
    year_var  = tk.StringVar(value=str(today.year))

    frame = tk.Frame(root, bg="#eef2f7")
    frame.pack()

    labels_vals = [
        ("Day",   day_var,   [f"{i:02d}" for i in range(1, 32)], 5),
        ("Month", month_var, [f"{i:02d}" for i in range(1, 13)], 5),
        ("Year",  year_var,  [str(i) for i in range(2024, 2031)], 7),
    ]
    for col, (lbl, var, vals, w) in enumerate(labels_vals):
        tk.Label(frame, text=lbl, bg="#eef2f7").grid(row=0, column=col, padx=14)
        ttk.Combobox(frame, textvariable=var, values=vals,
                     width=w, state="readonly").grid(row=1, column=col, padx=14, pady=8)

    tk.Button(root, text="⬇  Download Runs 01–04", command=submit,
              bg="#2563eb", fg="white", font=("Segoe UI", 10, "bold"),
              relief="flat", padx=12, pady=6).pack(pady=8)

    status_lbl = tk.Label(root, text="", fg="#dc2626", bg="#eef2f7", font=("Segoe UI", 9))
    status_lbl.pack()

    root.bind("<Return>", submit)
    root.bind("<Escape>", on_exit)
    root.mainloop()

    return selected["value"]


# =============================================
# MCX LOGIN
# =============================================
def login():
    for pwd in PASSWORDS:
        print(f"🔐 Trying: {pwd}")
        try:
            res = session.post(
                BASE_URL + f"/Web%20Client/Login.xml?Command=Login&Sync={int(time.time()*1000)}",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": BASE_URL,
                    "Referer": BASE_URL + "/?command=logout",
                },
                data={"user": USERNAME, "pword": pwd, "language": "en,US"},
                verify=False,
                timeout=15,
            )
            print(f"   Status: {res.status_code}")
            if "successful" in res.text.lower():
                print("✅ MCX login successful")
                return
            print(f"   Response snippet: {res.text[:120]}")
        except Exception as e:
            print(f"⚠️  Error: {e}")

    raise RuntimeError("❌ MCX login failed — all passwords exhausted")


# =============================================
# SESSION INIT → returns CSRF token
# =============================================
def init_session() -> str:
    print("\n🔧 Initialising MCX session...")

    res = session.get(
        BASE_URL + "/WebClientApi/SessionInfo.json",
        verify=False, timeout=30,
    )
    csrf = res.json()["data"]["csrfToken"]
    print(f"✅ CSRF token acquired")

    hdrs = {"X-Csrf-Token": csrf}
    session.get(BASE_URL + "/WebClientApi/UserInfo.json", headers=hdrs, verify=False, timeout=30)
    session.get(BASE_URL + "/WebClientApi/Mfa.json",      headers=hdrs, verify=False, timeout=30)
    session.get(
        BASE_URL + "/",
        params={"Command": "List", "Dir": "~", "ListFile": "List.json", "NewWebClient": "1"},
        headers=hdrs, verify=False, timeout=30,
    )
    return csrf


# =============================================
# LIST FILES — filter to PEAK_RUNS only
# =============================================
def list_peak_files(folder: str, csrf: str) -> list[str]:
    """
    folder   : date string in MCX format  DD-MM-YYYY  e.g. "02-07-2026"
    Returns  : list of filenames matching PEAK_RUNS, sorted
    """
    print(f"\n📡 Listing MCX files in /{folder} ...")
    res = session.get(
        BASE_URL + "/",
        params={
            "Command": "List",
            "Dir": f"/{folder}",
            "ListFile": "List.json",
            "NewWebClient": "1",
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "X-Csrf-Token": csrf,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE_URL + f"/WebClientNew/Browser/%2F{folder}",
        },
        verify=False, timeout=30,
    )

    data = res.json()
    all_peak = sorted(
        f["name"] for f in data.get("files", [])
        if f.get("name") and "PeakMargin" in f["name"]
    )

    if not all_peak:
        print(f"⚠️  No PeakMargin files found in folder '{folder}'")
        print("   (folder may not exist yet, or date format mismatch)")
        print(f"   Raw file list sample: {[f.get('name') for f in data.get('files', [])[:5]]}")
        return []

    print(f"\n📂 All available PeakMargin files ({len(all_peak)}):")
    for f in all_peak:
        print(f"   {f}")

    # Keep only the runs we want: filename ends with _01.csv, _02.csv, ...
    selected = [f for f in all_peak if any(f.endswith(f"_{r}.csv") for r in PEAK_RUNS)]

    print(f"\n✅ Selected (runs {PEAK_RUNS}): {len(selected)} file(s)")
    for f in selected:
        print(f"   {f}")

    return selected


# =============================================
# DOWNLOAD ONE FILE
# =============================================
def download_file(folder: str, filename: str, csrf: str, output_dir: str):
    dest = os.path.join(output_dir, filename)

    if os.path.exists(dest):
        print(f"⏭️  Already exists, skipping: {filename}")
        return

    print(f"\n⬇️  {filename}")
    res = session.get(
        BASE_URL + "/",
        params={
            "Command": "Download",
            "File": f"/{folder}/{filename}",
            "CsrfToken": csrf,
        },
        headers={"User-Agent": "Mozilla/5.0", "X-Csrf-Token": csrf},
        verify=False, timeout=60, stream=True,
    )

    print(f"   HTTP {res.status_code}")

    if res.status_code != 200:
        print(f"❌ Download failed: HTTP {res.status_code}")
        return

    with open(dest, "wb") as f:
        for chunk in res.iter_content(8192):
            f.write(chunk)

    # ── Validate: server sometimes returns HTTP 200 with an HTML error page
    # ("Your session may have timed out") instead of the actual CSV.
    try:
        with open(dest, "rb") as fchk:
            header = fchk.read(128).lower()
        html_markers = (b"<!doctype", b"<html", b"<head>", b"your session", b"error occurred")
        if any(m in header for m in html_markers):
            os.remove(dest)
            print(f"❌ MCX returned HTML instead of CSV — session timed out or login failed.")
            print(f"   File rejected and deleted: {filename}")
            return
    except Exception as ve:
        print(f"⚠️  Could not validate {filename}: {ve}")

    kb = os.path.getsize(dest) / 1024
    print(f"✅ Saved → {dest}  ({kb:.1f} KB)")


# =============================================
# MAIN
# =============================================
def main():
    trade_date = get_date_from_popup()
    if trade_date is None:
        print("❌ No date selected. Exiting.")
        return

    folder   = trade_date.strftime("%d-%m-%Y")   # MCX folder name format
    print(f"\n📅 Trading Date : {trade_date.strftime('%d-%b-%Y')}")
    print(f"📁 MCX folder   : /{folder}")
    print(f"📂 Saving to    : {OUTPUT_DIR}")
    print(f"🎯 Runs wanted  : {PEAK_RUNS}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # --- Login ---
    login()

    # --- Session + CSRF ---
    csrf = init_session()

    # --- List + filter ---
    files = list_peak_files(folder, csrf)

    if not files:
        print(f"\n⚠️  Nothing to download. Check that run files for {folder} are published on MCX.")
        return

    # --- Download ---
    print(f"\n⬇️  Downloading {len(files)} file(s)...\n")
    for filename in files:
        download_file(folder, filename, csrf, OUTPUT_DIR)

    print(f"\n🎉 Done — {len(files)} MCX Peak Margin file(s) in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
