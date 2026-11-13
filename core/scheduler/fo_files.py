"""Shared pieces of the two NSE FO ASM / OTM / MWPL processes (FO ASM OTM MWPL MAIL and FO FILES UPLOAD).

Ported from D:\\CLAUDE\\data\\Final.py (daily e-mail) and D:\\CLAUDE\\data\\nse_upload_senior_test.py (3-file upload).

What the NSE FO files are (member code from Scheduler > Connections > NSE):
    F_OPTCSHDTLS01   FO / Reports        <MEM>_..._DDMMYYYY.CSV.gz      cash-settled options detail (NO header row from NSE)
    ASMOTM           FO / Letters/Dnld   F_<MEM>_ASMOTM_DDMMYYYY.csv    ASM / OTM positions (usually WITH a header row)
    ASM_PDF          FO / Letters/Dnld   <MEM>_F_ASM_DDMMYY.pdf.gz      ASM letter
    MWPLVIOL02       FO / Letters/Dnld   F_MWPLVIOL02_<MEM>_YYYYMMDD.CSV.gz   MWPL violations (only on some days)

Error handling that the two scripts did not have:
  * only a real "not there" answer (404, or 400 with code 714/715) means "file not available". Every other HTTP answer
    is an ERROR (never mistaken for a holiday) - that is done by core.scheduler.nse.download;
  * if the exact name is not served, NSE's own folder listing is searched for the same file under another spelling
    (letter case, .gz or plain, 2- or 4-digit year in the PDF name) - fetch();
  * a file that is really a plain file under a .gz name is kept; a damaged / cut-off .gz is an error, not "no file";
  * the header of ASMOTM is removed only when line 1 really is the header (strip_header_copy);
  * every uploaded (file, date) is written to a ledger, so running twice can never upload twice (no lookback window:
    one run handles ONE trade date).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import zlib
from pathlib import Path
from typing import Dict, List, Optional

from core.scheduler import nse, paths
from core.scheduler.processes import _kit as K

# header of F_OPTCSHDTLS01 (NSE sends the file without one; it is added for the e-mailed copy only)
F_OPT_HEADERS = [
    "CM Code", "TM Code/CP Code", "Client Code", "Instrument Type", "Symbol", "Expiry Date", "Strike Price", "Option Type",
    "Previous Day Short Position", "Amount already collected", "Current Day Net Position Long (-)/Short(+)",
    "Current Day Net Value Long (-)/Short(+)", "Current Day Amount Payable (+)/Receivable (-)", "Closing Short Position",
    "Total Amount Collected",
]

# header words of the ASMOTM file (line 1 is dropped only when it holds at least HEADER_MATCH_MIN of them)
ASMOTM_HEADER_TOKENS = ["SYMBOL", "INSTRUMENT", "STRIKE", "OPTION_TYPE", "EXPIRY_DATE", "CLIENT_CODE", "TM_CODE",
                        "CUMULATIVE_SHORT_POS", "CUMULATIVE_NOTIONAL_VALUE", "MARGIN"]
HEADER_MATCH_MIN = 2


# ───────────────────────────── the files ─────────────────────────────
def specs(mem: str) -> List[dict]:
    """The four files. `remote(d)` = the names to ask NSE for (first = normal name, then alternatives);
    `local(d)` = the name after un-gzipping (also the file name sent to the upload API);
    `template` = the literal text of the upload API's fileName field (DDMMYYYY / DDMMYY stays as typed)."""
    def d8(d): return f"{d:%d%m%Y}"
    def d6(d): return f"{d:%d%m%y}"
    def y8(d): return f"{d:%Y%m%d}"
    return [
        {"key": "F_OPTCSHDTLS01", "label": "F_OPTCSHDTLS01 (cash-settled options detail)", "folder": "Reports",
         "remote": lambda d: [f"F_OPTCSHDTLS01_{mem}_{d8(d)}.CSV.gz"],
         "local": lambda d: f"F_OPTCSHDTLS01_{mem}_{d8(d)}.CSV",
         "template": f"F_OPTCSHDTLS01_{mem}_DDMMYYYY.csv", "mime": "text/csv", "strip_header": False, "upload": True},
        {"key": "ASMOTM", "label": "F_ASMOTM (ASM/OTM positions)", "folder": "Letters/Dnld",
         "remote": lambda d: [f"F_{mem}_ASMOTM_{d8(d)}.csv"],
         "local": lambda d: f"F_{mem}_ASMOTM_{d8(d)}.csv",
         "template": f"F_{mem}_ASMOTM_DDMMYYYY.csv", "mime": "text/csv", "strip_header": True, "upload": True},
        {"key": "ASM_PDF", "label": "F_ASM (ASM letter PDF)", "folder": "Letters/Dnld",
         "remote": lambda d: [f"{mem}_F_ASM_{d6(d)}.pdf.gz", f"{mem}_F_ASM_{d8(d)}.pdf.gz"],
         "local": lambda d: f"{mem}_F_ASM_{d6(d)}.pdf",
         "template": f"{mem}_F_ASM_DDMMYY.pdf", "mime": "application/pdf", "strip_header": False, "upload": True},
        {"key": "MWPLVIOL02", "label": "F_MWPLVIOL02 (MWPL violations)", "folder": "Letters/Dnld",
         "remote": lambda d: [f"F_MWPLVIOL02_{mem}_{y8(d)}.CSV.gz"],
         "local": lambda d: f"F_MWPLVIOL02_{mem}_{y8(d)}.CSV",
         "template": "", "mime": "text/csv", "strip_header": False, "upload": False},     # e-mailed only, never uploaded
    ]


# ───────────────────────────── downloading ─────────────────────────────
def _stem(n: str) -> str:
    return n.lower().split(".")[0]


def match_listed_name(listing: List[str], wanted: List[str]) -> Optional[str]:
    """The name NSE really uses for one of the wanted files: same name in another letter case, the same name with or
    without .gz, or the same stem (the stem contains the date, so it cannot be another day's file)."""
    low = {n.lower(): n for n in listing}
    for w in wanted:
        if w.lower() in low:
            return low[w.lower()]
    for w in wanted:
        alt = w.lower()[:-3] if w.lower().endswith(".gz") else w.lower() + ".gz"
        if alt in low:
            return low[alt]
    stems = {}
    for n in listing:
        stems.setdefault(_stem(n), n)
    for w in wanted:
        if _stem(w) in stems:
            return stems[_stem(w)]
    return None


def _unpack(p: Path, ctx) -> Path:
    """.gz -> plain file. A plain file under a .gz name is kept (renamed). A damaged .gz is an ERROR."""
    if not p.name.lower().endswith(".gz"):
        return p
    try:
        out = K.gunzip(p)
    except (EOFError, zlib.error) as e:
        raise RuntimeError(f"{p.name} was downloaded but is damaged / cut off ({type(e).__name__}) - it will be fetched "
                           "again on the next run; it is NOT treated as 'no file'.")
    except OSError:                                          # BadGzipFile: NSE served the plain file under a .gz name
        raw = p.with_suffix("")
        if raw.exists():
            raw.unlink()
        p.replace(raw)
        ctx.log(f"{p.name} is not really gzipped - kept as the plain file {raw.name}.")
        return raw
    p.unlink(missing_ok=True)
    return out


def fetch(spec: dict, d: dt.date, dest: Path, ctx, wait_minutes: int = 0) -> Optional[Path]:
    """Download one file for trade date d, un-gzipped, named spec['local'](d). None = NSE does not have it (after
    waiting up to wait_minutes, trying the alternative names and searching NSE's folder listing)."""
    dest.mkdir(parents=True, exist_ok=True)
    local = spec["local"](d)
    final = dest / local
    if final.is_file() and final.stat().st_size > 0:
        ctx.log(f"Already here, not downloaded again: {local}")
        return final
    names = spec["remote"](d)

    def try_all() -> Optional[Path]:
        for n in names:
            p = nse.download("member", "FO", spec["folder"], n, dest, ctx)
            if p:
                return p
        return None

    p = try_all()
    deadline = time.time() + max(0, int(wait_minutes or 0)) * 60
    while p is None and time.time() < deadline and not getattr(ctx, "cancelled", False):
        ctx.log(f"{spec['label']}: not on NSE yet - checking again in 60 s.")
        time.sleep(60)
        p = try_all()
    if p is None:
        listed = nse.list_dir("member", "FO", spec["folder"], ctx)
        real = match_listed_name(listed, names)
        if real:
            ctx.log(f"NSE lists the file as {real} - fetching it under that name.")
            p = nse.download("member", "FO", spec["folder"], real, dest, ctx)
            if p is None:
                p = nse.download("member", "FO", spec["folder"], real, dest, ctx)      # one more try: it IS listed
            if p is not None and real not in names:
                path = _unpack(p, ctx)
                if path.name != local:
                    if (dest / local).exists():
                        (dest / local).unlink()
                    path.replace(dest / local)
                    ctx.log(f"Renamed {path.name} -> {local}")
                    path = dest / local
                return path
    if p is None:
        return None
    return _unpack(p, ctx)


# ───────────────────────────── headers ─────────────────────────────
def add_header_to_csv(path: Path, header_row: List[str], ctx) -> Path:
    """F_OPTCSHDTLS01 comes without a header: put one on top (in the delimiter the file uses). Does nothing when the
    first line already is the header or the file is empty."""
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    first = text.split("\n", 1)[0].rstrip("\r")
    if not first.strip():
        ctx.log("The file is empty - no header added.")
        return path
    if first.strip().strip('"').lower().startswith(header_row[0].lower()):
        ctx.log("The file already has a header row - left as it is.")
        return path
    delim = None
    for c in (",", "|", "\t", ";"):
        if len(first.split(c)) == len(header_row):
            delim = c
            break
    if delim is None:
        delim = max((",", "|", "\t", ";"), key=lambda c: first.count(c))
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes((delim.join(header_row) + "\r\n" + text).encode("utf-8"))
    tmp.replace(path)
    ctx.log(f"Header row added to {path.name} (delimiter {delim!r}).")
    return path


def looks_like_header(line: str) -> bool:
    norm = line.upper()
    for ch in (",", "|", "\t", ";", '"', "'"):
        norm = norm.replace(ch, " ")
    cells = {c.strip() for c in norm.split() if c.strip()}
    if len([t for t in ASMOTM_HEADER_TOKENS if t in cells]) >= HEADER_MATCH_MIN:
        return True
    return len([t for t in ASMOTM_HEADER_TOKENS if t in norm]) >= HEADER_MATCH_MIN


def strip_header_copy(src: Path, out_dir: Path, ctx) -> Optional[Path]:
    """The copy of an ASMOTM file to upload: the header row removed when there is one (the original is never changed).
    None = nothing worth uploading (empty file / header only)."""
    raw = src.read_bytes()
    if not raw.strip():
        ctx.log(f"{src.name} is empty - nothing to upload.")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / src.name
    i = raw.find(b"\n")
    first = (raw if i == -1 else raw[:i]).rstrip(b"\r\n").decode("utf-8", errors="replace")
    if not looks_like_header(first):
        ctx.log("No header row found - all rows are uploaded as they are.")
        out.write_bytes(raw)
        return out
    body = b"" if i == -1 else raw[i + 1:]
    if not body.strip():
        ctx.log("The file holds only the header row - nothing to upload.")
        return None
    out.write_bytes(body)
    ctx.log(f"Header row removed before upload (it was: {first[:200]}).")
    return out


# ───────────────────────────── ledger: which (file, trade date) are uploaded ─────────────────────────────
def _ledger_file(folder: str) -> Path:
    return paths.proc_dir(folder, "state") / "uploaded_files.json"


def ledger(folder: str) -> Dict[str, Dict[str, str]]:
    try:
        d = json.loads(_ledger_file(folder).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def is_uploaded(folder: str, key: str, d: dt.date) -> bool:
    return d.isoformat() in (ledger(folder).get(key) or {})


def mark_uploaded(folder: str, key: str, d: dt.date, note: str = ""):
    led = ledger(folder)
    led.setdefault(key, {})[d.isoformat()] = (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + (f" {note}" if note else "")).strip()
    for k in list(led):
        led[k] = {a: led[k][a] for a in sorted(led[k])[-400:]}
    tmp = _ledger_file(folder).with_suffix(".tmp")
    tmp.write_text(json.dumps(led, indent=1), encoding="utf-8")
    tmp.replace(_ledger_file(folder))
