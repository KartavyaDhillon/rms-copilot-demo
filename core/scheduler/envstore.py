"""Reads / writes the Scheduler's settings in the console's .env files.

Layout (so the file stays readable and each process is easy to find):

    # scheduler process: MCX WEB MORNING EMAIL
    SCHED_MCX_WEB_MORNING_EMAIL_ZOCHND_TO='a@x.com,b@x.com'
    ...
    # scheduler connection: E-mail sending (SMTP)
    SCHED_SMTP_SERVER='smtp3.netcore.co.in'

* Recipients, URLs, servers, user names → .env            (shared config)
* Passwords and tokens                  → .env.local      (machine + secret, as core/env_boot.py prescribes)

Saving also updates the running server's environment, so a change applies immediately — no restart.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Dict

_LOCK = threading.Lock()
_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _dir() -> Path:
    v = os.getenv("SCHED_ENV_DIR", "").strip()          # tests point this at a temp folder
    if v:
        return Path(v)
    try:
        from core.env_boot import repo_root
        return Path(repo_root())
    except Exception:
        return Path(__file__).resolve().parents[2]


def shared_file() -> Path:
    return _dir() / ".env"


def local_file() -> Path:
    return _dir() / ".env.local"


def proc_key(process_key: str, param: str) -> str:
    return f"SCHED_{process_key.upper()}_{param.upper()}"


def conn_key(group: str, field: str) -> str:
    return f"SCHED_{group.upper()}_{field.upper()}"


def one_line(v) -> str:
    """A value must live on ONE line of the .env file (a multi-line value breaks the in-place edits and the
    parser).  Lists typed one-per-line (client codes) become comma separated: 'A,\\n B' -> 'A,B'."""
    t = str(v)
    if "\n" in t or "\r" in t:
        t = re.sub(r"[ \t]*[\r\n]+[ \t]*", ",", t)
        t = re.sub(r",\s*(?:,\s*)+", ",", t).strip().strip(",").strip()
    return t


def quote(v) -> str:
    """Single-quoted .env value (python-dotenv understands \\\\ and \\' inside)."""
    return "'" + one_line(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _entry_end(lines, i) -> int:
    """Index of the LAST line of the entry that starts at lines[i] (an old multi-line quoted value spans several)."""
    m = _KEY.match(lines[i])
    if not m:
        return i
    rest = lines[i][m.end():].strip()
    if rest[:1] not in ("'", '"'):
        return i
    q, j, text = rest[0], i, rest[1:]
    while True:
        k, closed = 0, False
        while k < len(text):
            if text[k] == "\\":
                k += 2
                continue
            if text[k] == q:
                closed = True
                break
            k += 1
        if closed or j + 1 >= len(lines):
            return j
        j += 1
        text = lines[j]


def write(path: Path, section: str, updates: Dict[str, str]):
    """Set the given keys in `path`: existing keys are changed in place, new ones are added under
    '# scheduler <section>' (the header is created if needed). Writes atomically."""
    if not updates:
        return
    header = f"# scheduler {section}"
    with _LOCK:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        seen = set()
        i = 0
        while i < len(lines):
            m = _KEY.match(lines[i])
            if m:
                end = _entry_end(lines, i)
                if m.group(1) in updates:
                    lines[i:end + 1] = [f"{m.group(1)}={quote(updates[m.group(1)])}"]   # also drops old continuation lines
                    seen.add(m.group(1))
                    end = i
                i = end + 1
            else:
                i += 1
        new = [k for k in updates if k not in seen]
        if new:
            block = [f"{k}={quote(updates[k])}" for k in new]
            hi = next((i for i, l in enumerate(lines) if l.strip() == header), None)
            if hi is None:
                if lines and lines[-1].strip():
                    lines.append("")
                lines += [header] + block
            else:
                j = hi + 1
                while j < len(lines) and _KEY.match(lines[j]):
                    j = _entry_end(lines, j) + 1
                lines[j:j] = block
        tmp = Path(str(path) + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    for k, v in updates.items():                         # live: no restart needed
        os.environ[k] = one_line(v)
