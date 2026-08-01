"""
Diagnostic: did the NSE credentials load correctly from .env?
Prints SAFE info only (lengths and masked previews, never the actual secret).
Run:  python check_nse.py   then delete it.
"""
import os
from dotenv import load_dotenv
load_dotenv()

def show(name):
    val = os.getenv(name)
    if val is None:
        print(f"  {name:18} -> MISSING (not found in .env)")
    elif val.strip() == "":
        print(f"  {name:18} -> EMPTY")
    else:
        # show only length + first/last char so you can spot truncation safely
        masked = val[0] + "*" * (len(val) - 2) + val[-1] if len(val) > 2 else "**"
        print(f"  {name:18} -> loaded | length={len(val)} | looks like: {masked}")
        if val != val.strip():
            print(f"  {' '*18}    WARNING: has leading/trailing spaces!")

print("Checking NSE credentials from .env:\n")
show("NSE_MEMBER_CODE")
show("NSE_LOGIN_ID")
show("NSE_PASSWORD")
print("\nFor your password, length should be 24 (it ends in '==').")
print("If length is 22, the '==' got chopped -> wrap the value in quotes in .env.")
