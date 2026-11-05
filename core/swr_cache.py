"""Stale-while-revalidate cache for polled status routes.

Every open screen polls many status routes every few seconds. Several of those routes look at files on disk (readiness
checks, run folders) and can take 5-45 s when the disk is busy. A plain request then holds a server thread AND one of
the browser's 6 connections for that long; a few of those and the browser cannot even send /health -> "server
unreachable", although the server is fine.

cached(seconds): the first call ever (per argument set) computes the answer; after that every caller gets the stored
answer at once. When it is older than `seconds`, ONE background thread refreshes it while callers keep getting the
previous answer. No poll ever waits for the disk again. A refresh that fails keeps the previous answer."""
from __future__ import annotations

import functools
import threading
import time
from typing import Any, Dict

_PRINT_SLOW_S = 8.0


def cached(seconds: float):
    def deco(fn):
        store: Dict[Any, dict] = {}
        guard = threading.Lock()

        def key_of(a, k):
            try:
                key = (a, tuple(sorted(k.items())))
                hash(key)
                return key
            except TypeError:
                return None                                   # unhashable args (Request, dict): no caching

        def slot(key):
            with guard:
                s = store.get(key)
                if s is None:
                    s = store[key] = {"v": None, "t": 0.0, "has": False, "busy": False, "first": threading.Lock()}
                    if len(store) > 200:                     # many dates/cards: drop the oldest entries
                        for old in sorted(store, key=lambda x: store[x]["t"])[:50]:
                            if old != key:
                                store.pop(old, None)
                return s

        def refresh(s, a, k):
            t0 = time.monotonic()
            try:
                v = fn(*a, **k)
                s["v"], s["t"], s["has"] = v, time.monotonic(), True
            except Exception as e:                            # noqa: BLE001
                s["t"] = time.monotonic()                     # do not hammer a failing route; keep the old answer
                print(f"[cache] {fn.__name__}: refresh failed ({type(e).__name__}: {e}) - kept the previous answer")
            finally:
                s["busy"] = False
                d = time.monotonic() - t0
                if d > _PRINT_SLOW_S:
                    print(f"[cache] {fn.__name__}: background refresh took {d:.1f}s (nobody waited for it)")

        @functools.wraps(fn)
        def wrapper(*a, **k):
            key = key_of(a, k)
            if key is None:
                return fn(*a, **k)
            s = slot(key)
            if not s["has"]:
                with s["first"]:                              # first call: one computes, the others wait for it
                    if not s["has"]:
                        v = fn(*a, **k)                       # errors go to the caller (e.g. HTTP 400 bad date)
                        s["v"], s["t"], s["has"] = v, time.monotonic(), True
                return s["v"]
            if time.monotonic() - s["t"] >= seconds and not s["busy"]:
                s["busy"] = True
                threading.Thread(target=refresh, args=(s, a, k), daemon=True,
                                 name=f"swr-{fn.__name__}").start()
            return s["v"]

        wrapper.cache_clear = lambda: store.clear()         # tests / after an action that must show at once
        return wrapper
    return deco
