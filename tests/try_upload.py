"""Try a file upload against a console card's upload API and print the answer (nothing is mocked - it really uploads).

Examples (run from D:\\CONSOLE or the dev copy, with the console running):
    python tests\\try_upload.py "C:\\path\\MTF_TRADE-17112026.txt"
    python tests\\try_upload.py "C:\\path\\TradeBook_17112026.csv" --card mtfic
    python tests\\try_upload.py "C:\\path\\PositionBook_18112026.csv" --card cnc-f11
    python tests\\try_upload.py file.csv --url http://localhost:8000 --cookie "session=..."   # if the console asks for a login

--card:  mtfic  -> POST /mtfic/upload          (TradeBook_<ddmmyyyy>, C_CATG_<MON><YYYY>.T01)
         cnc-f11 -> POST /cnc-f11/upload       (today's PositionBook; the file's saved time is sent too)
         any other card key of the shared card windows (mtf-order, mtf-conc, entrisk, mcx-comm, ncdex-comm) -> POST /<card>/upload
The answer shows which names the card accepted and which it refused, and why.
"""
import argparse
import json
import os
import sys

try:
    import requests
except ImportError:                                              # pragma: no cover
    sys.exit("pip install requests")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--card", default="mtfic")
    ap.add_argument("--url", default=os.getenv("CONSOLE_URL", "http://localhost:8000"))
    ap.add_argument("--cookie", default="", help='e.g. "session=abc" when the console needs a login')
    a = ap.parse_args()
    if not os.path.isfile(a.file):
        sys.exit(f"file not found: {a.file}")
    data = {}
    if a.card == "cnc-f11":
        data["modified"] = str(int(os.path.getmtime(a.file) * 1000))      # what the browser sends as File.lastModified
    headers = {"Cookie": a.cookie} if a.cookie else {}
    with open(a.file, "rb") as fh:
        r = requests.post(f"{a.url.rstrip('/')}/{a.card}/upload", files={"files": (os.path.basename(a.file), fh)},
                          data=data, headers=headers, timeout=120)
    print("HTTP", r.status_code)
    try:
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(r.text[:1500])


if __name__ == "__main__":
    main()
