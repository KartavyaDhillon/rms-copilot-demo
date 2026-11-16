"""Loaded automatically by Python at start-up whenever the repo folder is on PYTHONPATH (the console sets that for the
scripts it launches). Installs core/mail_identity.py's smtplib wrapper so every mail-sending script gets the sender
switch + the "moving to noreply" note without being edited. Does nothing (and never fails) if anything is missing."""
try:
    import importlib.util as _u
    import os as _os
    _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "core", "mail_identity.py")
    if _os.path.isfile(_p):
        _s = _u.spec_from_file_location("_rms_mail_identity", _p)
        _m = _u.module_from_spec(_s)
        _s.loader.exec_module(_m)
        _m.install()
except Exception:
    pass
