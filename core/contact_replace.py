"""CHANGE E-MAIL / NUMBER  -  find a phone number or e-mail address everywhere the console stores it and replace it.

Why: an employee leaves; his number / mail id sits in many places (process recipients, WhatsApp alert lists, card
settings, credentials, schedules).  This module
  * scan()     lists every place that holds the OLD value (nothing is changed),
  * replace()  changes the places the user ticked (new value, or removal when the new value is empty),
  * undo()     puts a change back (24 hours, only where the value is still what we wrote),
  * history()  the audit trail: who, when, before, after.

Places searched:   .env and .env.local   (all SCHED_*, MS15_*, CTCL_*, ... settings - this also covers the
                   per-process recipient lists, the common failure-alert numbers and the credentials),
                   data/**/*.json        (card settings, card alert numbers - the scheduler's own folder is skipped),
                   saved schedules      (their parameters / notify settings).
Rules:  phones match on the LAST 10 DIGITS (+91 / 0 / spaces do not matter); e-mails match ignoring upper/lower case;
        a '#name' after a value is kept unless a new name is given; a value already present in the same list is not
        added twice; passwords / tokens / secrets are never touched.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

UNDO_HOURS = 24
_LOCK = threading.Lock()
_SECRET_KEY = re.compile(r"PASS|SECRET|TOKEN|PWD|API_?KEY|SESSION|COOKIE", re.I)
_EMAIL_RE = re.compile(r"^[^@\s,;#]+@[^@\s,;#]+\.[^@\s,;#]+$")
_JSON_SKIP_DIRS = {"scheduler", "_backup_db", "darwinbox_exit", "_state", "logs", "log"}
_JSON_MAX_BYTES = 3_000_000
_JSON_MAX_DEPTH = 3


# ───────────────────────── validation / matching ─────────────────────────
def _digits10(val: str) -> Optional[str]:
    """Last 10 digits of something that LOOKS like a phone number, else None."""
    v = (val or "").strip()
    if not re.fullmatch(r"\+?[\d\s\-()]{10,18}", v):
        return None
    d = re.sub(r"\D", "", v)
    return d[-10:] if 10 <= len(d) <= 13 else None


def clean_value(kind: str, text: str, what: str) -> str:
    """Validated, normalised form: phone -> '+91XXXXXXXXXX', e-mail -> as typed (stripped)."""
    t = (text or "").strip()
    if kind == "phone":
        d = _digits10(t)
        if not d:
            raise ValueError(f"{what}: '{t}' is not a valid phone number - type the 10-digit mobile number.")
        return "+91" + d
    if not _EMAIL_RE.match(t):
        raise ValueError(f"{what}: '{t}' is not a valid e-mail address.")
    return t


def _same(kind: str, token: str, old: str) -> bool:
    if kind == "phone":
        return _digits10(token) == _digits10(old)
    return token.strip().lower() == old.strip().lower()


def _fmt_new(kind: str, original: str, new: str) -> str:
    """The new value written the way the old one was (with or without +91)."""
    if kind == "email":
        return new
    return new if original.strip().startswith("+") else new[-10:]


# ───────────────────────── the text transform ─────────────────────────
def transform(text: str, kind: str, old: str, new: str, new_name: str = "") -> Tuple[str, int]:
    """Replace (or remove, when `new` is empty) `old` inside a list like  'a@x.com #Ravi, b@x.com'.
    Returns (new_text, number_of_entries_changed).  Entries that do not match are returned untouched."""
    if not isinstance(text, str) or not text.strip():
        return text, 0
    pieces = re.split(r"([,;\n]+)", text)
    pairs = [(pieces[i], pieces[i + 1] if i + 1 < len(pieces) else "") for i in range(0, len(pieces), 2)]
    present_new = set()
    if new:
        for p, _s in pairs:
            for tok in p.partition("#")[0].split():
                present_new.add(_digits10(tok) if kind == "phone" else tok.lower())
    key_new = (_digits10(new) if kind == "phone" else new.lower()) if new else None
    out, changed = [], 0
    for part, sep in pairs:
        lead = part[:len(part) - len(part.lstrip())]
        core = part.strip()
        val, hash_, name = core.partition("#")
        toks = val.split() if kind == "email" else ([val.strip()] if val.strip() else [])
        if not any(_same(kind, t, old) for t in toks):
            out.append((part, sep))
            continue
        changed += 1
        kept = []
        for t in toks:
            if not _same(kind, t, old):
                kept.append(t)
                continue
            if new and key_new not in present_new:
                kept.append(_fmt_new(kind, t, new))
                present_new.add(key_new)
            # else: removed, or the new value already sits in this list -> the old one simply drops out
        if not kept:
            continue                                                       # the whole entry goes
        nm = (new_name or name.strip()) if new else name.strip()
        out.append((lead + " ".join(kept) + (f" #{nm}" if nm else ""), sep))
    if not changed:
        return text, 0
    res = ""
    for i, (p, s) in enumerate(out):
        res += p + (s if i < len(out) - 1 else "")
    if out and text.rstrip() != text and not res.endswith(("\n", " ")):
        pass
    return res, changed


# ───────────────────────── friendly names for env keys ─────────────────────────
_PREFIX_NAMES = [
    ("MS15_", "Margin Shortage (15-min) card"), ("CTCL_", "CTCL card"), ("CONC_", "Concentration card"),
    ("MTF_", "MTF card"), ("CNC_F11_", "CNC F11 card"), ("PROBABLE_", "PROBABLE card"), ("SA2_", "Short Allocation"),
    ("SA_", "Short Allocation"), ("FTP_", "FTP"), ("COMMON_", "Common files"), ("KORP_", "KORP"), ("CLI_", "CLI card"),
    ("SENDER_", "Mail sender"), ("PG_", "Database"), ("NSCCL_", "NSCCL"), ("MCX_", "MCX"), ("LAS_", "LAS funding"),
    ("NCDEX_", "NCDEX"), ("WA_", "WhatsApp"), ("RMS_", "Console"), ("R1R2_", "R1-R2 card"), ("PEAK_", "Peak margin"),
    ("PAYOUT_", "Payout check"), ("NSE_", "NSE"), ("MARGIN_", "Margin"), ("OBL_", "Obligation card"),
    ("TPP_", "Tentative Payin Payout card"),
]


def _label(key: str) -> Tuple[str, str]:
    """(where, field) in plain words for a .env key."""
    try:
        m = re.match(r"SCHED_OVR_(.+?)__(.+?)__(.+)$", key)
        if m:
            return f"Own {m.group(2).title()} settings of {m.group(1).replace('_', ' ').title()}", m.group(3).lower()
        if key.startswith("SCHED_"):
            from core.scheduler import catalog, connections
            best = None
            for d in catalog.all_defs():
                for k in {d.key.upper(), re.sub(r"[^A-Za-z0-9]+", "_", d.key).strip("_").upper()}:
                    pre = f"SCHED_{k}_"
                    if key.startswith(pre) and (best is None or len(pre) > len(best[0])):
                        best = (pre, d.name)
            if best:
                return f"Process: {best[1]}", key[len(best[0]):].lower()
            for g, spec in connections.GROUPS.items():
                pre = f"SCHED_{g.upper()}_"
                if key.startswith(pre):
                    return f"Credentials: {spec.get('label') or g.title()}", key[len(pre):].lower()
    except Exception:                                                      # noqa: BLE001 - a label is never worth a failure
        pass
    for pre, name in _PREFIX_NAMES:
        if key.startswith(pre):
            return name, key[len(pre):].lower()
    return "Console settings (.env)", key.lower()


# ───────────────────────── readers ─────────────────────────
def _env_files() -> List[Tuple[str, Path]]:
    from core.scheduler import envstore
    return [(".env", envstore.shared_file()), (".env.local", envstore.local_file())]


def _env_values(path: Path) -> Dict[str, str]:
    from dotenv import dotenv_values
    try:
        return {k: v for k, v in dotenv_values(path, interpolate=False).items() if isinstance(v, str)}
    except Exception:                                                      # noqa: BLE001
        return {}


def _data_root() -> Path:
    from core.scheduler import paths
    return Path(paths.data_root())


def _json_files() -> List[Path]:
    root = _data_root()
    out: List[Path] = []
    if not root.is_dir():
        return out
    for dp, dn, fn in os.walk(root):
        rel = Path(dp).relative_to(root)
        if len(rel.parts) >= _JSON_MAX_DEPTH:
            dn[:] = []
        dn[:] = [d for d in dn if d.lower() not in _JSON_SKIP_DIRS and not d.startswith(".")]
        for f in fn:
            if f.lower().endswith(".json") and not f.startswith(".") and not re.search(r"session|cookie|token", f, re.I):
                p = Path(dp) / f
                try:
                    if p.stat().st_size <= _JSON_MAX_BYTES:
                        out.append(p)
                except OSError:
                    pass
    return sorted(out)


def _walk_json(node, path=()):
    """(path_tuple, string) for every string value in a JSON document (secret-named keys skipped)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if _SECRET_KEY.search(str(k)):
                continue
            yield from _walk_json(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_json(v, path + (i,))
    elif isinstance(node, str):
        yield path, node


def _get_path(doc, path):
    for p in path:
        doc = doc[p]
    return doc


def _set_path(doc, path, value):
    for p in path[:-1]:
        doc = doc[p]
    doc[path[-1]] = value


def _pstr(path) -> str:
    return " > ".join(str(p) for p in path)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(_data_root())).replace("\\", "/")
    except ValueError:
        return p.name


def _schedules() -> List[dict]:
    try:
        from core.scheduler import store
        store.ensure_schema()
        return store._rows("SELECT id, name, params_json, notify_json FROM sched_schedules")
    except Exception:                                                      # noqa: BLE001
        return []


# ───────────────────────── scan ─────────────────────────
def scan(kind: str, old: str) -> List[dict]:
    """Every place that holds `old`.  Each item: id, source, where, field, count, preview (nothing is changed)."""
    kind = "phone" if kind == "phone" else "email"
    old = clean_value(kind, old, "Number/e-mail to replace")
    items: List[dict] = []
    for fname, path in _env_files():
        for key, val in _env_values(path).items():
            if _SECRET_KEY.search(key):
                continue
            _t, n = transform(val, kind, old, "")
            if n:
                where, field = _label(key)
                items.append({"id": f"env|{fname}|{key}", "source": "env", "file": fname, "key": key,
                              "where": where, "field": field, "count": n, "preview": val[:160]})
    for p in _json_files():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                                  # noqa: BLE001
            continue
        for path, s in _walk_json(doc):
            _t, n = transform(s, kind, old, "")
            if n:
                items.append({"id": f"json|{_rel(p)}|{_pstr(path)}", "source": "json", "file": _rel(p),
                              "key": _pstr(path), "where": f"Card settings: {_rel(p)}", "field": _pstr(path),
                              "count": n, "preview": s[:160]})
    for r in _schedules():
        for col, label in (("params_json", "parameters"), ("notify_json", "notify settings")):
            try:
                doc = json.loads(r.get(col) or "null")
            except Exception:                                              # noqa: BLE001
                continue
            for path, s in _walk_json(doc):
                _t, n = transform(s, kind, old, "")
                if n:
                    items.append({"id": f"sched|{r['id']}|{col}|{_pstr(path)}", "source": "sched", "file": r.get("name") or r["id"],
                                  "key": _pstr(path), "where": f"Schedule: {r.get('name') or r['id']}",
                                  "field": f"{label} > {_pstr(path)}", "count": n, "preview": s[:160]})
    return items


# ───────────────────────── replace ─────────────────────────
def _changes_file() -> Path:
    p = _data_root() / "scheduler"
    p.mkdir(parents=True, exist_ok=True)
    return p / "contact_changes.jsonl"


def _read_log() -> List[dict]:
    f = _changes_file()
    out = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:                                              # noqa: BLE001
                pass
    return out


def _write_log(rows: List[dict]) -> None:
    f = _changes_file()
    tmp = Path(str(f) + ".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""), encoding="utf-8")
    os.replace(tmp, f)


def _now() -> dt.datetime:
    return dt.datetime.now()


def _atomic_json(path: Path, doc) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _apply(item_id: str, kind: str, old: str, new: str, new_name: str, undo: Optional[dict] = None) -> Optional[dict]:
    """Change ONE place.  Returns {id, before, after, count, ...} or None when nothing matched any more.
    With `undo` ({before, after}) it restores `before` if the place still holds `after`."""
    parts = item_id.split("|", 3)
    src = parts[0]
    if src == "env":
        from core.scheduler import envstore
        _, fname, key = parts[0], parts[1], parts[2]
        path = dict(_env_files())[fname]
        cur = _env_values(path).get(key)
        if cur is None:
            return None
        if undo is not None:
            if cur != undo["after"]:
                return None
            envstore.write(path, "contact change", {key: undo["before"]})
            return {"id": item_id}
        nv, n = transform(cur, kind, old, new, new_name)
        if not n:
            return None
        envstore.write(path, "contact change", {key: nv})
        return {"id": item_id, "source": "env", "file": fname, "key": key, "before": cur, "after": nv, "count": n}
    if src == "json":
        rel, ppath = parts[1], parts[2]
        path = _data_root() / rel
        doc = json.loads(path.read_text(encoding="utf-8"))
        key_path = _find_path(doc, ppath)
        if key_path is None:
            return None
        cur = _get_path(doc, key_path)
        if undo is not None:
            if cur != undo["after"]:
                return None
            _set_path(doc, key_path, undo["before"])
            _atomic_json(path, doc)
            return {"id": item_id}
        nv, n = transform(cur, kind, old, new, new_name)
        if not n:
            return None
        _set_path(doc, key_path, nv)
        _atomic_json(path, doc)
        return {"id": item_id, "source": "json", "file": rel, "key": ppath, "before": cur, "after": nv, "count": n}
    if src == "sched":
        from core.scheduler import store
        sid, rest = parts[1], parts[2]
        col, _, ppath = rest.partition("|") if "|" in rest else (rest, "", parts[3] if len(parts) > 3 else "")
        if col not in ("params_json", "notify_json"):
            return None
        ppath = parts[3] if len(parts) > 3 else ppath
        rows = store._rows(f"SELECT {col} AS v FROM sched_schedules WHERE id=?", (sid,))
        if not rows:
            return None
        doc = json.loads(rows[0]["v"] or "null")
        key_path = _find_path(doc, ppath)
        if key_path is None:
            return None
        cur = _get_path(doc, key_path)
        if undo is not None:
            if cur != undo["after"]:
                return None
            _set_path(doc, key_path, undo["before"])
            store._write(f"UPDATE sched_schedules SET {col}=? WHERE id=?", (json.dumps(doc), sid))
            return {"id": item_id}
        nv, n = transform(cur, kind, old, new, new_name)
        if not n:
            return None
        _set_path(doc, key_path, nv)
        store._write(f"UPDATE sched_schedules SET {col}=? WHERE id=?", (json.dumps(doc), sid))
        return {"id": item_id, "source": "sched", "file": sid, "key": f"{col} > {ppath}", "before": cur, "after": nv, "count": n}
    return None


def _find_path(doc, ppath: str):
    """Path tuple whose ' > ' form equals `ppath` (the id stores the readable form)."""
    for path, _s in _walk_json(doc):
        if _pstr(path) == ppath:
            return path
    return None


def replace(kind: str, old: str, new: str, new_name: str, ids: List[str], user: str) -> dict:
    kind = "phone" if kind == "phone" else "email"
    old = clean_value(kind, old, "Number/e-mail to replace")
    new = clean_value(kind, new, "New number/e-mail") if str(new or "").strip() else ""
    new_name = re.sub(r"\s+", " ", str(new_name or "").replace("#", "").replace(",", " ")).strip()
    if new and _same(kind, new, old):
        raise ValueError("The new value is the same as the old one.")
    if not ids:
        raise ValueError("Tick at least one place to change.")
    done, skipped = [], []
    with _LOCK:
        for iid in ids:
            try:
                r = _apply(iid, kind, old, new, new_name)
            except Exception as e:                                         # noqa: BLE001
                skipped.append({"id": iid, "why": f"{type(e).__name__}: {e}"})
                continue
            (done if r else skipped).append(r or {"id": iid, "why": "no longer contains the old value"})
        rec = {"change_id": _now().strftime("%Y%m%d%H%M%S%f"), "ts": _now().isoformat(timespec="seconds"),
               "user": user or "unknown", "kind": kind, "old": old, "new": new, "new_name": new_name,
               "action": "replace" if new else "remove", "items": done, "skipped": skipped, "undone": False}
        if done:
            rows = _read_log()
            rows.append(rec)
            _write_log(rows)
            try:
                from core.scheduler import store
                store.audit(user, "contact.replace", {"id": None, "name": "Change email/number"},
                            before={"kind": kind, "old": old, "places": [d["id"] for d in done]},
                            after={"new": new or "(removed)", "name": new_name, "places": len(done)})
            except Exception:                                              # noqa: BLE001
                pass
    return {"change_id": rec["change_id"], "changed": len(done), "entries": sum(d["count"] for d in done),
            "skipped": skipped, "removed": not new}


def history(limit: int = 30) -> List[dict]:
    now, out = _now(), []
    for r in reversed(_read_log()[-limit:]):
        try:
            age = now - dt.datetime.fromisoformat(r["ts"])
        except Exception:                                                  # noqa: BLE001
            age = dt.timedelta(days=99)
        out.append({"change_id": r["change_id"], "ts": r["ts"], "user": r["user"], "kind": r["kind"], "old": r["old"],
                    "new": r["new"], "action": r["action"], "places": len(r["items"]), "undone": r.get("undone", False),
                    "can_undo": (not r.get("undone")) and age <= dt.timedelta(hours=UNDO_HOURS) and bool(r["items"]),
                    "detail": [{"where": i["file"], "key": i["key"], "before": i["before"], "after": i["after"]}
                               for i in r["items"]]})
    return out


def undo(change_id: str, user: str) -> dict:
    with _LOCK:
        rows = _read_log()
        rec = next((r for r in rows if r["change_id"] == change_id), None)
        if not rec:
            raise ValueError("That change was not found.")
        if rec.get("undone"):
            raise ValueError("That change has already been undone.")
        if _now() - dt.datetime.fromisoformat(rec["ts"]) > dt.timedelta(hours=UNDO_HOURS):
            raise ValueError(f"Undo is only possible within {UNDO_HOURS} hours of the change.")
        restored, skipped = 0, []
        for it in rec["items"]:
            try:
                ok = _apply(it["id"], rec["kind"], rec["old"], rec["new"], "", undo={"before": it["before"], "after": it["after"]})
            except Exception as e:                                         # noqa: BLE001
                ok, why = None, f"{type(e).__name__}: {e}"
            else:
                why = "changed again after this change - left as it is"
            if ok:
                restored += 1
            else:
                skipped.append({"id": it["id"], "why": why})
        rec["undone"] = True
        rec["undone_by"], rec["undone_ts"] = user or "unknown", _now().isoformat(timespec="seconds")
        _write_log(rows)
        try:
            from core.scheduler import store
            store.audit(user, "contact.undo", {"id": None, "name": "Change email/number"},
                        before={"change_id": change_id}, after={"restored": restored, "skipped": len(skipped)})
        except Exception:                                                  # noqa: BLE001
            pass
    return {"restored": restored, "skipped": skipped}
