"""One place that knows WHERE the FinKORP back-office API (boapi) lives.

    KORP_BASE_URL            main address       default  https://boapi.mastertrust.co.in
    KORP_BASE_URL_FALLBACK   spare address      default  http://boapi.mastertrust.co.in:15000   (the old one)

Every script that calls the API asks pick_base() instead of using a typed-in address.  pick_base() tries the main
address first and, only if it does not answer, the spare one - so if the old address ever comes back (or the new one
goes away) nothing needs editing.  The answer is remembered for a few minutes so a run does not probe every time.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import List, Optional

PRIMARY = "https://boapi.mastertrust.co.in"
FALLBACK = "http://boapi.mastertrust.co.in:15000"

_lock = threading.Lock()
_cache = {"base": None, "at": 0.0}
CACHE_SECONDS = 300


def _clean(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    return re.sub(r"/api$", "", u, flags=re.I)             # people sometimes paste ".../api/"


def candidates(preferred: Optional[str] = None) -> List[str]:
    out: List[str] = []
    for u in (preferred, os.getenv("KORP_BASE_URL"), PRIMARY, os.getenv("KORP_BASE_URL_FALLBACK"), FALLBACK):
        u = _clean(u or "")
        if u and u not in out:
            out.append(u)
    return out


def parse_session_key(text: str) -> str:
    """The server answers either JSON ("KEY") or XML (<string ...>KEY</string>) - accept both."""
    s = (text or "").strip()
    m = re.search(r">\s*([^<>\s]+)\s*</string>", s)
    if m:
        return m.group(1)
    return s.replace('"', "").strip()


def _answers(base: str, timeout: float) -> bool:
    import requests
    try:
        r = requests.get(f"{base}/api/Common/SessionKey/Get", timeout=timeout)
        return r.status_code == 200 and len(parse_session_key(r.text)) >= 8
    except Exception:
        return False


def pick_base(preferred: Optional[str] = None, log=print, timeout: float = 8.0, force: bool = False) -> str:
    """The first address that really answers.  Falls back to the first candidate when none does (the caller's own
    request then fails with the normal, readable error)."""
    cands = candidates(preferred)
    with _lock:
        if (not force and _cache["base"] in cands and time.time() - _cache["at"] < CACHE_SECONDS):
            return _cache["base"]
    for i, b in enumerate(cands):
        if _answers(b, timeout):
            if i > 0:
                try:
                    log(f"[KORP] {cands[0]} did not answer - using {b} instead.")
                except Exception:
                    pass
            with _lock:
                _cache.update(base=b, at=time.time())
            return b
    try:
        log(f"[KORP] none of {', '.join(cands)} answered - trying {cands[0]} anyway.")
    except Exception:
        pass
    return cands[0]
