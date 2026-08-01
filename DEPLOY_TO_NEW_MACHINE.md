# Moving RMS Copilot to the always-on machine (07:00 – 01:00)

Target: new Windows machine with a **D: drive**, running 07:00 to 01:00 daily.
Postgres **moves** to the new machine — it stays a single database, just hosted
where the uptime actually is.

**You do not create a single data folder by hand.** Every pipeline folder
(`D:\PROBABLE`, `D:\15 minutes`, `D:\CTCL VS NMASS`, the BACKUPFILES trees, the
REGION/BRANCH subfolders) is created automatically on first use — there are 50
`makedirs(exist_ok=True)` calls across 27 pipeline files. Because the new box
has a D: drive, **`.env` needs no path changes at all.**

What genuinely has to be carried over is listed in Step 5. It's three files.

---

## Before you start — on the OLD machine

### 0. Fix requirements.txt encoding (do this first, it's saved in the repo)

The file is currently UTF-16. pip will reject it. In PowerShell, on the old
machine, from the repo root:

```powershell
Get-Content requirements.txt | Set-Content -Encoding utf8 requirements-utf8.txt
Move-Item -Force requirements-utf8.txt requirements.txt
```

Confirm it worked — this should print readable text, not spaced-out characters:

```powershell
Get-Content requirements.txt -TotalCount 5
```

### 0b. Stop the server

Nothing should be running while you dump the database.

---

## On the NEW machine

### 1. Install the prerequisites

| What | Version / notes |
|---|---|
| **Python** | 3.13.7 — match the old machine. Tick **"Add python.exe to PATH"** in the installer. |
| **PostgreSQL** | Same major version as the old machine, or newer. Remember the `postgres` password you set. |
| **Microsoft Excel** | Required. `win32com` opens files by path to password-protect the PROBABLE zone files. Without it they still send, but **unprotected and silently** — easy to miss. |
| **Google Chrome** | Required for the NMASS/NCDEX and TCS BANCS downloads. |

Check Python landed correctly:

```powershell
python --version
```

Must print `Python 3.13.7`.

### 2. Copy the repo

Copy `D:\CLAUDE\rms-copilot` to `D:\CLAUDE\rms-copilot` on the new machine.

**Delete `.venv\` from the copy.** A Windows virtualenv hardcodes absolute
paths into `pyvenv.cfg` and its activate scripts — it will not work on another
machine and will fail in confusing ways if you leave it. It gets rebuilt in the
next step.

Also delete `rms_history.db` if present — the SQLite fallback file. Postgres is
the real store; a stale copy of this only causes confusion.

### 3. Build the virtualenv

From `D:\CLAUDE\rms-copilot`:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**This takes a while.** `torch`, `torchvision`, `transformers`,
`sentence-transformers` and `easyocr` are several GB between them — they power
the captcha OCR. Leave it running; a slow connection can push this past an hour.

If pip complains about the file encoding, Step 0 didn't take. Go back and redo it.

### 4. Move the database

**On the OLD machine**, from anywhere:

```powershell
& "C:\Program Files\PostgreSQL\<version>\bin\pg_dump.exe" -U postgres -d postgres -F c -f D:\rms_backup.dump
```

It will prompt for the password (`Waheguru@1`, per the current `.env`).

Copy `rms_backup.dump` to the new machine, then **on the NEW machine**:

```powershell
& "C:\Program Files\PostgreSQL\<version>\bin\pg_restore.exe" -U postgres -d postgres --clean --if-exists D:\rms_backup.dump
```

Then in the new machine's `.env`, set `PG_PASSWORD` to whatever you chose when
installing Postgres there. `PG_HOST=localhost` stays as it is — Postgres is now
local to this machine.

> If you ever want to run the console from your own workstation again, point its
> `.env` at this machine instead: `PG_HOST=<new machine's LAN IP>`, and open port
> 5432 in the new machine's firewall. Still one database.

### 5. Copy the three reference files

These are permanent master data. No code creates them and nothing downloads
them — they are the only files that must be carried across by hand.

| File | Goes in |
|---|---|
| `KAMBALA_ALLOCATION_LIST.xlsx` | `D:\CTCL VS NMASS\` |
| `MAIL LIST.xlsx` | `D:\PROBABLE\MASTER\` |
| `Branch_AND_Region_LIST.xlsx` | `D:\PROBABLE\MASTER\` |

`Book1.xlsx` (the user roster) lives in the repo root, so it already came with
Step 2 — nothing to do.

Everything else arrives on the day: NMASS/NCDEX, TCS BANCS and the delivery
margin file download themselves; you upload `Probable_Fullfile` by hand as usual.

### 6. Fix the start script

`START_SERVER.bat` line 3 hardcodes the repo path. It's already correct if you
copied to `D:\CLAUDE\rms-copilot` — verify it reads:

```bat
cd /d D:\CLAUDE\rms-copilot
```

### 7. First manual start — before automating anything

```
D:\CLAUDE\rms-copilot\START_SERVER.bat
```

Open `http://localhost:8000`. Work through this list before going further:

- [ ] Login page appears and your account signs in
- [ ] All five process boxes render
- [ ] Each box's readiness banner shows the files it wants (missing is fine and expected — this proves the check works)
- [ ] **History** page shows your old runs — this confirms the Postgres restore
- [ ] **Ask** answers a question about a past run
- [ ] Press **📥 NMASS/NCDEX** on any box — confirms Chrome and the downloader work
- [ ] Arm a **🧪 Safe test run** on PROBABLE and run it — confirms Excel/COM, SMTP and the Tata WhatsApp path all work end to end, with nothing reaching a client

The safe test is the single most valuable check here. It exercises every
external dependency in one go and cannot reach a real recipient.

### 8. Make it start on its own

The `.bat` ends in `pause` — it needs a human. For an unattended box, use Task
Scheduler:

- **Trigger:** At startup, plus daily at 07:00
- **Action:** `D:\CLAUDE\rms-copilot\START_SERVER.bat`
- **Run whether user is logged on or not:** ticked
- **Start in:** `D:\CLAUDE\rms-copilot`
- **Settings:** untick *"Stop the task if it runs longer than…"*

> Windows Update rebooting overnight is the most common cause of a console that's
> quietly been down since 3am. The "At startup" trigger is what saves you.

### 9. Shut the old console down

Once the new machine is confirmed working, **stop the server on this machine and
leave it stopped.**

Two consoles pointed at the same folders and the same database will double-send
every email and every client WhatsApp. Closing the browser tab is not enough —
the `START_SERVER` window must be closed, and any Task Scheduler entry here
disabled.

---

## Schedule check

Your window is 07:00 – 01:00, which clears everything:

| Box | First run |
|---|---|
| PROBABLE | 08:00 |
| CTCL vs NMASS | 08:40 |
| Margin Shortage | 09:30 |

Nothing is missed. If the machine ever starts late, run windows are **not** hard
deadlines — the Run button stays enabled and a missed slot can still be run.

---

## Things that will bite you later

**`RMS_SECRET_KEY`** is set in `.env` and comes across with the copy — good.
Leave it. If it were blank, every restart would sign everyone out.

**Credentials are all in `.env`**, which means the copy carries live passwords
and API tokens to the new machine. Make sure the new box is as access-controlled
as this one.

**`USER_SHEET_STORE_PASSWORDS=true`** means `Book1.xlsx` holds real passwords in
plain text. Anyone who can open that file can sign in as anyone in it. It's now
on a machine other people may be able to reach — worth reconsidering.

**Postgres is now the only copy of your run history.** Set up a scheduled
`pg_dump` on the new machine. The whole point of moving it was durability;
a single un-backed-up instance gives that away.
