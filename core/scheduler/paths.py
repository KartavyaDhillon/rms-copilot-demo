"""Where scheduled processes keep their files.

    <data root>/scheduler/<PROCESS NAME>/inputs    files a process reads (uploaded by hand or picked up from another process)
                                         /outputs   images / reports a process creates
                                         /backup/<dd-mm-yyyy>   used + created files moved here after a successful run
                                         /logs      one text log per run
                                         /state     "already sent today" markers
    <data root>/scheduler/_connections.json         WhatsApp / SMTP / POP3 settings (see connections.py)

<data root> is RMS_DATA_ROOT (e.g. D:\\CONSOLE\\data), the same root every other pipeline uses.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

SUBS = ("inputs", "outputs", "backup", "logs", "state")


def data_root() -> Path:
    v = os.getenv("SCHED_DATA_ROOT", "").strip()        # explicit override (tests use this so they never touch real data)
    if v:
        return Path(v)
    try:
        from core.env_boot import data_root as _dr
        return Path(_dr())
    except Exception:
        return Path(os.getenv("RMS_DATA_ROOT", Path(__file__).resolve().parents[2] / "data"))


def sched_root() -> Path:
    p = data_root() / "scheduler"
    p.mkdir(parents=True, exist_ok=True)
    return p


def proc_dir(folder: str, sub: str = "", create: bool = True) -> Path:
    """data/scheduler/<folder>[/<sub>] — `folder` is the process's display name, `sub` one of SUBS."""
    if not folder or re.search(r'[\\/:*?"<>|]', folder) or folder.strip(". ") != folder:
        raise ValueError(f"bad process folder name: {folder!r}")
    if sub and sub not in SUBS:
        raise ValueError(f"bad sub folder: {sub!r}")
    p = sched_root() / folder / sub if sub else sched_root() / folder
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_process_dirs(folder: str) -> Path:
    base = proc_dir(folder)
    for s in SUBS:
        (base / s).mkdir(parents=True, exist_ok=True)
    return base


def safe_name(name: str) -> str:
    """Strip any path part and unsafe characters from an uploaded file name."""
    n = (name or "").replace("\\", "/").split("/")[-1].strip()
    n = re.sub(r'[<>:"|?*\x00-\x1f]', "_", n)
    if not n or n in (".", ".."):
        raise ValueError("invalid file name")
    return n


def resolve_inside(folder: str, sub: str, name: str) -> Path:
    """Path of an existing file under data/scheduler/<folder>/<sub> — refuses anything that escapes it."""
    base = proc_dir(folder, sub, create=False).resolve()
    p = (base / safe_name(name)).resolve()
    if base not in p.parents:
        raise ValueError("path not allowed")
    return p
