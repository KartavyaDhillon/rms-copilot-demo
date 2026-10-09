"""MCX file-portal (sftp.mcxindia.com web client) — login with password + TOTP, list /Common, download.
Credentials: Scheduler → Connections → "MCX file portal"."""
from __future__ import annotations

import datetime as dt
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple

from core.scheduler import connections, helpers as H

BASE = "https://sftp.mcxindia.com"


def _xml(text, tag):
    try:
        el = ET.fromstring(text).find(tag)
        return el.text if el is not None else None
    except ET.ParseError:
        return None


class Session:
    def __init__(self, ctx):
        self.ctx = ctx
        self.requests = H.need("requests")
        self.s = self.requests.Session()
        self.csrf = ""

    def login(self):
        c = connections.require("mcx_sftp", "user", "password")
        pwds = [p for p in (c["password"], c.get("password2")) if p]
        hdr = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE,
               "Referer": BASE + "/?command=logout"}
        for pwd in pwds:
            r = self.s.post(f"{BASE}/Web%20Client/Login.xml?Command=Login&Sync={int(time.time() * 1000)}",
                            headers=hdr, data={"user": c["user"], "pword": pwd, "language": "en,US"},
                            verify=False, timeout=30)
            if "successful" not in r.text:
                continue
            lc = _xml(r.text, "CsrfToken") or ""
            if _xml(r.text, "MFATOTPStatus") == "USES":
                secret = connections.require("mcx_sftp", "totp_secret")["totp_secret"]
                pyotp = H.need("pyotp")
                r2 = self.s.post(f"{BASE}/Web%20Client/Login.xml?Command=TOTPPassin&Sync={int(time.time() * 1000)}",
                                 headers={**hdr, "X-Csrf-Token": lc},
                                 data={"passcode": pyotp.TOTP(secret).now(), "role": "validation"},
                                 verify=False, timeout=30)
                if "successful" not in r2.text:
                    raise RuntimeError("MCX: MFA code was refused — check the MFA secret in Connections.")
            self.ctx.log("MCX login OK.")
            break
        else:
            raise RuntimeError("MCX login failed — check the user name / passwords in Connections.")
        r = self.s.get(BASE + "/WebClientApi/SessionInfo.json", verify=False, timeout=30)
        r.raise_for_status()
        self.csrf = r.json()["data"]["csrfToken"]
        for u in ("/WebClientApi/UserInfo.json", "/WebClientApi/Mfa.json"):
            self.s.get(BASE + u, headers={"X-Csrf-Token": self.csrf}, verify=False, timeout=30)

    def find_bhavcopy(self, d: dt.date) -> Optional[str]:
        r = self.s.get(BASE + "/", params={"Command": "List", "Dir": "/Common", "ListFile": "List.json",
                                           "NewWebClient": "1"},
                       headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                                "X-Csrf-Token": self.csrf, "X-Requested-With": "XMLHttpRequest"},
                       verify=False, timeout=30)
        r.raise_for_status()
        ds = d.strftime("%Y%m%d")
        for f in r.json().get("files", []):
            n = f.get("name") or ""
            if ds in n and "Bhavcopy" in n:
                return n
        return None

    def download(self, folder: str, name: str, dest_dir: Path) -> Path:
        r = self.s.get(BASE + "/", params={"Command": "Download", "File": f"/{folder}/{name}", "CsrfToken": self.csrf},
                       headers={"User-Agent": "Mozilla/5.0", "X-Csrf-Token": self.csrf},
                       verify=False, timeout=60)
        r.raise_for_status()
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / name
        tmp = out.with_suffix(out.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(out)
        self.ctx.log(f"MCX: downloaded {name} ({len(r.content):,} bytes).")
        return out


def latest_bhavcopy(dest_dir: Path, ctx, tries: int = 5) -> Path:
    """Newest MCX Bhavcopy of the last 3 trading days (whole login restarted after a broken connection, 5 s apart)."""
    last = None
    for i in range(1, tries + 1):
        try:
            s = Session(ctx)
            s.login()
            d = dt.date.today()
            for _ in range(3):
                d = H.prev_trading_day(d)
                n = s.find_bhavcopy(d)
                if n:
                    return s.download("Common", n, dest_dir)
            raise FileNotFoundError("No MCX Bhavcopy in the last 3 trading days.")
        except FileNotFoundError:
            raise
        except RuntimeError:
            raise
        except Exception as e:
            last = e
            ctx.log(f"MCX attempt {i}/{tries} failed: {type(e).__name__}: {e}")
            if i < tries:
                ctx.log("MCX: starting the whole login again from the beginning in 5 s…")
                time.sleep(5)
    raise RuntimeError(f"MCX portal unreachable after {tries} tries — {last}")
