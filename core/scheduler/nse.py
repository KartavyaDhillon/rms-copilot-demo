"""NSE Extranet client for scheduled processes (login token cached 55 min, one session at a time).

Credentials come from Scheduler → Connections → "NSE Extranet" (never from code).
URLs are built by hand (no percent-encoding of the folder path) exactly like the old extranetlib did,
because the NSE API expects '/' and spaces as typed.
"""
from __future__ import annotations

import base64
import datetime as dt
import threading
from pathlib import Path
from typing import Optional

from core.scheduler import connections, helpers as H

BASE = "https://www.connect2nse.com/extranet-api"
_LOCK = threading.RLock()                    # NSE may allow only one live session per login → serialise
_TOKEN = {"token": None, "at": None}


def _encrypt(password: str, b64_key: str) -> str:
    cryptography = H.need("cryptography")
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = base64.standard_b64decode(b64_key)
    if len(key) != 32:
        raise RuntimeError("NSE AES key must decode to 32 bytes — check Connections → NSE Extranet.")
    pad = padding.PKCS7(128).padder()
    data = pad.update(password.encode("utf-8")) + pad.finalize()
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return base64.standard_b64encode(enc.update(data) + enc.finalize()).decode()


LOGIN_TIMEOUT = 15       # NSE answers within 15 s or not at all — final, never longer
DOWNLOAD_TIMEOUT = 15    # per-read wait (not the whole download)


def _token_file() -> Path:
    from core.scheduler import paths
    return paths.sched_root() / "_nse_token.json"


def _load_saved_token():
    """Same idea as the old extranetlib token.txt: a token issued < 55 min ago is reused, even after a restart."""
    import json
    try:
        d = json.loads(_token_file().read_text(encoding="utf-8"))
        at = dt.datetime.fromisoformat(d["time"])
        if d.get("token") and dt.datetime.now() - at < dt.timedelta(minutes=55) and at <= dt.datetime.now():
            _TOKEN.update(token=d["token"], at=at)
    except Exception:
        pass


def _save_token():
    import json
    try:
        _token_file().write_text(json.dumps({"token": _TOKEN["token"], "time": _TOKEN["at"].isoformat()}), encoding="utf-8")
    except Exception:
        pass


MAX_TRIES = 15           # NSE sometimes just doesn't answer one request; the next one goes through (seen live)


def _send(method: str, url: str, ctx, what: str, tries: int = MAX_TRIES, **kw):
    """requests call with a log line per attempt and a retry on timeout / connection failure (never on an HTTP answer)."""
    import time
    requests = H.need("requests")
    last = None
    for n in range(1, tries + 1):
        try:
            if ctx:
                ctx.log(f"NSE: {what} (attempt {n}/{tries})…")
            return requests.request(method, url, **kw)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError) as e:    # incl. cut mid-download
            last = e
            if ctx:
                ctx.log(f"NSE: no answer — {type(e).__name__}.")
            if n < tries:
                time.sleep(3)
    raise RuntimeError(f"Cannot reach NSE ({BASE.split('/')[2]}) from this server after {tries} tries ({type(last).__name__}). "
                       "This is a network problem, not a file problem: the NSE Extranet only answers from your registered "
                       "static IP / leased line, and a firewall or proxy can also block it. Check that this machine can "
                       "open the NSE Extranet (same machine the old extranet scripts ran on) and try again.")


def _login(ctx=None) -> str:
    c = connections.require("nse", "member_code", "login_id")
    enc = c.get("encrypted_password")
    if not enc:
        connections.require("nse", "password", "aes_key")
        enc = _encrypt(c["password"], c["aes_key"])
    r = _send("POST", f"{BASE}/login/2.0", ctx, "logging in",
              json={"memberCode": c["member_code"], "loginId": c["login_id"], "password": enc}, timeout=LOGIN_TIMEOUT)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"NSE login: unreadable answer (HTTP {r.status_code}).")
    if j.get("status") != "success" or not j.get("token"):
        codes = j.get("responseCode")
        codes = ", ".join(map(str, codes)) if isinstance(codes, list) else str(codes or "")
        _TOKEN.update(token=None, at=None)
        msg = j.get("message") or j.get("responseMessage") or str(j)[:200]
        # NSE answered, so this is a credentials problem — it is NOT retried (repeat wrong logins can lock the ID).
        raise RuntimeError(f"NSE login refused (code {codes or 'n/a'}): {msg}. Check Connections → NSE Extranet "
                           "(member code, login ID, password / encrypted password, AES key). Not retried.")
    _TOKEN.update(token=j["token"], at=dt.datetime.now())
    _save_token()
    if ctx:
        ctx.log("NSE login OK.")
    return _TOKEN["token"]


def _token(ctx=None, force=False) -> str:
    with _LOCK:
        if not force and not _TOKEN["token"]:
            _load_saved_token()
        at = _TOKEN["at"]
        if force or not _TOKEN["token"] or not at or dt.datetime.now() > at + dt.timedelta(minutes=55):
            return _login(ctx)
        return _TOKEN["token"]


def _bad_token(r) -> bool:
    if r.status_code in (401, 403):
        return True
    try:
        codes = r.json().get("responseCode", [])
        return any(x in ("802", "803") for x in (codes if isinstance(codes, list) else [codes]))
    except Exception:
        return False


def download(kind: str, segment: str, folder: str, filename: str, dest_dir: Path, ctx,
             date: Optional[str] = None) -> Optional[Path]:
    """kind 'member' | 'common'.  Returns the saved path, or None when NSE says the file is not there (yet)."""
    requests = H.need("requests")
    if kind not in ("member", "common"):
        raise ValueError("kind must be 'member' or 'common'")
    url = f"{BASE}/{kind}/file/download/2.0?segment={segment}&folderPath={folder}&filename={filename}"
    if date:
        url += f"&date={date}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    import time
    with _LOCK:
        ctx.log(f"NSE: requesting {filename} from {folder} ({segment}).")
        force = False
        for n in range(1, MAX_TRIES + 1):
            r = _send("GET", url, ctx, f"downloading {filename}",
                      headers={"Authorization": f"Bearer {_token(ctx, force)}"}, timeout=DOWNLOAD_TIMEOUT)
            if not _bad_token(r):
                break
            # 401/403/802/803: NSE did not accept the token (expired, or another login replaced it, or it had
            # not settled yet right after login).  The old script did the same: fresh login, short pause, try again.
            ctx.log(f"NSE: token rejected (HTTP {r.status_code}) — fresh login and retry {n}/{MAX_TRIES}.")
            force = True
            if n < MAX_TRIES:
                time.sleep(5)
    if r.status_code == 404:
        ctx.log(f"NSE: {filename} not available yet (404).")
        return None
    if r.status_code == 400 and "715" in r.text and "FOLDER NOT FOUND" in r.text.upper():
        # NSE gives this (instead of 404) when the day's file / folder has not been published — seen live on
        # 13-10-2026 for PNC_OPT, while the same request answered 404 the day before. Treated as "not there".
        ctx.log(f"NSE: {filename} not available (HTTP 400, code 715 FOLDER NOT FOUND — NSE has nothing published "
                f"under {folder} for it).")
        return None
    if r.status_code != 200:
        raise RuntimeError(f"NSE download of {filename} failed — HTTP {r.status_code}: {r.text[:160]}")
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "json" in ctype and len(r.content) < 2000:          # an error body, not a file
        raise RuntimeError(f"NSE answered with a message instead of {filename}: {r.text[:160]}")
    out = dest_dir / filename
    tmp = out.with_suffix(out.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(out)
    ctx.log(f"NSE: downloaded {filename} ({len(r.content):,} bytes).")
    return out


def download_wait(kind, segment, folder, filename, dest_dir, ctx, wait_minutes: int = 0,
                  date: Optional[str] = None) -> Path:
    """download(), repeated every 60 s for up to wait_minutes; raises FileNotFoundError when it never shows up."""
    import time
    t0 = time.time()
    while True:
        p = download(kind, segment, folder, filename, dest_dir, ctx, date)
        if p:
            return p
        if time.time() - t0 >= max(0, wait_minutes) * 60 or getattr(ctx, "cancelled", False):
            raise FileNotFoundError(f"{filename} is not on NSE yet. Try Run Now later, or raise 'Wait for the file'.")
        ctx.log("Checking again in 60 s…")
        time.sleep(60)
