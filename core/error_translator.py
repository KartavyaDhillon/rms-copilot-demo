"""
core/error_translator.py — free AI fallback for errors we haven't specifically coded for.

Every downloader script prints a known tag when it hits a problem we've already seen
and handled: [NCFE_LOCKED], [NMASS_AUTH_FAIL], [MCX90_AUTH_FAIL], [MCX_DEPOSIT_AUTH_FAIL].
Each SSE generator (nmass_download.py, mcx90_download.py, mcx_deposit_download.py) scans
for those tags and shows an instant, hardcoded plain-English message — no network call,
no delay, no dependency.

But a script can also fail in a way we've NEVER seen before (a new server error message,
a network drop, a changed website, an unrelated crash) — none of those print one of our
known tags, so previously the dashboard fell back to showing a raw exit code, which means
nothing to a non-technical user.

This module is that fallback's fallback: when nothing known matched, we send the last
lines the script printed to Groq's API (free tier) and ask it to explain, in one plain
sentence, what most likely went wrong and what to do next.

This is NEVER on the critical path — it only runs after a known-tag check has already
failed to match, and it fails silently (returns None) if:
  - GROQ_API_KEY isn't set in .env
  - the network call errors out
  - it takes longer than _TIMEOUT seconds
In every one of those cases the caller must show its own fixed generic message instead.
Nobody is ever blocked or left with a spinner because of this.

Setup: get a free key at https://console.groq.com/keys and add it to .env as
GROQ_API_KEY=gsk_...
If that key is left blank, this whole module quietly does nothing.
"""
import os
import requests

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")  # Groq's fast free-tier model
_TIMEOUT = 8  # seconds — must never hold up the dashboard; if Groq is slow, we just skip it


def translate_error(context: str, log_lines):
    """Ask Groq to explain a failure in one plain-English sentence.

    context:    short label shown to the model, e.g. "NCDEX/NMASS download",
                "MCX 90% download", "MCX Deposit download"
    log_lines:  list[str] — the last lines the failing script printed (any length;
                only the last 20 are actually sent)

    Returns a ready-to-show string (already includes a 🤖 marker so users can tell
    this came from the AI fallback, not a hardcoded message), or None if translation
    isn't available right now — caller MUST have its own fallback message for that case.
    """
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None

    tail = "\n".join(log_lines[-20:]) if log_lines else "(no output was captured)"
    prompt = (
        f"A financial-markets download script called '{context}' just failed. "
        f"Here are the last lines it printed:\n\n{tail}\n\n"
        f"In ONE short sentence, written for a non-technical trading-desk operator "
        f"(no jargon, no error codes, no exit codes), explain what most likely went "
        f"wrong and what they should do next (for example: retry later, check/update "
        f"credentials, contact the exchange, or contact tech support). "
        f"If the log doesn't clearly show a specific cause, don't guess — just say the "
        f"download failed for an unrecognized reason and that they should check the "
        f"Logs button or contact tech support with the time of the failure."
    )

    try:
        resp = requests.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": _GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 120,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        if not text:
            return None
        return f"🤖 {text}"
    except Exception:
        return None
