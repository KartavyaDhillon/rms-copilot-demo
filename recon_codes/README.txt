RECONCILIATION — script folder
==============================

Put the desk's own scripts here, UNCHANGED, with exactly these file names
(then `git add` them like any other code):

    HOLDING_final.py
    T1_HOLDING_final.py
    FILES CHECKING POSITIONBOOK_final.py
    KAMBALA MCX CHECKING KAMBALA_final.py
    search_results.py

The console never edits them. For every run it writes a patched COPY into
<RECO_FOLDER>\_runtime (folders, e-mail To/CC, sender login, holiday-list location and,
for search_results.py, the desktop pop-ups replaced by the dashboard's RSA-token pop-up),
and runs that copy.

PYTHON: the scripts need pandas 2.x (the console itself may have 3.x). Double-click
setup_reco_python.bat ONCE — it builds recon_codes\.venv, which the console uses automatically
(or point RECO_PYTHON in .env at any other python.exe).

A different location can be set with RECO_CODES_DIR in .env.

.env keys for this box (all optional — defaults shown)
------------------------------------------------------
RECO_FOLDER=D:\CONSOLE\data\RECONCILIATION      # default: <repo>\data\RECONCILIATION
RECO_HOLDING_TO / RECO_HOLDING_CC               # default kartavya.dhillon@mastertrust.co.in
RECO_T1_TO      / RECO_T1_CC
RECO_NFO_TO     / RECO_NFO_CC
RECO_KAMB_TO    / RECO_KAMB_CC                  # (edit them from the console: ✉️ Email To / CC)
SENDER_EMAIL / SENDER_PASSWORD                  # blank = login written inside the scripts
RECO_NSE_MEMBER / RECO_NSE_LOGIN_ID / RECO_NSE_PASSWORD   # blank = taken from HOLDING_final.py
RECO_MCX_USER / RECO_MCX_PASSWORD               # blank = the one inside search_results.py
FTP_HOST / FTP_USER / FTP_PASS                  # same FTP the CLI box uses (required)
SA_COMMON_DIR                                   # COMMON FILES (HOLDING_ / RISK_REPORT_ live here)
