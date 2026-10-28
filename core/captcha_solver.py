"""Automatic captcha solving (2Captcha API v2) for the NMASS and NCDEX logins.

What this module does
  * Sends a captcha picture to 2Captcha (``ImageToTextTask``) and returns the text.
  * Gives every picture at most ``TIMEOUT_S`` (45) seconds. If 2Captcha does not answer in time the SAME picture is
    sent again as a new try.
  * Allows at most ``MAX_TRIES`` (5) tries per login. The budget is only consumed by tries that did not lead to an
    accepted captcha: when the portal accepts a captcha the budget resets (NMASS asks for a second captcha after an
    "existing session" logout, and that one gets its own five tries).
  * When the tries are used up, the key is missing/invalid or the switch is off, ``AutoSession.solve`` returns
    ``None`` and the caller shows the normal manual popup in the dashboard.

Configuration (never hard-code secrets - they live in ``.env``)
  APIKEY_2CAPTCHA        2Captcha API key (required for auto-fill)
  CAPTCHA_AUTO_DEFAULT   "1"/"0" - state of the dashboard switch until somebody changes it (default "1")
  CAPTCHA_AUTO_FILE      optional path of the switch file (default: ``.captcha_auto.json`` in the repo root)

The dashboard switch ("Captcha auto-fill") is stored in that small JSON file so that every running pipeline - each
one is its own process - sees a change immediately, without a restart.
"""
from __future__ import annotations

import base64
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:                                    # requests is already a dependency of every pipeline that needs a captcha
    import requests
except ImportError:                     # pragma: no cover - only when run without the project's packages
    requests = None                     # type: ignore[assignment]

API_BASE = "https://api.2captcha.com"
MAX_TRIES = 5                           # tries per login before the manual popup takes over
TIMEOUT_S = 45                          # seconds to wait for 2Captcha per try
POLL_S = 3                              # how often the answer is polled
INITIAL_WAIT_S = 4                      # 2Captcha needs a few seconds before the first poll makes sense
REPO_ROOT = Path(__file__).resolve().parents[1]
SWITCH_FILE = Path(os.getenv("CAPTCHA_AUTO_FILE") or REPO_ROOT / ".captcha_auto.json")
STATS_FILE = REPO_ROOT / "captcha_auto_log.csv"
# Errors where trying again cannot help: stop using the service for this login and fall back to manual entry.
FATAL_CODES = ("ERROR_KEY_DOES_NOT_EXIST", "ERROR_WRONG_USER_KEY", "ERROR_ZERO_BALANCE", "ERROR_IP_NOT_ALLOWED",
               "ERROR_NO_SLOT_AVAILABLE_FOR_THIS_KEY")

LOGGER = logging.getLogger("captcha_auto")


def _setup_logging() -> None:
    """Print log lines to stdout (the console shows the pipelines' stdout); done once."""
    if LOGGER.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("  [CAPTCHA-AUTO] %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


_setup_logging()


@dataclass(frozen=True)
class Profile:
    """How a portal's captcha looks, so the workers and our own sanity check know what to expect."""

    name: str
    min_len: int
    max_len: int
    case_sensitive: bool
    comment: str
    upper: bool = False                 # NCDEX captchas are upper case: normalise the answer


PROFILES = {
    # NMASS: 6 characters, mixed upper/lower case, tough shapes (z/2, x/X, o/O ...). Case is kept exactly as drawn.
    "nmass": Profile("nmass", 6, 6, True,
                     "6 characters, letters and digits. Upper and lower case matter - type each letter exactly "
                     "as drawn. Look carefully at similar shapes: 0/O, 1/l/I, 2/z/Z, x/X, s/S, v/V, c/C, w/W."),
    # NCDEX: plain upper-case letters and digits (e.g. 637FCF).
    "ncdex": Profile("ncdex", 5, 6, False, "5 or 6 characters: capital letters and digits.", upper=True),
}


class SolverError(Exception):
    """A 2Captcha problem; ``code`` is the API error code (or NETWORK / TIMEOUT / BAD_ANSWER)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# ── The dashboard switch ────────────────────────────────────────────────────────────────────────────────────────

def api_key() -> str:
    """The 2Captcha key from the environment, or from the repo ``.env`` when the process did not load it."""
    key = os.getenv("APIKEY_2CAPTCHA", "").strip()
    if key:
        return key
    try:
        from dotenv import dotenv_values
        return str((dotenv_values(REPO_ROOT / ".env").get("APIKEY_2CAPTCHA") or "")).strip()
    except Exception:                   # a missing dotenv package or unreadable file simply means "no key"
        return ""


def switch_state(path: Path | None = None) -> dict:
    """Return {"enabled": bool, "by": str, "at": str}; the file wins, otherwise CAPTCHA_AUTO_DEFAULT decides."""
    path = path or SWITCH_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"enabled": bool(data.get("enabled")), "by": str(data.get("by", "")), "at": str(data.get("at", ""))}
    except (OSError, ValueError):
        default = os.getenv("CAPTCHA_AUTO_DEFAULT", "1").strip().lower() not in ("0", "false", "no", "off")
        return {"enabled": default, "by": "", "at": ""}


def set_enabled(enabled: bool, who: str = "", path: Path | None = None) -> dict:
    """Persist the dashboard switch; returns the new state."""
    path = path or SWITCH_FILE
    state = {"enabled": bool(enabled), "by": who, "at": datetime.now().isoformat(timespec="seconds")}
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


def is_enabled(path: Path | None = None) -> bool:
    """True when the switch is on AND a key is configured."""
    return switch_state(path)["enabled"] and bool(api_key())


# ── 2Captcha v2 client ──────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Solved:
    """One answer from 2Captcha."""

    text: str
    task_id: int
    seconds: float


class V2Client:
    """Minimal 2Captcha API v2 client (JSON): ImageToTextTask, balance and feedback only."""

    def __init__(self, key: str, http=None, sleep=time.sleep, clock=time.monotonic) -> None:
        self.key = key
        self.http = http if http is not None else requests
        self.sleep = sleep
        self.clock = clock

    def scrub(self, text: str) -> str:
        """Remove the API key from text before it is logged."""
        return text.replace(self.key, "***") if self.key else text

    def _post(self, method: str, payload: dict) -> dict:
        """POST one API method; network problems and ``errorId != 0`` become SolverError (the key never leaks)."""
        try:
            response = self.http.post(f"{API_BASE}/{method}", json=dict(payload, clientKey=self.key), timeout=20)
            data = response.json()
        except Exception as exc:        # requests errors, JSON errors, anything from a fake in tests
            raise SolverError("NETWORK", self.scrub(f"{type(exc).__name__}: {exc}")) from None
        if data.get("errorId"):
            raise SolverError(str(data.get("errorCode", "ERROR")), str(data.get("errorDescription", "")))
        return data

    def solve(self, image: bytes, profile: Profile, timeout_s: float = TIMEOUT_S) -> Solved:
        """Submit the picture and wait up to ``timeout_s`` for the text (SolverError('TIMEOUT') otherwise)."""
        started = self.clock()
        task = {"type": "ImageToTextTask", "body": base64.b64encode(image).decode("ascii"),
                "case": profile.case_sensitive, "minLength": profile.min_len, "maxLength": profile.max_len,
                "comment": profile.comment}
        task_id = int(self._post("createTask", {"task": task, "languagePool": "en"})["taskId"])
        self.sleep(min(INITIAL_WAIT_S, timeout_s))
        while True:
            data = self._post("getTaskResult", {"taskId": task_id})
            if data.get("status") == "ready":
                text = str(data.get("solution", {}).get("text", "")).replace(" ", "").strip()
                return Solved(text, task_id, self.clock() - started)
            if self.clock() - started + POLL_S > timeout_s:
                raise SolverError("TIMEOUT", f"no answer within {timeout_s:.0f} s")
            self.sleep(POLL_S)

    def balance(self) -> float:
        """The account balance in USD (read only)."""
        return float(self._post("getBalance", {})["balance"])

    def report(self, task_id: int, good: bool) -> None:
        """Feedback to 2Captcha (wrong answers can be refunded after their review)."""
        self._post("reportCorrect" if good else "reportIncorrect", {"taskId": task_id})


# ── One login's worth of automatic tries ────────────────────────────────────────────────────────────────────────

def _log_stat(portal: str, outcome: str, seconds: float, tries: int) -> None:
    """Append one line to captcha_auto_log.csv so accuracy can be measured later; never raises."""
    try:
        new = not STATS_FILE.exists()
        with open(STATS_FILE, "a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if new:
                writer.writerow(["time", "portal", "outcome", "seconds", "try_no"])
            writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), portal, outcome, f"{seconds:.1f}", tries])
    except OSError:
        pass


class AutoSession:
    """Automatic answering for one portal login; create one per script run (or per login) and reuse it.

    Typical use inside a login loop::

        answer = auto.solve(image_bytes)        # None -> show the manual popup
        ...submit the answer...
        auto.accepted() if the portal took the captcha else auto.rejected()
    """

    def __init__(self, portal: str, client: V2Client | None = None, max_tries: int = MAX_TRIES,
                 timeout_s: float = TIMEOUT_S, enabled_fn=is_enabled) -> None:
        if portal not in PROFILES:
            raise ValueError(f"unknown portal {portal!r}")
        self.portal = portal
        self.profile = PROFILES[portal]
        self.client = client
        self.max_tries = max_tries
        self.timeout_s = timeout_s
        self.enabled_fn = enabled_fn
        self.tries_used = 0             # tries since the last accepted captcha
        self.total_tries = 0            # tries over the whole run (callers use it to size their own attempt caps)
        self.gave_up = False            # True once the manual popup has taken over for this login
        self._broken = False            # a fatal 2Captcha error (bad key / no balance): stay manual
        self._task_id: int | None = None
        self._last_seconds = 0.0

    def _client(self) -> V2Client | None:
        """Create the client on first use; None when no key is configured."""
        if self.client is None:
            key = api_key()
            if not key:
                return None
            self.client = V2Client(key)
        return self.client

    def active(self) -> bool:
        """True while the next captcha should be answered automatically."""
        return (not self._broken) and self.tries_used < self.max_tries and self.enabled_fn()

    def manual_note(self) -> str:
        """A short sentence for the manual popup explaining why a person is asked."""
        if self._broken:
            return "Auto-fill is unavailable (2Captcha key/balance problem) - please type the captcha"
        if self.tries_used >= self.max_tries:
            return f"Auto-fill failed {self.max_tries} times - please type the captcha"
        return ""

    def solve(self, image: bytes) -> str | None:
        """Return the answer for ``image`` or None when the caller must ask a person.

        Each call to 2Captcha is one try. A 45 s timeout (or a worker answer of the wrong shape) counts as a try and
        the same picture is sent again, until the budget is used up.
        """
        while self.active():
            client = self._client()
            if client is None:
                self._broken = True
                LOGGER.warning("No APIKEY_2CAPTCHA in .env - using the manual popup.")
                return None
            self.tries_used += 1
            self.total_tries += 1
            LOGGER.info("%s try %d of %d ...", self.portal.upper(), self.tries_used, self.max_tries)
            try:
                result = client.solve(image, self.profile, self.timeout_s)
            except SolverError as exc:
                LOGGER.warning("try %d failed: %s", self.tries_used, exc)
                _log_stat(self.portal, "error_" + exc.code, 0.0, self.tries_used)
                if exc.code in FATAL_CODES:
                    self._broken = True
                    return None
                continue
            text = result.text.upper() if self.profile.upper else result.text
            if not self.profile.min_len <= len(text) <= self.profile.max_len:
                LOGGER.warning("try %d: answer %r has the wrong length - sending the picture again",
                               self.tries_used, text)
                _log_stat(self.portal, "bad_length", result.seconds, self.tries_used)
                continue
            self._task_id, self._last_seconds = result.task_id, result.seconds
            LOGGER.info("answer %s received in %.1f s - filling it in", text, result.seconds)
            return text
        self.gave_up = True
        return None

    def accepted(self) -> bool:
        """The portal accepted the last answer: tell 2Captcha and give the next captcha a fresh budget.

        Returns True when that answer came from 2Captcha (False when a person typed it).
        """
        was_auto = self._task_id is not None
        if was_auto:
            self._report(True)
            _log_stat(self.portal, "accepted", self._last_seconds, self.tries_used)
        self.tries_used = 0
        self.gave_up = False
        self._task_id = None
        return was_auto

    def rejected(self) -> bool:
        """The portal refused the last answer (wrong captcha); an automatic try stays used.

        Returns True when the refused answer came from 2Captcha - callers that restart the whole login use this to
        decide whether another automatic try is worth it (a wrong answer typed by a person is never retried silently).
        """
        was_auto = self._task_id is not None
        if was_auto:
            self._report(False)
            _log_stat(self.portal, "rejected", self._last_seconds, self.tries_used)
        self._task_id = None
        return was_auto

    def _report(self, good: bool) -> None:
        """Best-effort feedback to 2Captcha; a failure here must never disturb the login."""
        try:
            if self.client is not None and self._task_id is not None:
                self.client.report(self._task_id, good)
        except SolverError as exc:
            LOGGER.warning("could not send feedback to 2Captcha: %s", exc)


def stats_today() -> dict:
    """Counts of today's automatic outcomes from the log file: {"accepted": n, "rejected": n, "errors": n}."""
    counts = {"accepted": 0, "rejected": 0, "errors": 0}
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(STATS_FILE, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if not str(row.get("time", "")).startswith(today):
                    continue
                outcome = row.get("outcome", "")
                if outcome in ("accepted", "rejected"):
                    counts[outcome] += 1
                else:
                    counts["errors"] += 1
    except OSError:
        pass
    return counts
