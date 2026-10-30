"""76 FILES DOWNLOADER - every exchange file of one trading day, in ONE day folder.

The date is the previous trading day unless another date is chosen on the card. The layout is the one the old
D:\\76 FILES DOWNLOADER\\CODES\\new.py made, and NOTHING is copied anywhere else (no C:\\PRIYANKA, C:\\Rajeev Ji,
C:\\MCX_INTRA_SAR ...): the later processes read the files from this folder.

  <console data>\\76 FILES DOWNLOADER\\BACKUPFILES AUTOMATED\\<dd-mm-yyyy>\\
      CM\\ FO\\ CO\\ CD\\         NSE extranet files (API, no captcha), unpacked from .gz
      MCX\\                     MCX file portal (password + TOTP code)
      NCDEX\\                   01292_MGN end file (ncfe.ncdex.com) + NCCL_MARGIN_REP 01-08 (webextranet.ncdex.com)
      MCX & NCDEX EODSAR\\      the CC02 files, 01292_MGN and MCX_MARGIN, kept together as before
      EPI.xlsx                 early pay-in margin (back-office API)

Both NCDEX logins answer their captcha with the 2Captcha auto-solver (core/captcha_solver.py). There is no popup:
when the solver cannot answer, that part of the run fails and the log says why.

Logins (never in code):
  NSE extranet, MCX file portal, back-office API   Scheduler > Connections
  NCDEX NCFE                                        Credentials: CTCL_NCDEX_USER / CTCL_NCDEX_PASSWORD (shared)
  NCDEX web extranet                                Credentials: F70_NCDEX_WEB_USER / F70_NCDEX_WEB_PASSWORD
                                                    (empty = the NCFE login is used)
  NCDEX security answer                             NCDEX_SEC_ANSWER (default "a")

The run itself happens in a child process (python -m core.files70 ...): the server only starts it, shows its log
and can stop it. Run logs: logs/files70/files70__<dd-mm-yyyy>__<time>.log
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import deque
from pathlib import Path
from typing import Callable, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
SUB = "BACKUPFILES AUTOMATED"
EODSAR = "MCX & NCDEX EODSAR"
LOG_DIR = REPO / "logs" / "files70"
STATE_FILE = LOG_DIR / ".state.json"
NCDEX_UCC = os.getenv("F70_NCDEX_UCC", "01292")

COMM_PEAK_SLOTS = ["1100", "1230", "1430", "1530", "1700", "1900", "2030", "2230"]
CM_PEAK_SLOTS = ["1100", "1230", "1430", "1530"]
FO_PEAK_SLOTS = ["1100", "1230", "1430", "1530"]

# order = the order of the run and of the card
GROUPS = [
    ("nse_cm", "NSE CM", "CM"),
    ("nse_fo", "NSE FO", "FO"),
    ("nse_co", "NSE CO", "CO"),
    ("nse_cd", "NSE CD", "CD"),
    ("mcx", "MCX", "MCX"),
    ("ncdex_end", "NCDEX end file", "NCDEX"),
    ("ncdex_peak", "NCDEX peaks 01-08", "NCDEX"),
    ("epi", "EPI", ""),
]
GROUP_KEYS = [g[0] for g in GROUPS]
GROUP_LABEL = {g[0]: g[1] for g in GROUPS}
EODSAR_RX = re.compile(r"^(?:[XCOF]_CC02_|%s_MGN_|MCX_MARGIN_)" % re.escape(NCDEX_UCC), re.I)


# ───────────────────────────── paths / dates ─────────────────────────────
NAME = "76 FILES DOWNLOADER"


def root() -> Path:
    """<console data folder>\\76 FILES DOWNLOADER (same data folder as LIVE ALLOCATION CAPTURE etc.).
    Only FILES76_DIR can move it; the old FILES_DOWNLOADER_DIR (D:\\70 FILES DOWNLOADER) is no longer used."""
    v = os.getenv("FILES76_DIR", "").strip()
    if v:
        return Path(v)
    try:
        from core.scheduler import paths
        base = paths.data_root()
    except Exception:
        base = REPO / "data"
    return Path(base) / NAME


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def day_dir(d: dt.date) -> Path:
    return root() / SUB / dstr(d)


def parse_date(s: Optional[str]) -> dt.date:
    """'YYYY-MM-DD' or 'DD-MM-YYYY'; empty = the previous trading day."""
    s = (s or "").strip()
    if not s:
        return default_date()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError("date must look like 2026-10-29")


def default_date(today: Optional[dt.date] = None) -> dt.date:
    try:
        from core.scheduler import helpers as H
        return H.prev_trading_day(today or dt.date.today())
    except Exception:
        d = (today or dt.date.today()) - dt.timedelta(days=1)
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
        return d


def _member() -> str:
    try:
        from core.scheduler import connections
        return (connections.get("nse").get("member_code") or "06436").strip()
    except Exception:
        return "06436"


def _mcx_user() -> str:
    try:
        from core.scheduler import connections
        return (connections.get("mcx_sftp").get("user") or "56720").strip()
    except Exception:
        return "56720"


# ───────────────────────────── what one day must contain ─────────────────────────────
def plan(d: dt.date, member: Optional[str] = None, mcx_user: Optional[str] = None) -> List[dict]:
    """Every expected file of the day: {group, sub, name | rx+label, src, + how to fetch it}."""
    m = member or _member()
    u = mcx_user or _mcx_user()
    ymd, dmy = d.strftime("%Y%m%d"), d.strftime("%d%m%Y")
    out: List[dict] = []

    def nse(group, sub, seg, folder, gz):
        out.append({"group": group, "sub": sub, "name": gz[:-3], "src": "nse", "seg": seg, "folder": folder, "remote": gz})

    std, rep = "Reports/Standard report", "Reports"
    # CM (16)
    for s in CM_PEAK_SLOTS:
        nse("nse_cm", "CM", "CM", std, f"Margin_NCL_CM_0_CM_{m}_{ymd}_P_{s}.csv.gz")
    nse("nse_cm", "CM", "CM", std, f"Margin_NCL_CM_0_CM_{m}_{ymd}_F_0000.csv.gz")
    nse("nse_cm", "CM", "CM", rep, f"C_MG13_{m}_{dmy}.lis.gz")
    nse("nse_cm", "CM", "CM", rep, f"C_SA01_{m}_{dmy}.lis.gz")
    for i in range(1, 5):
        nse("nse_cm", "CM", "CM", rep, f"C_SA01_P_{m}_{dmy}_i0{i}.lis.gz")
    nse("nse_cm", "CM", "CM", rep, f"C_SA04_{m}_{dmy}.lis.gz")
    nse("nse_cm", "CM", "CM", rep, f"C_CC02_{m}_{dmy}.csv.gz")
    nse("nse_cm", "CM", "CM", rep, f"C_{m}_SEC_PLEDGE_{dmy}_01.csv.gz")
    nse("nse_cm", "CM", "CM", rep, f"C_{m}_SEC_PLEDGE_{dmy}_02.csv.gz")
    nse("nse_cm", "CM", "CM", rep, f"Delivery_NCL_CM_EquityT1_CM_{m}_{ymd}_P_0000.csv.gz")
    # FO (15)
    nse("nse_fo", "FO", "FO", std, f"Margin_NCL_FO_0_CM_{m}_{ymd}_F_0000.csv.gz")
    for s in FO_PEAK_SLOTS:
        nse("nse_fo", "FO", "FO", std, f"Margin_NCL_FO_0_CM_{m}_{ymd}_P_{s}.csv.gz")
    nse("nse_fo", "FO", "FO", rep, f"F_SA01_{m}_{dmy}.lis.gz")
    for i in range(1, 5):
        nse("nse_fo", "FO", "FO", rep, f"F_SA01_P_{m}_{dmy}_i0{i}.lis.gz")
    nse("nse_fo", "FO", "FO", rep, f"F_SA04_{m}_{dmy}.lis.gz")
    nse("nse_fo", "FO", "FO", rep, f"F_CC02_{m}_{dmy}.CSV.gz")
    nse("nse_fo", "FO", "FO", rep, f"F_{m}_SEC_PLEDGE_{dmy}_01.csv.gz")
    nse("nse_fo", "FO", "FO", rep, f"F_{m}_SEC_PLEDGE_{dmy}_02.csv.gz")
    nse("nse_fo", "FO", "FO", rep, f"F_MG13_{m}_{dmy}.lis.gz")
    # CO (22)
    for s in COMM_PEAK_SLOTS:
        nse("nse_co", "CO", "CO", std, f"Margin_NCL_CO_0_CM_{m}_{ymd}_P_{s}.csv.gz")
    nse("nse_co", "CO", "CO", std, f"Margin_NCL_CO_0_CM_{m}_{ymd}_F_0000.csv.gz")
    nse("nse_co", "CO", "CO", rep, f"O_CC02_{m}_{dmy}.CSV.gz")
    nse("nse_co", "CO", "CO", rep, f"O_{m}_SEC_PLEDGE_{dmy}_01.csv.gz")
    nse("nse_co", "CO", "CO", rep, f"O_{m}_SEC_PLEDGE_{dmy}_02.csv.gz")
    nse("nse_co", "CO", "CO", rep, f"O_SA01_{m}_{dmy}.lis.gz")
    for i in range(1, 9):
        nse("nse_co", "CO", "CO", rep, f"O_SA01_P_{m}_{dmy}_i0{i}.lis.gz")
    nse("nse_co", "CO", "CO", rep, f"O_SA04_{m}_{dmy}.lis.gz")
    # CD (2)
    nse("nse_cd", "CD", "CD", rep, f"X_CC02_{m}_{dmy}.CSV.gz")
    nse("nse_cd", "CD", "CD", rep, f"X_SA01_{m}_{dmy}.lis.gz")
    # MCX (11)
    for i in range(1, 9):
        out.append({"group": "mcx", "sub": "MCX", "name": f"MCX_PeakMargin{u}_{ymd}_{i:02d}.csv", "src": "mcx"})
    for kind in ("INTRASAR", "MARGIN", "EODSAR"):
        out.append({"group": "mcx", "sub": "MCX", "name": f"MCX_{kind}_{u}_{ymd}.csv", "src": "mcx"})
    # NCDEX (1 + 8)
    out.append({"group": "ncdex_end", "sub": "NCDEX", "name": None, "src": "ncfe",
                "rx": r"^%s_MGN_%s\d*\.csv$" % (NCDEX_UCC, dmy), "label": f"{NCDEX_UCC}_MGN_{dmy}<time>.CSV"})
    for i in range(1, 9):
        out.append({"group": "ncdex_peak", "sub": "NCDEX", "name": f"NCCL_MARGIN_REP_{NCDEX_UCC}_{ymd}_{i:02d}.csv",
                    "src": "ncdexweb"})
    # EPI (1)
    out.append({"group": "epi", "sub": "", "name": "EPI.xlsx", "src": "epi"})
    return out


def _listing(folder: Path) -> Dict[str, str]:
    """{lower-case name: real name} of the visible files of a folder."""
    try:
        return {n.lower(): n for n in os.listdir(folder)
                if not n.startswith(".") and not n.endswith(".part") and (folder / n).is_file()}
    except OSError:
        return {}


def find(item: dict, base: Path, cache: Optional[dict] = None) -> Optional[Path]:
    """The file on disk for a plan item (any letter case; the NCDEX end file by pattern), or None. Empty = missing."""
    folder = base / item["sub"] if item["sub"] else base
    key = str(folder)
    names = cache.get(key) if cache is not None and key in cache else _listing(folder)
    if cache is not None:
        cache[key] = names
    if item.get("name"):
        real = names.get(item["name"].lower())
        hits = [real] if real else []
    else:
        rx = re.compile(item["rx"], re.I)
        hits = sorted(n for n in names.values() if rx.match(n))
    for h in reversed(hits):
        p = folder / h
        try:
            if p.stat().st_size > 0:
                return p
        except OSError:
            pass
    return None


def status(d: Optional[dt.date] = None) -> dict:
    d = d or default_date()
    base = day_dir(d)
    cache: dict = {}
    groups = []
    found_all = total_all = 0
    items = plan(d)
    for key, label, sub in GROUPS:
        files = []
        for it in items:
            if it["group"] != key:
                continue
            p = find(it, base, cache)
            files.append({"name": p.name if p else (it.get("name") or it.get("label")), "present": bool(p),
                          "size": p.stat().st_size if p else 0})
        n = sum(1 for f in files if f["present"])
        found_all += n
        total_all += len(files)
        groups.append({"key": key, "label": label, "folder": sub or "(day folder)", "found": n, "total": len(files),
                       "files": files})
    eod = sorted(_listing(base / EODSAR).values())
    job = job_status()
    return {"date": dstr(d), "iso": d.isoformat(), "default_iso": default_date().isoformat(), "folder": str(base),
            "exists": base.is_dir(), "groups": groups, "found": found_all, "total": total_all,
            "eodsar": {"found": len(eod), "total": 6, "files": eod}, "job": job, "last": _last_result(d), "auto": auto_info()}


def _last_result(d: dt.date) -> dict:
    try:
        return json.loads((LOG_DIR / f"last_{dstr(d)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def refresh_eodsar(d: dt.date, log: Callable[[str], None] = print) -> int:
    """Copy the CC02 files, the 01292_MGN file and MCX_MARGIN into 'MCX & NCDEX EODSAR' (inside the same day folder,
    as the old downloader kept them). Only what is not there yet. -> number copied."""
    base = day_dir(d)
    dest = base / EODSAR
    n = 0
    for sub in ("CM", "FO", "CO", "CD", "MCX", "NCDEX"):
        for name in _listing(base / sub).values():
            if not EODSAR_RX.match(name):
                continue
            src = base / sub / name
            if src.stat().st_size <= 0 or (dest / name).exists():
                continue
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
            n += 1
            log(f"  {EODSAR}: added {name}")
    return n


# ───────────────────────────── downloads (run inside the worker process) ─────────────────────────────
class _Ctx:
    """What core.scheduler.nse / mcx expect: .log() and .cancelled."""

    def __init__(self, log, cancelled=lambda: False):
        self._log, self._cancelled = log, cancelled

    def log(self, msg):
        self._log(str(msg))

    @property
    def cancelled(self):
        return bool(self._cancelled())


def _save_unpacked(content: bytes, dest: Path) -> int:
    """Write the downloaded .gz content unpacked to `dest` (atomic). A body that is not gzip is written as it is."""
    data = gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return len(data)


def fetch_nse(items: List[dict], base: Path, ctx: _Ctx) -> List[str]:
    """NSE extranet API (login from Scheduler > Connections, token shared with the scheduled NSE processes).
    -> names that NSE does not have."""
    from core.scheduler import nse
    url = f"{nse.BASE}/member/file/download/2.0"
    missing = []
    force = False
    for it in items:
        if ctx.cancelled:
            break
        params = {"segment": it["seg"], "folderPath": it["folder"], "filename": it["remote"]}
        nf = 0
        r = None
        for n in range(1, 9):
            r = nse._send("GET", url, ctx, f"downloading {it['remote']}",
                          headers={"Authorization": f"Bearer {nse._token(ctx, force)}"}, params=params,
                          timeout=max(nse.DOWNLOAD_TIMEOUT, 30))
            force = False
            if nse._bad_token(r):
                ctx.log("NSE: token not accepted - fresh login.")
                force = True
                time.sleep(3)
                continue
            if r.status_code in (429, 500, 502, 503, 504) or nse._cut_short(r):
                ctx.log(f"NSE: server problem (HTTP {r.status_code}) - asking again in {min(30, 3 * n)} s.")
                time.sleep(min(30, 3 * n))
                continue
            if nse._not_found_400(r) and nf < 2:          # NSE sometimes says 'not found' for a file that is there
                nf += 1
                time.sleep(10)
                continue
            break
        if r is None or r.status_code == 404 or nse._not_found_400(r):
            ctx.log(f"  ✗ not on NSE (yet): {it['remote']}")
            missing.append(it["name"])
            continue
        if r.status_code != 200:
            ctx.log(f"  ✗ {it['remote']}: HTTP {r.status_code} {r.text[:120]}")
            missing.append(it["name"])
            continue
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "json" in ctype and len(r.content) < 2000:
            ctx.log(f"  ✗ {it['remote']}: NSE answered with a message - {r.text[:150]}")
            missing.append(it["name"])
            continue
        try:
            size = _save_unpacked(r.content, base / it["sub"] / it["name"])
            ctx.log(f"  ✓ {it['sub']}\\{it['name']} ({size:,} bytes)")
        except Exception as e:
            ctx.log(f"  ✗ {it['remote']}: could not unpack - {type(e).__name__}: {e}")
            missing.append(it["name"])
    return missing


def fetch_mcx(items: List[dict], base: Path, d: dt.date, ctx: _Ctx) -> List[str]:
    """MCX file portal, folder /dd-mm-yyyy (password + TOTP from Scheduler > Connections > MCX file portal)."""
    from core.scheduler import mcx
    folder = dstr(d)
    last = None
    for attempt in range(1, 4):
        try:
            s = mcx.Session(ctx)
            s.login()
            r = s.s.get(mcx.BASE + "/", params={"Command": "List", "Dir": f"/{folder}", "ListFile": "List.json",
                                               "NewWebClient": "1"},
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "X-Csrf-Token": s.csrf,
                                 "X-Requested-With": "XMLHttpRequest"}, verify=False, timeout=30)
            r.raise_for_status()
            names = {(f.get("name") or "").lower(): f.get("name") for f in (r.json().get("files") or [])}
            ctx.log(f"MCX: folder /{folder} has {len(names)} file(s).")
            missing = []
            for it in items:
                if ctx.cancelled:
                    break
                real = names.get(it["name"].lower())
                if not real:
                    ctx.log(f"  ✗ not on the MCX portal (yet): {it['name']}")
                    missing.append(it["name"])
                    continue
                s.download(folder, real, base / "MCX")
                ctx.log(f"  ✓ MCX\\{real}")
            return missing
        except RuntimeError:
            raise                                    # wrong password / MFA - trying again cannot help
        except Exception as e:
            last = e
            ctx.log(f"MCX attempt {attempt}/3 failed: {type(e).__name__}: {e}")
            if attempt < 3:
                time.sleep(5)
    raise RuntimeError(f"MCX portal not reachable after 3 tries - {last}")


def fetch_epi(base: Path, d: dt.date, ctx: _Ctx) -> List[str]:
    """Early pay-in margin from the back-office API (Scheduler > Connections > Back-office API) -> EPI.xlsx."""
    import base64
    import requests
    import pandas as pd
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from core import korp_api
    from core.scheduler import connections
    c = connections.require("backoffice", "base_url", "username", "password", "client_ip", "client_mac")
    b = korp_api.pick_base(c["base_url"], log=ctx.log)
    s = requests.Session()
    key = korp_api.parse_session_key(s.get(f"{b}/api/Common/SessionKey/Get", timeout=30).text).encode()
    pdr = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    pw = base64.b64encode(enc.update(pdr.update(c["password"].encode()) + pdr.finalize()) + enc.finalize()).decode()
    lg = s.post(f"{b}/api/token", data={"CallFrom": "FINKORP", "UserName": c["username"], "Password": pw,
                                       "Grant_type": "password", "ClientIP": c["client_ip"], "ClientMAC": c["client_mac"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    try:
        token = lg.json()["access_token"]
    except Exception:
        raise RuntimeError(f"Back-office login failed (HTTP {lg.status_code}) - check Connections > Back-office API.")
    fy = f"{d.year}-{d.year + 1}" if d.month >= 4 else f"{d.year - 1}-{d.year}"
    r = s.get(f"{b}/api/Vouchers/EarlyPayinMargin/Get",
              headers={"Authorization": f"Bearer {token}", "FIRMID": "1001", "FINANCIALYEAR": fy},
              params={"Exchange": "NSE", "TradeDate": d.isoformat()}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"EPI API answered HTTP {r.status_code}: {r.text[:150]}")
    data = r.json()
    if not data:
        ctx.log("  ✗ EPI: the back office returned no rows for this date.")
        return ["EPI.xlsx"]
    df = pd.DataFrame(data).drop(columns=["FirmID", "Segment"], errors="ignore").rename(columns={
        "TradeDate": "Trade Date", "UCICode": "UCI Code", "AccountID": "Account ID", "AccountName": "Account Name",
        "SettlementNo": "Settlement No", "SettlementType": "Settlement Type", "ScripCode": "Scrip Code",
        "ScripName": "Scrip Name", "RequestedQuantity": "Requested Quantity", "AllocatedQuantity": "Allocated Quantity",
        "VarPer": "Var %", "FinalAmount": "Final Amount"})
    if "Trade Date" in df.columns:
        df["Trade Date"] = pd.to_datetime(df["Trade Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    order = ["Exchange", "Trade Date", "UCI Code", "Account ID", "Account Name", "Settlement No", "Settlement Type",
             "Scrip Code", "Scrip Name", "ISIN", "Symbol", "Series", "Requested Quantity", "Allocated Quantity", "Rate",
             "Amount", "Var %", "Final Amount"]
    df = df[[c for c in order if c in df.columns]]
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / "EPI.xlsx.part"
    with pd.ExcelWriter(tmp, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Sheet1")
        ws = w.sheets["Sheet1"]
        for i, col in enumerate(df.columns):
            ws.set_column(i, i, int(max(df[col].fillna("").astype(str).map(len).max() if len(df) else 0, len(col))) + 2)
    tmp.replace(base / "EPI.xlsx")
    ctx.log(f"  ✓ EPI.xlsx ({len(df):,} rows)")
    return []


# ── NCDEX: shared bits for both sites ──
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/152.0.0.0 Safari/537.36")


class _Restart(Exception):
    """The site answered with an error page: start the whole login again from the beginning."""


def _aes(password: str, hdn: str) -> str:
    import base64
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    k, iv = hdn.split(":", 1)
    return base64.b64encode(AES.new(k.encode(), AES.MODE_CBC, iv.encode()).encrypt(pad(password.encode(), 16))).decode()


def _auto(portal: str):
    """Captcha auto-solver for one login. Always automatic here (no manual popup exists on this card), so only the
    2Captcha key is needed - the header's auto-fill switch is for the cards that can fall back to a person."""
    from core import captcha_solver as cs
    return cs.AutoSession(portal, enabled_fn=lambda: bool(cs.api_key()))


def _solve(auto, image: bytes) -> str:
    text = auto.solve(image)
    if not text:
        raise RuntimeError("the captcha auto-solver could not answer (2Captcha key missing / no balance / "
                           f"{auto.max_tries} wrong answers). Nothing is typed by hand on this card.")
    return text


def _ncdex_creds(web: bool = False):
    u = os.getenv("CTCL_NCDEX_USER", "").strip()
    p = os.getenv("CTCL_NCDEX_PASSWORD", "").strip()
    if web:
        u = os.getenv("F70_NCDEX_WEB_USER", "").strip() or u
        p = os.getenv("F70_NCDEX_WEB_PASSWORD", "").strip() or p
    if not u or not p:
        raise RuntimeError("NCDEX login is not set - add it with the Credentials button of the 76 FILES DOWNLOADER card.")
    return u, p


def _sec_answer() -> str:
    return os.getenv("NCDEX_SEC_ANSWER", "a").strip() or "a"


def _soup(text):
    from bs4 import BeautifulSoup
    return BeautifulSoup(text, "html.parser")


def _fields(s) -> list:
    """Every postable input of an ASP.NET page in document order (duplicates kept)."""
    out = []
    for inp in s.find_all("input"):
        nm = inp.get("name")
        if not nm:
            continue
        t = (inp.get("type") or "text").lower()
        if t in ("image", "submit", "button", "password", "file"):
            continue
        if t in ("checkbox", "radio"):
            if inp.has_attr("checked"):
                out.append((nm, inp.get("value", "on")))
            continue
        out.append((nm, inp.get("value", "")))
    for sel in s.find_all("select"):
        nm = sel.get("name")
        if nm:
            opt = sel.find("option", selected=True) or sel.find("option")
            out.append((nm, opt.get("value", "") if opt else ""))
    for ta in s.find_all("textarea"):
        if ta.get("name"):
            out.append((ta.get("name"), ta.text or ""))
    return out


def _msg(s) -> str:
    b = s.find(id="lblMsgBox") or s.find(id="lblMessage")
    return b.get_text(strip=True) if b else ""


def _is_file(body: bytes, ctype: str = "") -> bool:
    head = body[:400].lstrip().lower()
    return len(body) > 20 and "html" not in (ctype or "").lower() and not head.startswith((b"<!doctype", b"<html", b"<"))


# ── NCDEX NCFE (ncfe.ncdex.com): the 01292_MGN end-of-day margin file ──
NCFE = "https://ncfe.ncdex.com"


class Ncfe:
    """Login (auto captcha + security question + frmSession), the margin download and a real logout.
    The site allows ONE login per user, so the run holds the shared NMASS/NCDEX login lock meanwhile."""

    def __init__(self, ctx: _Ctx):
        import requests
        self.ctx = ctx
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        self.form = {"Content-Type": "application/x-www-form-urlencoded", "Origin": NCFE}

    def logged_in(self, text) -> bool:
        s = _soup(text)
        if s.find(id="txtUserNm") or s.find(id="hdnCaptchaVal"):
            return False
        return bool(s.find(id="ctl00_lblUser") or s.find("input", {"id": "ctl00_imgBtnLogout"}))

    def _captcha(self, page_text) -> bytes:
        import urllib.parse
        img = _soup(page_text).find("img", src=lambda x: x and "Captcha.aspx" in x)
        if not img:
            raise _Restart("no captcha on the login page")
        url = NCFE + "/Login/" + urllib.parse.quote(img["src"], safe=":/?=&")
        for _ in range(3):
            r = self.s.get(url, headers={"Referer": NCFE + "/Login/NCFEMain.aspx",
                                         "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}, timeout=30)
            if r.status_code == 200 and len(r.content) > 100 and not r.content.lstrip()[:1] == b"<":
                return r.content
            time.sleep(2)
        raise _Restart("the captcha picture could not be downloaded")

    def _payload(self, page_text, captcha, user, pw):
        s = _soup(page_text)

        def v(n):
            el = s.find("input", {"name": n})
            return el.get("value", "") if el else ""
        af = v("_AntiForgeryToken") or v("__ANTIFORGERY")
        hdn = s.find(id="hdn")
        if hdn is None:
            raise _Restart("the login form is incomplete")
        cap = s.find(id="hdnCaptchaVal")
        en = s.find(id="hdnEnabledCaptcha")
        return [("_AntiForgeryToken", af), ("__ANTIFORGERY", af), ("__ANTIFORGERY", af),
                ("__VIEWSTATE", v("__VIEWSTATE")), ("__VIEWSTATEGENERATOR", v("__VIEWSTATEGENERATOR")),
                ("__EVENTVALIDATION", v("__EVENTVALIDATION")), ("__RequestVerificationToken", v("__RequestVerificationToken")),
                ("hdn", ""), ("hdnCaptchaVal", cap.get("value", "") if cap else ""),
                ("hdnEnabledCaptcha", en.get("value", "1") if en else "1"),
                ("txtUserNm", user), ("txtPassword", _aes(pw, hdn.get("value", ""))), ("txtCaptcha", captcha),
                ("imgBtnLogin.x", "46"), ("imgBtnLogin.y", "13"), ("hdnUserAgent", UA), ("hidProId", ""), ("hidFile", "")]

    def _security(self, url, r):
        s = _soup(r.text)
        data = [(k, v) for k, v in _fields(s) if "txtanswers" not in k]
        n = 0
        for inp in s.find_all("input"):
            nm = inp.get("name") or ""
            if "txtanswers" in nm:
                data.append((nm, _sec_answer()))
                n += 1
        if not n:
            raise RuntimeError("NCDEX security question page had no answer box.")
        btn = s.find("input", {"type": "image", "id": re.compile("imgBtnSubmit")})
        bn = btn.get("name") if btn else "ctl00$ContentPlaceHolderCentre$imgBtnSubmit"
        data += [(bn + ".x", "19"), (bn + ".y", "19")]
        return self.s.post(url, data=data, headers={**self.form, "Referer": url}, allow_redirects=False, timeout=60)

    def _submit_form(self, url, r):
        import urllib.parse
        form = _soup(r.text).find("form")
        if not form:
            raise RuntimeError("NCDEX session page had no form.")
        data = _fields(_soup(str(form)))
        for inp in form.find_all("input"):
            nm, t = inp.get("name"), (inp.get("type") or "").lower()
            if nm and t == "image":
                data += [(nm + ".x", "10"), (nm + ".y", "10")]
            elif nm and t in ("submit", "button"):
                data.append((nm, inp.get("value", "")))
        action = urllib.parse.urljoin(url, form.get("action") or url)
        return self.s.post(action, data=data, headers={**self.form, "Referer": url}, allow_redirects=False, timeout=60)

    def login(self):
        user, pw = _ncdex_creds()
        login_url = NCFE + "/Login/NCFEMain.aspx"
        last = ""
        for full in range(1, 4):
            try:
                self.s.cookies.clear()
                auto = _auto("ncdex")
                page = self.s.get(login_url, headers={"Referer": login_url}, timeout=60)
                resp = None
                for _ in range(auto.max_tries + 2):
                    if self.ctx.cancelled:
                        raise RuntimeError("stopped")
                    cap = _solve(auto, self._captcha(page.text))
                    resp = self.s.post(login_url, data=self._payload(page.text, cap, user, pw),
                                       headers={**self.form, "Referer": login_url}, allow_redirects=False, timeout=60)
                    if resp.status_code >= 500:
                        raise _Restart(f"NCFE server error HTTP {resp.status_code}")
                    if resp.status_code in (301, 302):
                        auto.accepted()
                        break
                    m = _msg(_soup(resp.text)).lower()
                    if "locked" in m:
                        raise RuntimeError(f"NCDEX (NCFE) account locked: {m} - not retried.")
                    if "invalid" in m and ("username" in m or "password" in m):
                        raise RuntimeError(f"NCDEX (NCFE) wrong user name / password: {m} - not retried.")
                    if "already" in m and "login" in m:
                        auto.accepted()                      # the captcha was right; the second login clears the old session
                        self.ctx.log("NCFE: user already logged in elsewhere - logging in again to clear it.")
                    else:
                        auto.rejected()
                        self.ctx.log(f"NCFE: login not accepted ({m or 'no message'}) - new captcha.")
                    page = resp
                else:
                    raise _Restart("the login was not accepted after several captchas")
                loc, ref = resp.headers.get("Location"), login_url
                for _ in range(8):
                    if not loc:
                        break
                    url = loc if loc.lower().startswith("http") else NCFE + loc
                    r = self.s.get(url, headers={"Referer": ref}, allow_redirects=False, timeout=60)
                    ref, low = url, url.lower()
                    if r.status_code in (301, 302):
                        loc = r.headers.get("Location")
                    elif "authenticateqs" in low or "securityqs" in low:
                        r2 = self._security(url, r)
                        loc = r2.headers.get("Location") if r2.status_code in (301, 302) else None
                    elif "frmsession" in low:
                        r2 = self._submit_form(url, r)
                        loc = r2.headers.get("Location") if r2.status_code in (301, 302) else None
                    else:
                        loc = None
                dash = self.s.get(NCFE + "/Common/Dashboard.aspx", headers={"Referer": ref}, timeout=60)
                if not self.logged_in(dash.text):
                    raise _Restart("the session did not open after the login")
                self.ctx.log("NCFE: logged in.")
                return
            except _Restart as e:
                last = str(e)
                self.ctx.log(f"NCFE login try {full}/3: {e} - starting again in 5 s.")
                time.sleep(5)
        raise RuntimeError(f"NCDEX (NCFE) login failed 3 times - {last}")

    def margin_file(self, d: dt.date, dest: Path) -> str:
        url = NCFE + "/TradeListener/ViewUpdateMargins.aspx"
        page = self.s.get(url, headers={"Referer": NCFE + "/Common/Dashboard.aspx"}, timeout=60)
        if not self.logged_in(page.text):
            raise RuntimeError("NCFE margin page bounced back to the login page.")
        s = _soup(page.text)
        fmap: Dict[str, str] = {}
        for k, v in _fields(s):
            fmap[k] = v                                  # dict: our values below must REPLACE the page defaults
        fmap.update({"ctl00$ContentPlaceHolderCentre$txtDate": d.strftime("%d-%b-%Y"),
                     "ctl00$ContentPlaceHolderCentre$ddlDateType": "BD",
                     "ctl00$ContentPlaceHolderCentre$ddlFilterBy": "0",
                     "ctl00$txtSubject": "Problem in: Client Margin Report", "ctl00$txtProbDesc": ""})
        btn = next((i.get("name") for i in s.find_all("input", {"type": "image"})
                    if "download" in (i.get("name") or "").lower()), None) or "ctl00$ContentPlaceHolderCentre$imgBtnDownload"
        fmap[btn + ".x"], fmap[btn + ".y"] = "8", "11"
        af = fmap.pop("_AntiForgeryToken", None) or fmap.pop("__ANTIFORGERY", None) or ""
        fmap.pop("__ANTIFORGERY", None)
        data = ([("_AntiForgeryToken", af), ("__ANTIFORGERY", af), ("__ANTIFORGERY", af)] if af else []) + list(fmap.items())
        r = self.s.post(url, data=data, headers={**self.form, "Referer": url}, timeout=120)
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*?\s*=\s*(?:UTF-8'')?\"?([^\";\r\n]+)\"?", cd, re.I)
        if not m or not _is_file(r.content, r.headers.get("Content-Type", "")):
            note = _msg(_soup(r.text)) if r.content[:1] == b"<" else ""
            self.ctx.log(f"  ✗ NCDEX end file not given for {d:%d-%b-%Y}" + (f" - {note}" if note else ""))
            return ""
        name = os.path.basename(m.group(1).strip())
        dest.mkdir(parents=True, exist_ok=True)
        tmp = dest / (name + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(dest / name)
        self.ctx.log(f"  ✓ NCDEX\\{name} ({len(r.content):,} bytes)")
        return name

    def logout(self):
        try:
            dash = NCFE + "/Common/Dashboard.aspx"
            pg = self.s.get(dash, headers={"Referer": dash}, timeout=30)
            if not self.logged_in(pg.text):
                return
            data = _fields(_soup(pg.text)) + [("ctl00$imgBtnLogout.x", "17"), ("ctl00$imgBtnLogout.y", "16")]
            r = self.s.post(dash, data=data, headers={**self.form, "Referer": dash}, allow_redirects=False, timeout=30)
            ok = r.status_code in (301, 302) and "NCFEMain" in (r.headers.get("Location") or "")
            self.ctx.log("NCFE: signed out." if ok else f"NCFE: sign-out not confirmed (HTTP {r.status_code}).")
        except Exception as e:
            self.ctx.log(f"NCFE: sign-out problem - {type(e).__name__}: {e}")


def fetch_ncdex_end(base: Path, d: dt.date, ctx: _Ctx) -> List[str]:
    from core import dl_lock
    wait_until = time.time() + int(os.getenv("F70_NCFE_WAIT_MIN", "10")) * 60
    while True:
        try:
            dl_lock.acquire("files70")
            break
        except dl_lock.NmassLoginBusyError as e:
            if time.time() > wait_until or ctx.cancelled:
                raise RuntimeError(f"the NCDEX login stayed busy ({e.owner} is using it) - not downloaded.")
            ctx.log(f"NCFE login is in use by '{e.owner}' - waiting…")
            time.sleep(15)
    n = None
    try:
        n = Ncfe(ctx)
        n.login()
        name = n.margin_file(d, base / "NCDEX")
        return [] if name else [f"{NCDEX_UCC}_MGN_{d:%d%m%Y}<time>.CSV"]
    finally:
        if n is not None:
            n.logout()
        dl_lock.release()


# ── NCDEX web extranet (webextranet.ncdex.com): NCCL_MARGIN_REP 01-08 ──
WEB = "https://webextranet.ncdex.com"
WEB_LOGIN, WEB_DNLD = WEB + "/Login.aspx", WEB + "/LoginSuccessDnld.aspx"
_POSTBACK = re.compile(r"__doPostBack\('([^']+)'", re.I)


def _hidden(s) -> dict:
    keys = ["_AntiForgeryToken", "__ANTIFORGERY", "__EVENTTARGET", "__EVENTARGUMENT", "__VIEWSTATE",
            "__VIEWSTATEGENERATOR", "__VIEWSTATEENCRYPTED", "__EVENTVALIDATION", "__RequestVerificationToken"]
    out = {}
    for k in keys:
        t = s.find("input", {"id": k}) or s.find("input", {"name": k})
        out[k] = (t.get("value") or "") if t else ""
    return out


def _hidden_val(s, name) -> str:
    t = s.find("input", {"id": name}) or s.find("input", {"name": name})
    return (t.get("value") or "") if t is not None else ""


def _fname(s, elem_id):
    t = s.find(attrs={"id": elem_id})
    return t["name"] if t is not None and t.get("name") else elem_id


def _links(s) -> Dict[str, str]:
    out = {}
    for a in s.find_all("a", href=True):
        m = _POSTBACK.search(a["href"])
        txt = a.get_text(strip=True)
        if m and txt and txt.lower() not in out:
            out[txt.lower()] = m.group(1)
    return out


class NcdexWeb:
    def __init__(self, ctx: _Ctx):
        import requests
        self.ctx = ctx
        self.s = requests.Session()
        self.h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                  "Accept-Language": "en-US,en;q=0.9"}
        self.post_h = {**self.h, "Content-Type": "application/x-www-form-urlencoded", "Origin": "null"}

    def login(self):
        user, pw = _ncdex_creds(web=True)
        last = ""
        for full in range(1, 4):
            auto = _auto("ncdex_web")
            self.s.cookies.clear()
            try:
                for _ in range(auto.max_tries + 1):
                    if self.ctx.cancelled:
                        raise RuntimeError("stopped")
                    r = self.s.get(WEB_LOGIN, headers=self.h, timeout=30)
                    if "__verify/fp" in r.text:                     # the site's bot check, answered like a browser
                        self.s.post(WEB + "/__verify/fp?url=%2FLogin.aspx",
                                    json={"version": 1, "src": "", "userAgent": UA, "checks": {}}, headers=self.h, timeout=15)
                        r = self.s.get(WEB_LOGIN, headers=self.h, timeout=30)
                    s = _soup(r.text)
                    hdn = _hidden_val(s, "hdn")
                    capv = _hidden_val(s, "hdnCaptchaVal")
                    if ":" not in hdn:
                        raise _Restart("the login page had no key")
                    img = self.s.get(f"{WEB}/Captcha2.aspx?cpt={capv}&t={int(time.time() * 1000)}", headers=self.h, timeout=20)
                    if img.status_code != 200 or len(img.content) < 100 or img.content.lstrip()[:1] == b"<":
                        raise _Restart("the captcha picture could not be downloaded")
                    cap = _solve(auto, img.content)
                    lr = self.s.post(WEB_LOGIN, data={**_hidden(s), "hdn": "", "hdnCaptchaVal": capv, "txtUserNm": user,
                                                      "txtPassword": _aes(pw, hdn), "txtCaptcha": cap, "cmdSubmit": "Login"},
                                     headers={"User-Agent": UA}, allow_redirects=False, timeout=30)
                    if lr.status_code >= 500:
                        raise _Restart(f"server error HTTP {lr.status_code}")
                    if "AuthenticateQs.aspx" in (lr.headers.get("Location") or ""):
                        auto.accepted()
                        break
                    note = _msg(_soup(lr.text)).lower()
                    if "lock" in note:
                        raise RuntimeError(f"NCDEX web extranet account locked: {note} - not retried.")
                    if "invalid" in note and ("user" in note or "password" in note):
                        auto.accepted()
                        raise RuntimeError(f"NCDEX web extranet wrong user name / password: {note} - not retried.")
                    auto.rejected()
                    self.ctx.log(f"NCDEX web: login not accepted ({note or 'no message'}) - new captcha.")
                else:
                    raise _Restart("the login was not accepted after several captchas")
                ar = self.s.get(WEB + "/AuthenticateQs.aspx", headers=self.h, timeout=30)
                sr = self.s.post(WEB + "/AuthenticateQs.aspx",
                                 data={**_hidden(_soup(ar.text)), "__EVENTTARGET": "btnSubmit", "txtanswers0": _sec_answer(),
                                       "hdnType": "ACTIVE", "hidDate": "", "hdnCounter": "", "Hidden1": ""},
                                 headers={**self.post_h, "Referer": WEB + "/AuthenticateQs.aspx"}, allow_redirects=False, timeout=30)
                if "LoginSuccessDnld.aspx" not in (sr.headers.get("Location") or ""):
                    raise RuntimeError("NCDEX web extranet: the security answer was not accepted (NCDEX_SEC_ANSWER).")
                self.ctx.log("NCDEX web: logged in.")
                return
            except _Restart as e:
                last = str(e)
                self.ctx.log(f"NCDEX web login try {full}/3: {e} - starting again in 5 s.")
                time.sleep(5)
        raise RuntimeError(f"NCDEX web extranet login failed 3 times - {last}")

    def _nav(self, s, target):
        r = self.s.post(WEB_DNLD, data={**_hidden(s), "__EVENTTARGET": target},
                        headers={**self.post_h, "Referer": WEB_DNLD}, timeout=45)
        return _soup(r.text)

    def _listing(self, s, d: dt.date):
        if not s or not s.find(attrs={"id": "txtDate"}):
            return None
        dmy = d.strftime("%d-%b-%Y")
        form = {**_hidden(s), _fname(s, "txtDate"): dmy, _fname(s, "txtToDate"): dmy}
        btn = s.find(attrs={"id": "btnShowFiles"})
        if btn is not None and (btn.get("type") or "").lower() in ("submit", "button", "image"):
            form[_fname(s, "btnShowFiles")] = btn.get("value", "Show Files")
            form["__EVENTTARGET"] = ""
        else:
            form["__EVENTTARGET"] = _fname(s, "btnShowFiles")
        r = self.s.post(WEB_DNLD, data=form, headers={**self.post_h, "Referer": WEB_DNLD}, timeout=60)
        return _soup(r.text)

    def peaks(self, items: List[dict], d: dt.date, dest: Path) -> List[str]:
        s = _soup(self.s.get(WEB_DNLD, headers=self.h, timeout=30).text)
        for folder in ("reports", "dnld"):
            t = next((g for txt, g in _links(s).items() if folder in txt), None)
            if not t:
                raise RuntimeError(f"NCDEX web extranet: folder '{folder}' not found.")
            s = self._nav(s, t)
        base_soup = s
        listing = self._listing(base_soup, d)
        if listing is None:
            raise RuntimeError("NCDEX web extranet: no date filter on the download page.")
        missing = []
        for it in items:
            if self.ctx.cancelled:
                break
            tgt = _links(listing).get(it["name"].lower())
            if not tgt:
                self.ctx.log(f"  ✗ not published by NCDEX (yet): {it['name']}")
                missing.append(it["name"])
                continue
            r = self.s.post(WEB_DNLD, data={**_hidden(listing), "__EVENTTARGET": tgt},
                            headers={**self.post_h, "Referer": WEB_DNLD}, timeout=120)
            if not _is_file(r.content, r.headers.get("Content-Type", "")):
                self.ctx.log(f"  ✗ {it['name']}: the site answered with a page, not the file")
                missing.append(it["name"])
            else:
                dest.mkdir(parents=True, exist_ok=True)
                tmp = dest / (it["name"] + ".part")
                tmp.write_bytes(r.content)
                tmp.replace(dest / it["name"])
                self.ctx.log(f"  ✓ NCDEX\\{it['name']} ({len(r.content):,} bytes)")
            listing = self._listing(base_soup, d) or listing      # fresh viewstate for the next postback
        return missing


def fetch_ncdex_peaks(items: List[dict], base: Path, d: dt.date, ctx: _Ctx) -> List[str]:
    w = NcdexWeb(ctx)
    w.login()
    return w.peaks(items, d, base / "NCDEX")


# ───────────────────────────── one run (worker) ─────────────────────────────
def run(d: dt.date, groups: Optional[List[str]] = None, log: Callable[[str], None] = print,
        cancelled: Callable[[], bool] = lambda: False, progress: Callable[[dict], None] = lambda s: None) -> dict:
    """Download whatever of the chosen groups is not in the day folder yet. Groups do not stop each other."""
    groups = [g for g in GROUP_KEYS if not groups or g in groups]
    base = day_dir(d)
    base.mkdir(parents=True, exist_ok=True)
    ctx = _Ctx(log, cancelled)
    items = plan(d)
    log(f"76 FILES DOWNLOADER - trade date {d:%d-%m-%Y} ({d:%A}) -> {base}")
    log(f"groups: {', '.join(GROUP_LABEL[g] for g in groups)}")
    res = {"date": dstr(d), "groups": {}, "errors": {}, "missing": []}
    for g in groups:
        if cancelled():
            log("Stopped by request.")
            break
        todo = [it for it in items if it["group"] == g and not find(it, base)]
        total = sum(1 for it in items if it["group"] == g)
        progress({"group": g, "msg": f"{GROUP_LABEL[g]}: {len(todo)} to download"})
        if not todo:
            log(f"\n[{GROUP_LABEL[g]}] all {total} file(s) already there - nothing to download.")
            res["groups"][g] = "ok"
            continue
        log(f"\n[{GROUP_LABEL[g]}] {len(todo)} of {total} file(s) to download")
        try:
            if g.startswith("nse_"):
                fetch_nse(todo, base, ctx)
            elif g == "mcx":
                fetch_mcx(todo, base, d, ctx)
            elif g == "ncdex_end":
                fetch_ncdex_end(base, d, ctx)
            elif g == "ncdex_peak":
                fetch_ncdex_peaks(todo, base, d, ctx)
            elif g == "epi":
                fetch_epi(base, d, ctx)
        except Exception as e:
            res["errors"][g] = f"{type(e).__name__}: {e}"
            log(f"[{GROUP_LABEL[g]}] FAILED - {type(e).__name__}: {e}")
    try:
        refresh_eodsar(d, log)
    except Exception as e:
        log(f"{EODSAR}: {type(e).__name__}: {e}")
    for it in items:
        if it["group"] in groups and not find(it, base):
            res["missing"].append(it.get("name") or it.get("label"))
            res["groups"][it["group"]] = "missing"
    for g in groups:
        res["groups"].setdefault(g, "ok")
        if g in res["errors"]:
            res["groups"][g] = "failed"
    res["ok"] = not res["missing"] and not res["errors"]
    res["cancelled"] = bool(cancelled())
    log("")
    if res["ok"]:
        log(f"RESULT: all files are in {base}")
    else:
        log(f"RESULT: {len(res['missing'])} file(s) missing" + (f" - {', '.join(res['missing'][:12])}" +
                                                                ("…" if len(res["missing"]) > 12 else "") if res["missing"] else ""))
        for g, e in res["errors"].items():
            log(f"  {GROUP_LABEL[g]}: {e}")
    return res


# ───────────────────────────── job control (server side) ─────────────────────────────
_JOB = {"proc": None, "lines": deque(maxlen=300), "log": None, "date": None, "by": "", "started": None, "rc": None,
        "groups": None}
_JOB_LOCK = threading.Lock()


def _write_state(st: dict):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _alive(pid) -> bool:
    try:
        import psutil
        return bool(pid) and psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def is_running() -> bool:
    p = _JOB.get("proc")
    if p is not None and p.poll() is None:
        return True
    st = _read_state()
    return bool(st.get("running")) and _alive(st.get("pid"))


def job_status() -> dict:
    st = _read_state()
    running = is_running()
    lines = list(_JOB["lines"])
    if not lines and st.get("log"):
        try:
            lines = Path(st["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-120:]
        except OSError:
            lines = []
    return {"running": running, "date": st.get("date"), "by": st.get("by"), "started": st.get("started"),
            "group": st.get("group") if running else None, "msg": st.get("msg") if running else None,
            "lines": lines[-60:], "log": os.path.basename(st.get("log") or ""), "ended": st.get("ended"),
            "result": st.get("result") if not running else None}


def start(d: dt.date, by: str = "", groups: Optional[List[str]] = None, ip: str = "", trigger: str = "manual") -> dict:
    """Start one run in a child process. -> {"ok": bool, "error"?, "log"}"""
    with _JOB_LOCK:
        if is_running():
            st = _read_state()
            return {"ok": False, "error": f"A download is already running (date {st.get('date')}, started by "
                                          f"{st.get('by') or 'the timer'}) - wait for it or press Stop."}
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now()
        logp = LOG_DIR / f"files70__{dstr(d)}__{ts:%Y%m%d_%H%M%S}.log"
        try:
            from core.pipeline_box import who_started_line
            who = who_started_line(by or None, ip or None)
        except Exception:
            who = f"# started by: {by or 'nobody recorded'}\n"
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# 76 FILES DOWNLOADER  date={dstr(d)}  start={ts:%Y-%m-%d %H:%M:%S}  trigger={trigger}\n{who}")
        cmd = [sys.executable, "-u", "-m", "core.files70", "--date", d.isoformat()]
        if groups:
            cmd += ["--groups", ",".join(groups)]
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        flags = (0x08000000 | 0x00004000) if os.name == "nt" else 0           # no window, below-normal priority
        p = subprocess.Popen(cmd, cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=flags)
        _JOB.update(proc=p, log=str(logp), date=dstr(d), by=by, started=ts.isoformat(timespec="seconds"), rc=None,
                    groups=groups)
        _JOB["lines"].clear()
        _write_state({"running": True, "pid": p.pid, "date": dstr(d), "by": by, "started": _JOB["started"],
                      "log": str(logp), "group": None, "msg": "starting…", "groups": groups})

        def pump():
            with open(logp, "a", encoding="utf-8", errors="replace") as f:
                for line in p.stdout:
                    line = line.rstrip("\r\n")
                    if line.startswith("@@STATE "):               # progress note from the worker, not a log line
                        try:
                            st = _read_state()
                            st.update(json.loads(line[8:]))
                            _write_state(st)
                        except Exception:
                            pass
                        continue
                    f.write(line + "\n")
                    f.flush()
                    _JOB["lines"].append(line)
            rc = p.wait()
            _JOB["rc"] = rc
            st = _read_state()
            st.update(running=False, ended=dt.datetime.now().isoformat(timespec="seconds"), rc=rc)
            _write_state(st)
            try:
                from core import dl_lock                                  # a killed run must not leave the login locked
                info = dl_lock.status() or {}
                if info.get("busy") and info.get("owner") == "files70":
                    dl_lock.release()
            except Exception:
                pass
        threading.Thread(target=pump, daemon=True, name="files70-pump").start()
        return {"ok": True, "log": logp.name, "date": dstr(d)}


def stop() -> bool:
    st = _read_state()
    pid = (_JOB["proc"].pid if _JOB.get("proc") is not None and _JOB["proc"].poll() is None else st.get("pid"))
    if not pid or not is_running():
        return False
    try:
        import psutil
        proc = psutil.Process(int(pid))
        for c in proc.children(recursive=True):
            c.kill()
        proc.kill()
    except Exception:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    try:
        with open(st.get("log") or _JOB.get("log"), "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%H:%M:%S}] Stopped by user.\n")
    except Exception:
        pass
    return True


def wait(poll_s: float = 2.0, cancelled: Callable[[], bool] = lambda: False, on_line: Callable[[str], None] = None,
         timeout_s: int = 3 * 3600) -> Optional[dict]:
    """Block until the running job ends (used by the scheduler process). -> the worker's result dict (or None)."""
    seen = 0
    t0 = time.time()
    while is_running():
        if cancelled():
            stop()
        lines = list(_JOB["lines"])
        if on_line and len(lines) > seen:
            for ln in lines[seen:]:
                on_line(ln)
            seen = len(lines)
        if time.time() - t0 > timeout_s:
            stop()
            break
        time.sleep(poll_s)
    time.sleep(0.5)
    if on_line:
        for ln in list(_JOB["lines"])[seen:]:
            on_line(ln)
    return _read_state().get("result")


def list_logs(limit: int = 60) -> list:
    try:
        names = sorted((p for p in LOG_DIR.glob("files70__*.log")), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.name, "size": p.stat().st_size} for p in names[:limit]]
    except OSError:
        return []


# ───────────────────────────── auto run (the card's own timer - NOT the scheduler) ─────────────────────────────
# Every trading day (holidays and weekends are skipped, same calendar as the scheduler) at the set time the previous trading
# day's files are fetched. Files already in the day folder are skipped. When something is still missing after a run, the
# timer tries again (F76_AUTO_RETRIES times, F76_AUTO_RETRY_MIN minutes apart) and stops at F76_AUTO_UNTIL (default 18:00).
# Settings (card > Credentials): F76_AUTO = on / off,  F76_AUTO_TIME = HH:MM (default 09:00).
AUTO_FILE = LOG_DIR / ".auto.json"
_TICK = {"thread": None, "stop": threading.Event()}


def auto_on() -> bool:
    return (os.getenv("F76_AUTO", "on").strip().lower() or "on") not in ("off", "no", "0", "false")


def _hhmm(v: str, default: str) -> int:
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", v or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        m = re.match(r"^(\d{2}):(\d{2})$", default)
    return int(m.group(1)) * 60 + int(m.group(2))


def auto_time() -> str:
    mins = _hhmm(os.getenv("F76_AUTO_TIME", ""), "09:00")
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _auto_state() -> dict:
    try:
        return json.loads(AUTO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _auto_save(st: dict):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    AUTO_FILE.write_text(json.dumps(st), encoding="utf-8")


def _trading_day(d: dt.date) -> bool:
    try:
        from core.scheduler import helpers as H
        return bool(H.is_trading_day(d))
    except Exception:
        return d.weekday() < 5


def auto_info() -> dict:
    return {"on": auto_on(), "time": auto_time()}


def auto_tick(now: Optional[dt.datetime] = None, starter=None) -> Optional[str]:
    """Called every 30 s. Starts a run when it is time. -> a short reason string when it started one, else None."""
    if not auto_on() or is_running():
        return None
    now = now or dt.datetime.now()
    today = now.date()
    if not _trading_day(today):
        return None
    cur = now.hour * 60 + now.minute
    if cur < _hhmm(os.getenv("F76_AUTO_TIME", ""), "09:00") or cur >= _hhmm(os.getenv("F76_AUTO_UNTIL", ""), "18:00"):
        return None
    d = default_date(today)
    st = _auto_state()
    if st.get("today") != today.isoformat():
        st = {"today": today.isoformat(), "attempts": 0, "last": None, "done": False}
    if st.get("done"):
        return None
    stt = status(d)
    if stt["found"] >= stt["total"]:
        st["done"] = True
        _auto_save(st)
        return None
    try:
        retries = max(0, int(os.getenv("F76_AUTO_RETRIES", "3")))
        gap = max(1, int(os.getenv("F76_AUTO_RETRY_MIN", "15")))
    except ValueError:
        retries, gap = 3, 15
    if st["attempts"] > retries:
        return None
    if st.get("last"):
        try:
            if (now - dt.datetime.fromisoformat(st["last"])).total_seconds() < gap * 60:
                return None
        except ValueError:
            pass
    st["attempts"] += 1
    st["last"] = now.isoformat(timespec="seconds")
    _auto_save(st)
    res = (starter or start)(d, by="auto run (timer)", trigger="auto")
    if not res.get("ok"):
        return None
    return f"{dstr(d)} attempt {st['attempts']}"


def start_ticker(interval_s: int = 30):
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        print("[76-files] auto run timer started.", flush=True)
        while not _TICK["stop"].wait(interval_s):
            try:
                auto_tick()
            except Exception as e:                                    # noqa: BLE001
                print(f"[76-files] timer error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="files76-timer", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


# ───────────────────────────── backup: zips of a day ─────────────────────────────
BACKUP_GROUPS = [(k, l) for k, l, _ in GROUPS] + [("eodsar", EODSAR), ("live", "Live allocation 1-8")]


def _live_day(d: dt.date) -> Optional[Path]:
    try:
        from core import live_allocation as L
        p = L.day_dir(d)
        return p if p.is_dir() else None
    except Exception:
        return None


def backup_files(d: dt.date) -> List[dict]:
    """Every file that can go in a zip of this day: {rel, path, size, group}. rel = '<dd-mm-yyyy>/<folder>/<file>'."""
    base, day = day_dir(d), dstr(d)
    out: List[dict] = []
    seen = set()
    owner: Dict[str, str] = {}
    cache: dict = {}
    for it in plan(d):
        p = find(it, base, cache)
        if p:
            owner[str(p).lower()] = it["group"]

    def add(p: Path, rel: str, group: str):
        if rel.lower() in seen:
            return
        seen.add(rel.lower())
        try:
            out.append({"rel": rel, "path": str(p), "size": p.stat().st_size, "group": group})
        except OSError:
            pass

    if base.is_dir():
        for cur, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for f in sorted(files):
                if f.startswith(".") or f.endswith(".part"):
                    continue
                p = Path(cur) / f
                relp = p.relative_to(base).as_posix()
                top = relp.split("/")[0] if "/" in relp else ""
                if top == EODSAR:
                    g = "eodsar"
                elif top.isdigit():
                    g = "live"                       # slot folders already copied into the day folder by hand
                else:
                    g = owner.get(str(p).lower(), "other")
                add(p, f"{day}/{relp}", g)
    live = _live_day(d)
    if live is not None:
        for slot in sorted((x for x in os.listdir(live) if x.isdigit() and (live / x).is_dir()), key=int):
            for f in sorted(os.listdir(live / slot)):
                if f.startswith(".") or not (live / slot / f).is_file():
                    continue
                add(live / slot / f, f"{day}/{slot}/{f}", "live")
    return out


def make_zip(d: dt.date, groups: Optional[List[str]] = None, rels: Optional[List[str]] = None) -> Path:
    """Zip of the day: everything (groups=None, rels=None), some groups, or hand-picked files (rels)."""
    allf = backup_files(d)
    if rels:
        want = {r.lower() for r in rels}
        pick = [f for f in allf if f["rel"].lower() in want]
    elif groups:
        pick = [f for f in allf if f["group"] in groups]
    else:
        pick = allf
    if not pick:
        raise FileNotFoundError("no files for that choice")
    tmpdir = LOG_DIR / "_zip"
    tmpdir.mkdir(parents=True, exist_ok=True)
    for old in tmpdir.glob("*.zip"):
        try:
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
        except OSError:
            pass
    fd, path = tempfile.mkstemp(suffix=".zip", prefix=f"70files_{dstr(d)}_", dir=tmpdir)
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in pick:
            z.write(f["path"], f["rel"])
    return Path(path)


def file_path(d: dt.date, rel: str) -> Path:
    for f in backup_files(d):
        if f["rel"].lower() == (rel or "").lower():
            return Path(f["path"])
    raise FileNotFoundError(rel)


# ───────────────────────────── worker entry point ─────────────────────────────
def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m core.files70")
    ap.add_argument("--date", default="")
    ap.add_argument("--groups", default="")
    a = ap.parse_args(argv)
    try:
        from core import env_boot                      # the console's .env (logins, folders)
        env_boot.boot()
    except Exception:
        pass
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass

    def log(msg):
        for ln in str(msg).splitlines() or [""]:
            print(f"[{dt.datetime.now():%H:%M:%S}] {ln}", flush=True)

    def progress(st):
        print("@@STATE " + json.dumps(st), flush=True)

    d = parse_date(a.date)
    groups = [g for g in a.groups.split(",") if g] or None
    try:
        res = run(d, groups, log=log, progress=progress)
    except Exception as e:
        import traceback
        log(f"FAILED: {type(e).__name__}: {e}")
        log(traceback.format_exc()[-1500:])
        res = {"date": dstr(d), "ok": False, "errors": {"run": f"{type(e).__name__}: {e}"}, "missing": []}
    res["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    progress({"result": res, "msg": "finished"})
    try:
        (LOG_DIR / f"last_{dstr(d)}.json").write_text(json.dumps(res), encoding="utf-8")
    except OSError:
        pass
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    sys.exit(_main())
