"""Small pieces shared by the report processes (file name starts with '_' so it is not registered as a process)."""
from __future__ import annotations

import datetime as dt
import gzip
import shutil
import zipfile
from pathlib import Path

from core.scheduler import helpers as H
from core.scheduler.catalog import Param

SIGNATURE = "Kartavya Dhillon\nMaster Capital Services Ltd\nTel: 011-66331000 | Ext. 84"


def mail_params(to_default="", cc_default="", with_wait=True, wait_default=0):
    ps = [Param("to", "To", "text", required=True, fmt="emails", scope="process", default=to_default,
                help="E-mail addresses, comma or new-line separated."),
          Param("cc", "Cc", "text", fmt="emails", scope="process", default=cc_default),
          Param("signature", "E-mail signature", "text", default=SIGNATURE)]
    if with_wait:
        ps.append(Param("wait_minutes", "Wait for the file (minutes)", "int", default=wait_default,
                        help="0 = try once. Otherwise keep checking every minute for up to this long."))
    return ps


def recipients(params):
    to, cc = H.parse_emails(params.get("to")), H.parse_emails(params.get("cc"))
    if not to:
        raise ValueError("No 'To' e-mail address set — add it with the Recipients button of this process.")
    return to, cc


def sig(params) -> str:
    return (params.get("signature") or SIGNATURE).strip().replace("\n", "<br>")


def sig_text(params) -> str:
    return (params.get("signature") or SIGNATURE).strip()


def require_trading_day(ctx, d: dt.date = None):
    d = d or dt.date.today()
    if not H.is_trading_day(d):
        raise H.Skipped(f"{d:%d-%m-%Y} is not a trading day (weekend / market holiday) — nothing to do.")


def wait_of(params) -> int:
    try:
        return max(0, int(params.get("wait_minutes") or 0))
    except Exception:
        return 0


def gunzip(src: Path, dest: Path = None) -> Path:
    dest = dest or src.with_suffix("")
    with gzip.open(src, "rb") as fi, open(dest, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    return dest


def unzip_first(src: Path, dest_dir: Path) -> Path:
    with zipfile.ZipFile(src) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not names:
            raise RuntimeError(f"{src.name} is empty.")
        z.extract(names[0], dest_dir)
        return dest_dir / names[0]


def clean_inputs(folder_files, keep=()):
    """Delete temporary downloads (they are re-downloadable); never raises."""
    for f in folder_files:
        try:
            if Path(f).is_file() and Path(f).name not in keep:
                Path(f).unlink()
        except Exception:
            pass
