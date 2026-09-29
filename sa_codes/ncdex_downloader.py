#!/usr/bin/env python3
"""
ncdex_downloader.py  —  NCDEX margin-file downloader  (requests-based, no Selenium)

Standalone:
    python ncdex_downloader.py          # suffix "01" by default
    python ncdex_downloader.py 03       # run suffix "03"

Module:
    from ncdex_downloader import download_ncdex
    path = download_ncdex(output_dir=r"C:\\SHORT ALLOCATION", run_suffix="01")
    # returns absolute path string on success, None on failure

Flow
────
  GET  Login.aspx  (+ fingerprint POST if challenged)
  GET  Captcha2.aspx  →  saved as captcha_ncdex.png  →  typed in terminal
  POST Login.aspx  (AES-CBC encrypted password)  →  AuthenticateQs.aspx
  POST AuthenticateQs.aspx  (security answer)    →  LoginSuccessDnld.aspx
  POST dgFolderDisplay  (navigate "Reports")
  POST dgFolderDisplay  (navigate "dnld")
  POST btnShowFiles (date filter, if present on page)
  POST dgFilesDisplay  (download target CSV)
"""

import os
import re
import sys
import time
import base64
import subprocess
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# ── .env ──────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.getenv("SA_ENV_FILE", r"D:\CLAUDE\rms-copilot\.env"))
except ImportError:
    pass

# ── Config (overrideable via .env) ────────────────────────────────────────────
NCDEX_USER    = os.getenv("SA_NCDEX_USER",            "F01292")
NCDEX_PASS    = os.getenv("SA_NCDEX_PASSWORD",        "aSKUS%2222")
NCDEX_SEC_ANS = os.getenv("SA_NCDEX_SECURITY_ANSWER", "a")
DEFAULT_DIR   = os.getenv("SA_OUTPUT_DIR",             r"C:\SHORT ALLOCATION")

BASE      = "https://webextranet.ncdex.com"
LOGIN_URL = f"{BASE}/Login.aspx"
DL_URL    = f"{BASE}/LoginSuccessDnld.aspx"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)
HEADS = {
    "User-Agent":      _UA,
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
POST_H = {**HEADS, "Content-Type": "application/x-www-form-urlencoded", "Origin": "null"}


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _aes(password: str, hdn: str) -> str:
    """AES-128-CBC encrypt password using KEY:IV from the hdn hidden field."""
    k, iv = hdn.split(":", 1)
    cipher = AES.new(k.encode(), AES.MODE_CBC, iv.encode())
    return base64.b64encode(cipher.encrypt(pad(password.encode(), 16))).decode()


def _val(soup, name: str) -> str:
    tag = soup.find("input", {"id": name}) or soup.find("input", {"name": name})
    return (tag.get("value") or "") if tag else ""


def _hidden(soup) -> dict:
    """Extract all ASP.NET hidden fields needed for a POST."""
    keys = [
        "_AntiForgeryToken",     "__ANTIFORGERY",
        "__EVENTTARGET",         "__EVENTARGUMENT",
        "__VIEWSTATE",           "__VIEWSTATEGENERATOR",
        "__VIEWSTATEENCRYPTED",  "__EVENTVALIDATION",
        "__RequestVerificationToken",
    ]
    d = {k: _val(soup, k) for k in keys}
    d.setdefault("__EVENTTARGET",        "")
    d.setdefault("__EVENTARGUMENT",      "")
    d.setdefault("__VIEWSTATEENCRYPTED", "")
    return d


def _find_link(soup, text: str):
    """Return __doPostBack target of the first link whose text contains `text`."""
    pat = re.compile(r"__doPostBack\('([^']+)'", re.I)
    for a in soup.find_all("a", href=True):
        if text.lower() in a.get_text(strip=True).lower():
            m = pat.search(a["href"])
            if m:
                return m.group(1)
    return None


def _all_file_links(soup) -> list:
    """Return [(filename, postback_target)] for every file link on the page."""
    pat  = re.compile(r"__doPostBack\('([^']+)'", re.I)
    seen = set()
    out  = []
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        m   = pat.search(a["href"])
        if m and txt and txt not in seen:
            seen.add(txt)
            out.append((txt, m.group(1)))
    return out


def _nav(sess: requests.Session, soup, target: str, extra: dict = None):
    """POST a navigation step; return (new_soup, response)."""
    data = {**_hidden(soup), "__EVENTTARGET": target}
    if extra:
        data.update(extra)
    r = sess.post(DL_URL, data=data,
                  headers={**POST_H, "Referer": DL_URL}, timeout=30)
    return BeautifulSoup(r.text, "html.parser"), r


def _show_captcha(image_bytes: bytes) -> str:
    """
    Web-based CAPTCHA: write image + state files so the browser dashboard can
    show the CAPTCHA to the operator.  The dashboard polls GET /sa/captcha/pending,
    displays the image inline, and POSTs the answer to POST /sa/captcha/answer.
    Falls back to terminal input on timeout (120 s) or any file-I/O error.
    """
    state_dir  = Path(DEFAULT_DIR)
    img_file   = state_dir / ".captcha_ncdex.png"
    state_file = state_dir / ".captcha_state"
    ans_file   = state_dir / ".captcha_answer"

    # Debug convenience only — a copy of the captcha dropped in whatever the
    # current working directory happens to be, so it can be eyeballed after a
    # failed run. It used to sit INSIDE the try block below, which meant that
    # when this relative write failed (Errno 22 on the console machine, where
    # the server's cwd is not this folder), the exception aborted the whole
    # dashboard-captcha setup before state_file was written — the dashboard
    # was never told a captcha was waiting, and the run fell through to a
    # terminal prompt that has no terminal. A throwaway debug copy must never
    # be able to break the real mechanism, so it gets its own guard.
    try:
        Path("captcha_ncdex.png").write_bytes(image_bytes)
    except Exception:
        pass

    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        img_file.write_bytes(image_bytes)
        ans_file.write_text("", encoding="utf-8")
        state_file.write_text("PENDING", encoding="utf-8")

        print("\n  [CAPTCHA] Waiting for answer from browser dashboard (timeout 120 s)…")

        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                st = state_file.read_text(encoding="utf-8").strip()
                if st == "ANSWERED":
                    ans = ans_file.read_text(encoding="utf-8").strip()
                    for f in (img_file, state_file, ans_file):
                        try: f.unlink()
                        except Exception: pass
                    print(f"  [CAPTCHA] Answer received from dashboard.")
                    return ans
            except Exception:
                pass

        print("  [CAPTCHA] Timed out — falling back to terminal input")
        for f in (img_file, state_file, ans_file):
            try: f.unlink()
            except Exception: pass

    except Exception as e:
        print(f"  [CAPTCHA] Web state-file error ({e}) — falling back to terminal input")

    # ── Terminal fallback ────────────────────────────────────────────────
    # Only usable when a person is actually sitting at a terminal. Run from
    # the console this is a subprocess with no stdin, so input() used to raise
    # a bare EOFError and kill the whole SA run with a traceback that said
    # nothing about captchas. Check first and fail with a sentence that
    # explains what happened instead.
    p = Path("captcha_ncdex.png")
    try:
        Image.open(BytesIO(image_bytes)).save(p)
        print(f"  [CAPTCHA] Saved to: {p.absolute()}")
    except Exception:
        pass

    _interactive = False
    try:
        _interactive = bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        _interactive = False

    if not _interactive:
        raise RuntimeError(
            "NCDEX needs a CAPTCHA answered, but there is no way to ask for one. "
            "The browser dashboard was not reachable (see the reason above) and "
            "this is running without a terminal, so it cannot be typed either. "
            f"Check that SA_OUTPUT_DIR ({DEFAULT_DIR}) exists and is writable — "
            "that folder is how the dashboard is told a CAPTCHA is waiting."
        )

    # A human is present — open the image for them and ask.
    try:
        if sys.platform == "win32":
            os.startfile(str(p))
        else:
            subprocess.Popen(["open" if sys.platform == "darwin"
                              else "xdg-open", str(p)])
    except Exception:
        pass
    return input("  Enter CAPTCHA text: ").strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def download_ncdex(output_dir: str = None, run_suffix: str = "01") -> str:
    """
    Full NCDEX download flow.

    Parameters
    ----------
    output_dir  : save directory (default: SA_OUTPUT_DIR from .env)
    run_suffix  : "01" / "02" / "03" / "04"

    Returns
    -------
    Absolute path of downloaded file, or None on failure.
    """
    save_dir    = Path(output_dir or DEFAULT_DIR)
    save_dir.mkdir(parents=True, exist_ok=True)

    today_ymd   = datetime.now().strftime("%Y%m%d")   # 20260701
    today_dmy   = datetime.now().strftime("%d-%b-%Y") # 01-Jul-2026

    # Member code: "F01292" → "01292"
    member_code = re.sub(r"^[Ff]", "", NCDEX_USER)
    report_prefix = "NCCL_MARGIN_REP_"   # the ONLY report type this function may ever pick
    target_name = f"{report_prefix}{member_code}_{today_ymd}_{run_suffix}.csv"
    local_path  = save_dir / target_name

    # ── Hard skip ──────────────────────────────────────────────────────────
    if local_path.exists():
        print(f"⏭  NCDEX: {target_name} already exists — skipping download")
        return str(local_path)

    sess = requests.Session()
    print("\n" + "─" * 55)
    print(f"  NCDEX FILE DOWNLOADER    run={run_suffix}")
    print("─" * 55)

    # ── [1]-[3] Login page + fingerprint + CAPTCHA — retried as ONE unit ─────
    # Previously only the captcha IMAGE request was retried (5x), reusing the
    # SAME cap_val/hdn_raw token minted by the very first login-page load. If
    # that token itself had gone stale/invalid (not just a one-off network
    # hiccup — e.g. this run's "Captcha size: 260" 404 every single time),
    # every retry kept hitting the exact same dead token forever, with no way
    # to recover short of restarting the whole download. Now the login page
    # is re-fetched (a fresh token every attempt) as part of each retry, not
    # just the captcha image — same 5-attempt budget, same 2s pause between
    # tries, just redoing "login again" instead of re-asking for the same
    # already-invalid captcha.
    soup = hdn_raw = cap_val = cap_r = None
    for _login_attempt in range(1, 6):
        print(f"[1] Opening login page … (attempt {_login_attempt}/5)")
        r = sess.get(LOGIN_URL, headers=HEADS, timeout=20)

        # ── [2] Fingerprint challenge (if present) ──────────────────────────
        if "__verify/fp" in r.text:
            print("[2] Fingerprint challenge …")
            sess.post(
                f"{BASE}/__verify/fp?url=%2FLogin.aspx",
                json={"version": 1, "src": "", "userAgent": _UA, "checks": {}},
                headers=HEADS, timeout=10,
            )
            r = sess.get(LOGIN_URL, headers=HEADS, timeout=20)

        soup    = BeautifulSoup(r.text, "html.parser")
        hdn_raw = _val(soup, "hdn")
        cap_val = _val(soup, "hdnCaptchaVal")

        if not hdn_raw or ":" not in hdn_raw:
            print(f"  [✗] Could not extract AES key from login page (attempt "
                  f"{_login_attempt}/5) — retrying in 2s...")
            time.sleep(2)
            continue

        # ── [3] CAPTCHA ──────────────────────────────────────────────────────
        print("[3] Downloading CAPTCHA …")
        cap_r = sess.get(
            f"{BASE}/Captcha2.aspx?cpt={cap_val}&t={int(time.time()*1000)}",
            headers=HEADS, timeout=10,
        )
        # NOTE: we sniff the body instead of trusting Content-Type — some NCDEX
        # responses serve the real captcha image without an "image/*" header,
        # which made an earlier, stricter check reject good captchas and spin
        # here silently. We only want to catch genuine failures (a 404/error
        # page saved as "captcha.png"), which always starts with '<' (HTML/XML).
        _body = cap_r.content
        _looks_like_html = _body.lstrip()[:20].lower().startswith(b"<")
        if cap_r.status_code == 200 and len(_body) > 100 and not _looks_like_html:
            break
        print(f"  ⚠️ Captcha download looked invalid (status {cap_r.status_code}, "
              f"attempt {_login_attempt}/5) — refreshing login page + token and retrying in 2s...")
        time.sleep(2)
    else:
        print("  [✗] NCDEX captcha image never downloaded successfully after 5 full attempts "
              "(re-opened the login page and got a fresh token each time).")
        return None
    captcha = _show_captcha(cap_r.content)

    # ── [4] Login POST ─────────────────────────────────────────────────────
    print("\n[4] Logging in …")
    h       = _hidden(soup)
    payload = {
        **h,
        "hdn":         "",
        "hdnCaptchaVal": cap_val,
        "txtUserNm":   NCDEX_USER,
        "txtPassword": _aes(NCDEX_PASS, hdn_raw),
        "txtCaptcha":  captcha,
        "cmdSubmit":   "Login",
    }
    # Use bare User-Agent only (matching working X.py — no Origin, no Referer)
    lr = sess.post(
        LOGIN_URL, data=payload,
        headers={"User-Agent": _UA},
        allow_redirects=False, timeout=20,
    )
    redir = lr.headers.get("Location", "")
    print(f"  HTTP {lr.status_code}  →  {redir or '(no redirect)'}")
    if "AuthenticateQs.aspx" not in redir:
        # Print server message if present (usually shows "Invalid CAPTCHA" etc.)
        _soup_err = BeautifulSoup(lr.text, "html.parser")
        _msg = (_soup_err.find(id="lblMsgBox") or _soup_err.find(id="lblMessage"))
        _msg_text = _msg.get_text(strip=True) if _msg else ""
        if _msg_text:
            print(f"  Server says: {_msg_text}")
        # Confirmed via test_ncdex_login.py: a wrong password shows up here as
        # "Authentication Code NNN Failed. Please Contact Customer Care" in
        # lblMsgBox — not as a plain "wrong password" string, so match on
        # "authentication code" + "failed" rather than guessing exact wording.
        _low = _msg_text.lower()
        if "authentication code" in _low and "failed" in _low:
            print(f"[NCDEX_AUTH_FAIL] {_msg_text}")
        print("  [✗] Login failed — wrong CAPTCHA or credentials.")
        return None
    print("  [✓] Login OK")

    # ── [5] Security question ──────────────────────────────────────────────
    print("[5] Answering security question …")
    ar   = sess.get(f"{BASE}/AuthenticateQs.aspx", headers=HEADS, timeout=20)
    asup = BeautifulSoup(ar.text, "html.parser")
    sec  = NCDEX_SEC_ANS or input("  Security answer: ").strip()

    sr = sess.post(
        f"{BASE}/AuthenticateQs.aspx",
        data={
            **_hidden(asup),
            "__EVENTTARGET": "btnSubmit",
            "txtanswers0":   sec,
            "hdnType":       "ACTIVE",
            "hidDate":       "",
            "hdnCounter":    "",
            "Hidden1":       "",
        },
        headers={**POST_H, "Referer": f"{BASE}/AuthenticateQs.aspx"},
        allow_redirects=False, timeout=20,
    )
    if "LoginSuccessDnld.aspx" not in sr.headers.get("Location", ""):
        print("  [✗] Security answer rejected.")
        return None
    print("  [✓] Security OK")

    # ── [6] Download landing page ──────────────────────────────────────────
    print("[6] Loading download page …")
    dr   = sess.get(DL_URL, headers=HEADS, timeout=20)
    dsup = BeautifulSoup(dr.text, "html.parser")

    # Detect if running interactively (attached terminal) or as a subprocess
    _interactive = sys.stdin.isatty()

    # ── [7] Navigate: Reports folder ──────────────────────────────────────
    print("[7] Navigating to Reports …")
    tgt = _find_link(dsup, "reports")
    if not tgt:
        links = _all_file_links(dsup)
        print("  'Reports' not found. Available links:")
        for txt, _ in links:
            print(f"    → {txt}")
        if not _interactive:
            print("  [✗] Reports folder not found — automated run, cannot prompt. Treating NCDEX as unavailable.")
            return None
        tgt = _find_link(dsup, input("  Folder name to click: ").strip())
    if not tgt:
        print("  [✗] Reports folder not found in page.")
        return None
    dsup, _ = _nav(sess, dsup, tgt)

    # ── [8] Navigate: dnld subfolder ──────────────────────────────────────
    print("[8] Navigating to dnld …")
    tgt = _find_link(dsup, "dnld")
    if not tgt:
        links = _all_file_links(dsup)
        print("  'dnld' not found. Available subfolders:")
        for txt, _ in links:
            print(f"    → {txt}")
        if not _interactive:
            print("  [✗] dnld subfolder not found — automated run, cannot prompt. Treating NCDEX as unavailable.")
            return None
        tgt = _find_link(dsup, input("  Subfolder name: ").strip())
    if not tgt:
        print("  [✗] dnld subfolder not found.")
        return None
    dsup, _ = _nav(sess, dsup, tgt)

    # ── [8b] Date filter (if page has txtDate / btnShowFiles) ─────────────
    if dsup.find("input", {"id": "txtDate"}):
        print("[8b] Date filter — applying today's date …")
        form = {
            **_hidden(dsup),
            "txtDate":   today_dmy,
            "txtToDate": today_dmy,
        }
        btn = dsup.find("input", {"id": "btnShowFiles"})
        if btn and (btn.get("type") or "").lower() == "submit":
            # regular submit button: add it to POST body
            form["btnShowFiles"] = btn.get("value", "Show Files")
            form["__EVENTTARGET"] = ""
        else:
            # link-button via __doPostBack
            form["__EVENTTARGET"] = "btnShowFiles"

        r    = sess.post(DL_URL, data=form,
                         headers={**POST_H, "Referer": DL_URL}, timeout=30)
        dsup = BeautifulSoup(r.text, "html.parser")

    # ── [9] Find the target file ───────────────────────────────────────────
    print(f"[9] Looking for {target_name} …")
    all_links = _all_file_links(dsup)

    # Every fallback below MUST also match the report prefix (NCCL_MARGIN_REP_) —
    # otherwise a same-day, same-member-code file for a DIFFERENT report (e.g.
    # NCDEX_CLIOI_01292_<date>_00.csv — Client-level Open Interest, not the
    # margin report) gets picked by mistake and silently reported as success.
    # That happened for real on 2026-09-29: the margin report wasn't published
    # yet, but the old "today's date anywhere in filename" fallback grabbed
    # NCDEX_CLIOI_01292_20260929_00.csv instead because it also contains
    # today's date and the member code. Requiring the prefix means an
    # unpublished margin report now correctly returns None (see below) instead
    # of ever substituting a different file.
    def _is_margin_report(t):
        return t.upper().startswith(report_prefix.upper())

    # Exact match first (case-insensitive)
    chosen = next(
        ((t, f) for t, f in all_links if t.lower() == target_name.lower()),
        None,
    )
    # Today-date match YYYYMMDD (any suffix) — same report type only
    if not chosen:
        chosen = next(
            ((t, f) for t, f in all_links if _is_margin_report(t) and today_ymd in t),
            None,
        )
    # Fallback: today date in DDMMYYYY format (some NCDEX portals use this) — same report type only
    if not chosen:
        today_dmy8 = datetime.now().strftime("%d%m%Y")
        chosen = next(
            ((t, f) for t, f in all_links if _is_margin_report(t) and today_dmy8 in t),
            None,
        )
    # Fallback: member_code in filename — same report type only (was matching
    # ANY .csv with our member code, e.g. CLIOI, which is the exact bug above)
    if not chosen:
        chosen = next(
            ((t, f) for t, f in all_links
             if _is_margin_report(t) and member_code in t and t.lower().endswith(".csv")),
            None,
        )
    # File not found — log what IS on the page to help diagnose
    if not chosen:
        csv_links = [(t, f) for t, f in all_links
                     if t.lower().endswith((".csv", ".lis"))]
        if not csv_links:
            print("  [✗] No downloadable files found on page.")
            print("  Page text (first 600 chars):")
            print(dsup.get_text()[:600])
        else:
            print(f"  [✗] {target_name} not available today. Files present on NCDEX:")
            for i, (t, _) in enumerate(csv_links):
                print(f"    [{i}] {t}")
        print(f"  ⚠️ NCDEX file treated as unavailable — missing-file flow will decide.")
        return None

    fname, tgt = chosen
    print(f"  → {fname}")

    # ── [10] Download binary ──────────────────────────────────────────────
    print("[10] Downloading file …")
    dl_data = {**_hidden(dsup), "__EVENTTARGET": tgt}
    dl = sess.post(
        DL_URL, data=dl_data,
        headers={**POST_H, "Referer": DL_URL},
        stream=True, timeout=60,
    )

    # Prefer filename from Content-Disposition, fall back to matched link text
    cd  = dl.headers.get("Content-Disposition", "")
    m   = re.search(r'[fF]ile[Nn]ame=["\'"]?([^"\';]+)', cd)
    out_name = m.group(1).strip() if m else fname
    if not out_name:
        out_name = target_name

    out_path = save_dir / out_name
    with open(out_path, "wb") as f:
        for chunk in dl.iter_content(chunk_size=8192):
            f.write(chunk)

    size_kb = out_path.stat().st_size / 1024
    if size_kb < 0.5:
        print(f"\n  [!] Warning: file is very small ({size_kb:.1f} KB).")
        print(f"      Content-Type: {dl.headers.get('Content-Type', '?')}")
        print(f"      This might be an error page, not the actual file.")

    print(f"\n  ✅ Saved: {out_path}  ({size_kb:.1f} KB)")
    return str(out_path)


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    _suffix = sys.argv[1] if len(sys.argv) > 1 else "01"
    _result = download_ncdex(run_suffix=_suffix)
    if _result:
        print(f"\nDone → {_result}")
    else:
        print("\nFailed — check errors above.")
        sys.exit(1)
