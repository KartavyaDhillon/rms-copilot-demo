"""Start the WHOLE login/download again when the connection breaks — used by the Scheduler and the older pipelines.

A broken connection ("An existing connection was forcibly closed by the remote host", reset, aborted, timed out,
dropped half-way through a download) says nothing about the credentials or the file, so the right reaction is to start
that step again from the very beginning (fresh session → login → download), not to give up and not to carry on without
the file.

NOT retried (a retry cannot help and can lock the account):  wrong password / refused login / locked user /
anything that is not a network failure.
"""
from __future__ import annotations

import socket
import ssl
import time
from typing import Callable

RESTART_WAIT_SEC = 5        # wait before starting again
MAX_RESTARTS = 5            # total attempts

_TEXT = ("forcibly closed", "connection aborted", "connection reset", "remotedisconnected", "remote end closed",
         "10054", "10053", "10060", "10061", "timed out", "temporary failure", "eof occurred", "broken pipe",
         "incompleteread", "connection broken", "max retries exceeded", "name or service not known")


def is_network_error(e: BaseException) -> bool:
    """True for connection-level failures only."""
    if isinstance(e, (ConnectionError, TimeoutError, socket.timeout, socket.gaierror, ssl.SSLError, EOFError)):
        return True
    try:
        import requests
        if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                          requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError)):
            return True
    except Exception:
        pass
    if isinstance(e, RuntimeError):          # our own messages ("refused the password", "Cannot reach …") — already decided
        return False
    if type(e).__name__ in ("IncompleteRead", "ProtocolError", "ReadTimeoutError", "RemoteDisconnected",
                            "SSHException", "NoValidConnectionsError"):
        return True
    if isinstance(e, OSError):
        txt = f"{e}".lower()
        return any(t in txt for t in _TEXT)
    return False


def run(fn: Callable, what: str, log: Callable[[str], None] = print, tries: int = MAX_RESTARTS,
        wait: int = RESTART_WAIT_SEC):
    """Call fn() — if it fails with a network error start it again from the beginning after `wait` seconds."""
    for n in range(1, max(1, tries) + 1):
        try:
            return fn()
        except Exception as e:
            if not is_network_error(e) or n >= tries:
                raise
            short = str(e).replace("\n", " ")[:140]
            log(f"{what}: connection problem — {type(e).__name__}: {short}")
            log(f"{what}: starting again from the beginning in {wait}s (restart {n}/{tries - 1}).")
            time.sleep(wait)
