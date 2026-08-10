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

### 2. Download the code from GitHub

Do **not** copy the folder across. Use Git — it brings exactly the right files
and nothing else (no `.venv`, no databases, no zips), and every future update is
then a single `git pull`.

Make the console folder and clone into it:

```powershell
mkdir D:\CONSOLE
cd /d D:\CONSOLE
git clone https://github.com/KartavyaDhillon/rms-copilot-demo.git rms-copilot
```

You'll be asked to sign in to GitHub the first time. The repo is private, so
this is expected.

Final layout on the new machine:

```
D:\CONSOLE\
├── rms-copilot\     the code — git pull lands here
└── data\            every working folder, created automatically
```

> Why not copy the folder? A Windows virtualenv hardcodes absolute paths into
> `pyvenv.cfg`, so a copied `.venv` cannot work on another machine — and a
> copied folder also drags across `knowledge.db` (200+ MB of rebuildable index)
> and every stale temp file. Cloning sidesteps all of it.

### 2b. Copy `.env` by hand — Git does not bring this one

`.env` holds live passwords and API tokens, so it is deliberately on the
gitignore list and will **never** arrive via `git clone` or `git pull`. Copy
it manually, once, from the old machine:

```
D:\CLAUDE\rms-copilot\.env  →  D:\CONSOLE\rms-copilot\.env
```

Drag it over RDC or carry it on the same USB drive as the database dump.
`tools\make_env_local.py` in Step 4b reads this file, so it must be in place
before that step runs. After this one-time copy, `.env` on the new machine is
edited directly there if it ever needs a change — it does not sync from the
old machine again.

### 3. Build the virtualenv

From `D:\CONSOLE\rms-copilot`:

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

### 4b. Tell this machine where its folders are — **do not skip this**

`.env` is a copy of the *old* machine's file, so every folder setting in it
still points at the old machine's layout (`MARGIN_SHORTAGE_FOLDER=D:\15 minutes`
and ~25 more like it). Nothing warns you about this. The console starts, the
downloaders run, and the files land in the old paths — recreated at the drive
root on this machine, where nobody is looking for them.

`.env.local` is what fixes that: it is loaded **before** `.env` with
`override=True`, so it wins. It is gitignored, so `git pull` will never bring
it — it has to be generated here, once.

From `D:\CONSOLE\rms-copilot`, preview first:

```powershell
python tools\make_env_local.py --root D:\CONSOLE\data --dry-run
```

Read the output. Every path should now start `D:\CONSOLE\data\`. If a folder in
that list is a **shared drop point** — somewhere another person or tool writes
files into — it must NOT be moved, or you break their workflow rather than just
relocating your own data. Re-run naming each one:

```powershell
python tools\make_env_local.py --root D:\CONSOLE\data ^
    --keep-external SA_COMMON_DIR
```

Then confirm it took:

```powershell
python tools\doctor.py
```

The `RMS_DATA_ROOT` line under Configuration should read `D:\CONSOLE\data`.

> **If you already started the console before doing this**, some files will
> have been written into the old paths (`D:\15 minutes`, `D:\PROBABLE`, …).
> `tools\move_data_folders.py` moves them across — it works out old → new from
> the two env files, never overwrites, and is a dry run unless you pass
> `--apply`:
>
> ```powershell
> python tools\move_data_folders.py
> python tools\move_data_folders.py --apply
> ```
>
> Do this before the next run. A pipeline that finds yesterday's file in one
> place and today's in another will not tell you — it will just process
> whichever it finds.

> Re-run this after any future change that adds a process, so the new keys get
> rebased too. `--reuse` keeps your `--keep-external` answers:
> `python tools\make_env_local.py --root D:\CONSOLE\data --reuse`

### 5. Copy the four reference files

These are permanent master data. No code creates them and nothing downloads
them — they are the only files that must be carried across by hand. This
includes `Book1.xlsx`: `.gitignore` excludes every `.xlsx` file from the repo
on purpose (see the comment near the bottom of `.gitignore`), so it does
**not** travel with `git clone` in Step 2 — it must be copied by hand like the
other three.

| File | Goes in |
|---|---|
| `Book1.xlsx` | repo root (`D:\CONSOLE\rms-copilot\`) |
| `KAMBALA_ALLOCATION_LIST.xlsx` | `<RMS_DATA_ROOT>\CTCL VS NMASS\` |
| `MAIL LIST.xlsx` | `<RMS_DATA_ROOT>\PROBABLE\MASTER\` |
| `Branch_AND_Region_LIST.xlsx` | `<RMS_DATA_ROOT>\PROBABLE\MASTER\` |

`<RMS_DATA_ROOT>` is whatever you passed to `--root` when you ran
`make_env_local.py` — e.g. `D:\CONSOLE\data`. Run `tools\doctor.py` and read
the `RMS_DATA_ROOT` line under Configuration if you're not sure.

Everything else arrives on the day: NMASS/NCDEX, TCS BANCS and the delivery
margin file download themselves; you upload `Probable_Fullfile` by hand as usual.

### 6. The start script needs no editing

`START_SERVER.bat` used to hardcode `D:\CLAUDE\rms-copilot`. It now does
`cd /d "%~dp0"` — it goes to whatever folder it is sitting in. The same file
works on the dev machine and on the server with no per-machine edit, and a
`git pull` can never re-break the path.

### 7. First manual start — before automating anything

```
D:\CONSOLE\rms-copilot\START_SERVER.bat
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
- **Action:** `D:\CONSOLE\rms-copilot\START_SERVER.bat`
- **Run whether user is logged on or not:** ticked
- **Start in:** `D:\CONSOLE\rms-copilot`
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
