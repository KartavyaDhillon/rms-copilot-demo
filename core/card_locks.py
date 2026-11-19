"""core/card_locks.py - who is running which KPI card RIGHT NOW (one very cheap snapshot for every card).

Every card module already refuses a second run on the server (its run lock -> "already running", HTTP 409). This snapshot lets the
screens show that lock at once, on EVERY user's screen: the Run buttons of a running card are greyed ("Running - by Abhishek") and a
click on one is stopped before it is sent. The older Process Center boxes (CTCL, Margin Shortage, CLI ...) have their own layer
(core/lock_manager.py, platform/js/process-locks.js) and are not repeated here.

snapshot() -> {"cards": {"<window title>": {"key", "running": True, "by", "since", "label"}}} - only cards that are running appear.
"""
from __future__ import annotations

import importlib

# window title of the card (exactly what its window shows)  ->  (key, module)
CARDS = {
    "CNC F11": ("cncf11", "core.cnc_f11_card"),
    "ENTERPRISE RISK REPORT": ("entrisk", "core.enterprise_risk"),
    "MTF CONCENTRATION": ("mtfconc", "core.mtf_concentration"),
    "MTF ORDER FILE": ("mtfof", "core.mtf_order_file"),
    "MCX COMM LIMIT": ("mcxcomm", "core.mcx_comm_limit"),
    "NCDEX COMM LIMIT": ("ncdexcomm", "core.ncdex_comm_limit"),
    "PAYOUT CHECK": ("payoutchk", "core.payout_check_card"),
    "OBLIGATION REPORT": ("obligation", "core.obligation_card"),
    "TENTATIVE PAYIN PAYOUT": ("tpp", "core.tpp_card"),
    "LIVE ALLOCATION CAPTURE": ("liveallocation", "core.live_allocation"),
    "MCX CASH MAINTENANCE": ("mcxcash", "core.mcx_cash_maintenance"),
    "76 FILES DOWNLOADER": ("files70", "core.files70"),
}


def _who(mod) -> dict:
    """by / since of the run that is going - best effort, never raises."""
    out = {"by": "", "since": ""}
    try:
        st = mod.status()
    except Exception:                                            # noqa: BLE001
        return out
    if not isinstance(st, dict):
        return out
    out["by"] = str(st.get("by") or "")
    out["since"] = str(st.get("started") or "")
    if not out["by"]:                                            # slot based cards: the slot that is running
        for s in (st.get("slots") or st.get("runs") or []):
            if isinstance(s, dict) and s.get("state") == "running":
                out["by"] = str(s.get("by") or "")
                out["since"] = out["since"] or str(s.get("started") or "")
                break
    return out


def snapshot() -> dict:
    cards = {}
    for title, (key, modname) in CARDS.items():
        try:
            mod = importlib.import_module(modname)
            if not mod.is_running():
                continue
        except Exception:                                        # noqa: BLE001
            continue
        w = _who(mod)
        cards[title] = {"key": key, "running": True, "by": w["by"], "since": w["since"], "label": title}
    return {"cards": cards}
