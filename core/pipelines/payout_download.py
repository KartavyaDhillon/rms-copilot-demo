"""
payout_download.py — Payment Request Log download (wrapped as a function)
==========================================================================
Document 10 (PAYMENT_REQUEST_LOG_FILE.py), wrapped in run_payout_download().
Logs into the KORP/BOAPI, fetches today's Payment Request Log, saves a dated
.xlsx into PAYOUT_FOLDER. This is the file payout_check.py then reads.

ONLY changes from the original:
  • SAVE_FOLDER   → .env (PAYOUT_FOLDER)
  • KORP creds    → .env (reuses KORP_BASE_URL, KORP_USERNAME, KORP_PASSWORD,
                          KORP_CLIENT_IP, KORP_CLIENT_MAC, KORP_FIRM_ID)
  • FINANCIALYEAR → computed dynamically (was hardcoded "2026-2027")
  • exit()        → return {"status": "error", ...}
  • wrapped in def run_payout_download(); everything else identical.
"""
import os
import base64
from datetime import datetime
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import pandas as pd
from openpyxl import load_workbook
from dotenv import load_dotenv

load_dotenv()

SAVE_FOLDER = os.getenv("PAYOUT_FOLDER", r"D:\PAYOUT ASHISH")


def run_payout_download():
    """Thin wrapper: adds the AI (Groq) plain-English fallback translation on an
    unrecognized failure, same as nmass_download.py etc. — see _run_payout_download_impl."""
    try:
        return _run_payout_download_impl()
    except SystemExit:
        raise
    except Exception:
        import traceback
        from core.error_translator import translate_error
        tb_lines = traceback.format_exc().splitlines()
        for _l in tb_lines:
            print(_l, flush=True)
        _ai_msg = translate_error("Payout download", tb_lines)
        print(f"[FRIENDLY_MSG] {_ai_msg or 'Payout download failed for an unrecognized reason — check the Logs button or contact tech support.'}", flush=True)
        # Re-raise the ORIGINAL exception (not SystemExit) so any caller using
        # `except Exception` (rather than checking a subprocess exit code) still catches it.
        raise


def _run_payout_download_impl():
    """Fetch today's Payment Request Log from BOAPI → dated .xlsx in PAYOUT_FOLDER."""
    os.makedirs(SAVE_FOLDER, exist_ok=True)
    today = datetime.today().strftime("%Y-%m-%d")
    print("Today's Date:", today)

    from core import korp_api
    BASE       = korp_api.pick_base()      # main address, or the spare one if the main does not answer
    USERNAME   = os.getenv("KORP_USERNAME")
    PASSWORD   = os.getenv("KORP_PASSWORD")
    CLIENT_IP  = os.getenv("KORP_CLIENT_IP")
    CLIENT_MAC = os.getenv("KORP_CLIENT_MAC")
    FIRM_ID    = os.getenv("KORP_FIRM_ID", "1001")

    # Financial year (Apr–Mar) computed dynamically
    now = datetime.now()
    if now.month >= 4:
        fy = f"{now.year}-{now.year + 1}"
    else:
        fy = f"{now.year - 1}-{now.year}"

    print("=" * 35); print("STEP 1 - Getting Session Key"); print("=" * 35)
    r = requests.get(f"{BASE}/api/Common/SessionKey/Get", timeout=30)
    session_key = korp_api.parse_session_key(r.text)
    print("Session Key:", session_key)

    print("\nSTEP 2 - Encrypting Password")
    key = session_key.encode(); iv = session_key.encode()
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(PASSWORD.encode(), 16))
    encrypted_password = base64.b64encode(encrypted).decode()
    print("Password encrypted")

    print("\nSTEP 3 - Login API")
    payload = {"CallFrom": "FINKORP", "UserName": USERNAME, "Password": encrypted_password,
               "Grant_type": "password", "ClientIP": CLIENT_IP, "ClientMAC": CLIENT_MAC}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    login = requests.post(f"{BASE}/api/token", data=payload, headers=headers, timeout=30)
    if login.status_code != 200:
        print("Login Failed"); print(login.text)
        return {"status": "error", "step": "login", "message": login.text[:300]}
    token = login.json()["access_token"]
    print("Login successful, token received")

    print("\nSTEP 4 - Fetching Data From API")
    headers = {"Authorization": f"Bearer {token}", "FIRMID": FIRM_ID, "FINANCIALYEAR": fy}
    params = {"RequestFromDate": today, "RequestToDate": today}
    r = requests.get(f"{BASE}/api/Vouchers/PaymentRequestLog/Get",
                     headers=headers, params=params, timeout=60)
    print("API Status Code:", r.status_code)
    data = r.json()
    print("Total Records Received:", len(data))
    if not data:
        print("No records returned")
        return {"status": "ok", "records": 0, "message": "no records today"}

    print("\nSTEP 5 - Converting JSON to DataFrame")
    df = pd.DataFrame(data)

    df["Account ID"] = df["AccountID"]
    df["UCC"] = df["AccountID"]
    df["Account Name"] = df["AccountName"]
    df["Request Amount"] = df["Amount"]
    df["Release Amount"] = df["ReleaseAmount"]
    df["Running AP Amount"] = df["RunningApplicableAmount"]
    df["Request AP Amount"] = df["APAmount"]
    df["Exchange ID"] = df["ExchangeID"]
    df["Segment ID"] = df["SegmentID"]
    df["Request Date"] = df["RequestDate"]
    df["Bank Account No"] = df["BankAccountNo"]
    df["Payment Mode"] = df["PaymentMode"]
    df["Bank Code"] = df["BankCode"]
    df["Maker"] = df["Maker"]
    df["Maker Time"] = df["MakerTime"]
    df["Approve By"] = df["ApproveBy"]
    df["Approve Date"] = df["ApproveDate"]
    df["Checker"] = df["Checker"]
    df["Checker Time"] = df["CheckerTime"]
    df["Status"] = df["Status"]
    df["Remark"] = df["Remark"]
    df["Bill No"] = df["BillNo"]
    df["Payment Bank Code"] = df["PaymentBankCode"]
    df["Posting Date"] = df["PostingDate"]
    df["Payment Request Type"] = df["PaymentRequestType"]
    df["Zone"] = df["Zone"]
    df["Region"] = df["Region"]
    df["Branch"] = df["Branch"]
    df["Entry Source"] = ""

    column_order = ["Zone", "Region", "Branch", "Account ID", "UCC", "Account Name",
                    "Request Amount", "Release Amount", "Running AP Amount", "Request AP Amount",
                    "Exchange ID", "Segment ID", "Request Date", "Bank Account No", "Payment Mode",
                    "Bank Code", "Maker", "Maker Time", "Approve By", "Approve Date", "Checker",
                    "Checker Time", "Status", "Entry Source", "Remark", "Bill No",
                    "Payment Bank Code", "Posting Date", "Payment Request Type"]
    df = df[column_order]

    date_columns = ["Request Date", "Maker Time", "Approve Date", "Checker Time", "Posting Date"]
    for col in date_columns:
        df[col] = pd.to_datetime(df[col]).dt.strftime("%d-%m-%Y %H:%M:%S")

    filename = os.path.join(SAVE_FOLDER, f"{datetime.now().strftime('%d%m%Y%H%M%S')}.xlsx")
    df.to_excel(filename, index=False)
    print("File Created:", filename)

    wb = load_workbook(filename); ws = wb.active
    for column in ws.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[column_letter].width = max_length + 2
    wb.save(filename)
    print("✅ Excel file saved successfully:", filename)
    return {"status": "ok", "records": len(data), "file": filename}


if __name__ == "__main__":
    print(run_payout_download())
