"""
core/env_boot.py — two-stage configuration load. Import this FIRST, before any
other project module.

WHY THIS EXISTS
───────────────
There are two kinds of setting in this system and they were living in one file:

  SHARED   — the same on every machine. Email recipients, run windows, feature
             flags, WhatsApp template names, the folder LAYOUT. When you add a
             new process, its new keys are shared config.
  MACHINE  — different per machine. Where the data root is, the Postgres
             password, and every credential/token.

Keeping both in one .env is what made deploying painful: .env is gitignored (it
holds secrets), so `git pull` on the server brought new CODE but never the new
CONFIG it needed, and someone had to hand-merge keys on a live machine.

THE SPLIT
─────────
  .env.local   machine + secret. NEVER committed, written once per machine,
               barely ever touched again.
  .env         shared. Safe to carry between machines. References the machine's
               data root as ${RMS_DATA_ROOT} rather than hardcoding drive paths.

LOAD ORDER MATTERS — .env.local goes FIRST
──────────────────────────────────────────
Two reasons, and they pull in the same direction:

  1. INTERPOLATION. .env contains lines like
         PROBABLE_FOLDER=${RMS_DATA_ROOT}\\PROBABLE
     python-dotenv resolves ${RMS_DATA_ROOT} against values already in the
     environment. If .env were read first, the root would not exist yet and
     every folder path would resolve to a broken partial path.

  2. PRECEDENCE. .env.local is loaded with override=True (it wins over anything
     already set), and .env is then loaded with override=False (it fills in the
     gaps but never clobbers). So a machine-specific value always beats the
     shared default, which is exactly the intent.

The practical result: adding a process means adding its keys to .env only. Pull
on the server and the keys are simply there. .env.local is never reopened.

USAGE
─────
    import core.env_boot   # noqa: F401  — must precede other project imports

Importing it is enough; the load runs at import time and is idempotent, so it
is safe for several entry points to import it.
"""
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_LOCAL = os.path.join(_REPO, ".env.local")
_ENV_SHARED = os.path.join(_REPO, ".env")

_loaded = False


def boot(verbose=False):
    """Run the two-stage load. Idempotent — repeat calls are no-ops."""
    global _loaded
    if _loaded:
        return
    try:
        from dotenv import load_dotenv
    except Exception:
        # python-dotenv missing: fall back to whatever is already in the real
        # environment rather than crashing at import. doctor.py reports this.
        _loaded = True
        return

    # 1 ── machine + secrets. Wins over everything.
    if os.path.exists(_ENV_LOCAL):
        load_dotenv(_ENV_LOCAL, override=True)
    elif verbose:
        print(f"[env_boot] no .env.local at {_ENV_LOCAL} — using .env defaults only")

    # 2 ── shared config. Fills gaps, never overwrites step 1.
    if os.path.exists(_ENV_SHARED):
        load_dotenv(_ENV_SHARED, override=False)

    _loaded = True


def reload():
    """Re-read BOTH files, in the correct precedence order, into the running
    process. Use this — never a bare load_dotenv(override=True) — whenever code
    needs to pick up values edited on disk since the server started (email
    recipients changed from the dashboard, a test-mode flag, new credentials).

    WHY A PLAIN RE-READ OF .env IS A BUG NOW
    ────────────────────────────────────────
    Before .env.local existed, `load_dotenv(".env", override=True)` was the
    right way to refresh: there was only one file, so re-reading it could only
    make things more current. Now .env is the LOWER-priority file, so doing that
    silently INVERTS the precedence this module exists to establish — every
    machine-specific path in .env.local gets overwritten by .env's shared
    defaults, which on any machine other than the one .env was written on are
    the WRONG paths. The process keeps running happily and writes a day's output
    to the old machine's folders.

    That is not hypothetical: it is exactly how NMASS downloads kept landing in
    D:\\15 minutes on a machine whose .env.local correctly said
    D:\\CONSOLE\\data\\15 minutes. Startup was right; the first /sa/status poll
    called cfg() → a bare override=True re-read of .env → and every path in the
    process reverted, minutes before anyone pressed Download.

    Both stages use override=True here, so freshly-saved values genuinely do
    win over what is already in memory — but .env.local goes SECOND, so the
    machine's own layout still beats the shared defaults. Same end state as
    boot(), just refreshed.
    """
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    if os.path.exists(_ENV_SHARED):
        load_dotenv(_ENV_SHARED, override=True)     # fresh shared values
    if os.path.exists(_ENV_LOCAL):
        load_dotenv(_ENV_LOCAL, override=True)      # machine layout wins, always


def data_root():
    """The single folder every pipeline's working directory lives under.

    Falls back to the repo's own `data` folder rather than a hardcoded drive so
    a fresh clone on an unfamiliar machine starts somewhere sane and visible
    instead of silently creating folders at a drive root.
    """
    return os.getenv("RMS_DATA_ROOT", os.path.join(_REPO, "data"))


def repo_root():
    return _REPO


boot()
