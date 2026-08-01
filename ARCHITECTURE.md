# RMS Copilot — Full Architecture Reference

**Purpose of this file:** Plain-English description of every major code file, what it does, what it reads/writes, and exactly where to go when you need to make a change. Keep this file updated whenever you add or significantly change something.

---

## How the system works (big picture)

The system is a **FastAPI web server** (`api.py`) that serves a single-page dashboard (`dashboard.html`) to the browser. The dashboard sends HTTP requests to the API when the user clicks a button. The API runs the relevant Python pipeline (as a subprocess or function) and streams back progress line-by-line using **Server-Sent Events (SSE)**. Everything important — credentials, folder paths, email addresses — lives in one file: **`.env`**.

```
Browser (dashboard.html)
   │  HTTP / SSE
   ▼
api.py  (FastAPI, port 8000)
   │  calls
   ▼
core/pipelines/*.py  →  subprocess  →  sa_codes/*.py   (the real worker scripts)
                     →  direct call →  core/pipeline_box.py  (orchestrator)
```

---

## Root-level files

### `api.py`
**What it does:** The central web server. Every button on the dashboard ultimately calls a URL defined here. It:
- Serves `dashboard.html` at `GET /` so the whole team only needs one URL
- Receives pipeline trigger requests and streams back progress via SSE
- Handles file uploads (`/sa/upload`, `/ctcl/upload`)
- Exposes the `/ask` and `/agent` endpoints for the AI assistant
- Manages a `PENDING` dict so pipelines that need human approval can be staged first

**Key routes (what each button maps to):**
| Dashboard button | API route |
|---|---|
| Peak Margin Run | `GET /peak-margin-stream` |
| CTCL — NMASS Download | `GET /ctcl/download/nmass-stream` |
| CTCL — MCX 90% Download | `GET /ctcl/download/mcx90-stream` |
| CTCL vs NMASS Run | `GET /px/run-stream?pipe=ctcl` |
| CTCL Stop | `POST /ctcl/stop` |
| CTCL Readiness check | `GET /ctcl/readiness` |
| CTCL Captcha (poll) | `GET /ctcl/captcha/pending` |
| CTCL Captcha (submit) | `POST /ctcl/captcha/answer` |
| MCX 90% RSA Token (poll) | `GET /ctcl/token/pending` |
| MCX 90% RSA Token (submit) | `POST /ctcl/token/answer` |
| Concentration Check | `GET /concentration-download-stream` |
| Margin Shortage Download | `GET /margin-shortage-stream` |
| Short Allocation Run | `GET /sa/run-stream` |
| SA File Download | `GET /sa/las-email-download-stream` |
| SA Stop | `POST /sa/stop` |
| 70 Files Downloader | `GET /files-downloader-70-stream` |
| MCX Deposit Download (SA) | `GET /sa/mcx-deposit-stream` |
| MCX Deposit Stop | `POST /sa/mcx-deposit-stop` |
| MCX Deposit RSA Token (poll) | `GET /sa/token/pending` |
| MCX Deposit RSA Token (submit) | `POST /sa/token/answer` |
| Payout Inspection | `GET /payout-inspection-daily-stream` |
| Client Lookup (CTCL) | `GET /px/client-lookup` |
| CLI Prudential Limit — Run OLD | `GET /cli/run-old-stream` |
| CLI Prudential Limit — Run NEW | `GET /cli/run-new-stream` |
| CLI — Download (CM/FO/CO/NCDEX) | `GET /cli/download-stream` |
| CLI — MCX Web Allocation download | `GET /cli/mcx-download-stream` |
| CLI — Upload files | `POST /px/upload?pipe=cli` |
| Generic pipeline status (any `px` pipe) | `GET /px/status?pipe=<ctcl\|cli>` |
| Generic pipeline logs | `GET /px/logs?pipe=<ctcl\|cli>` |

**To change:** Add a new button → add a new `@app.get(...)` route here, wire it to a stream function in `core/pipelines/`.

---

### `dashboard.html`
**What it does:** The entire front-end in a single HTML file. Contains all CSS, JavaScript, and HTML. No build step, no npm — just one file the browser loads.

**Key sections inside the file:**
- **CSS variables** (`:root { ... }`) — all colours and spacing live here; change one variable to restyle everywhere
- **Tab switcher** — the top nav tabs (Home / Short Allocation / CTCL / etc.). Each tab is a `<div id="tab-...">` section
- **Short Allocation tab** — run buttons (SA-01/02/03/04), file upload boxes, logs modal, MCX Deposit button
- **CTCL tab** — NMASS download, peak margin, CTCL run button, client lookup section
- **Client Lookup popup** — `pxShowLookupPopup()` function, builds two tables from JSON returned by `/px/client-lookup`
- **SSE helper** — `startStream(url, ...)` generic function that opens an EventSource, shows progress lines in a terminal-style box, and calls a done/error callback

**To change a column name or label in the client lookup popup:** Find `pxShowLookupPopup` (around line 3980). Table 1 headers use `subTh(...)` calls. Table 2 headers use `_THL`/`_THR` calls.

**To change a button label or add a button:** Find the relevant tab's `<div>` section and edit the HTML. Wire the `onclick` to either `startStream(...)` or a custom JS function.

---

### `.env`
**What it does:** All credentials, folder paths, and feature flags. **Never hard-code a path or password in any Python file** — put it here instead. Every Python file reads this via `load_dotenv()` or `_SAENV()`.

**Key variables:**
| Variable | Used for |
|---|---|
| `NSE_LOGIN_ID`, `NSE_PASSWORD` | NSE API login for file downloads |
| `NSE_MEMBER_CODE` | Broker code (e.g. `06436`) |
| `SENDER_EMAIL`, `SENDER_PASSWORD` | Email account for sending reports |
| `SMTP_SERVER`, `SMTP_PORT` | Email server settings |
| `SA_COMMON_DIR` | Shared folder for RISK_REPORT and collateral files |
| `CTCL_NMASS_FOLDER` | Where CTCL vs NMASS files are stored and backed up |
| `SA_OUTPUT_DIR` | Short Allocation output folder |
| `SA_LAS_FUNDING_DIR` | LAS Funding output folder |
| `MCX_USERNAME`, `MCX_PASSWORDS` | MCX SFTP login |
| `NCDEX_USERNAME`, `NCDEX_PASSWORD` | NCDEX portal login |
| `AUTO_SEND` | `true` = auto-email after pipeline; `false` = wait for manual approval |
| `SA_OVERRIDE_DATE` | Set to `YYYY-MM-DD` to replay pipelines for a past date |
| `PEAK_FO_CODES`, `PEAK_CM_CODES` | Client codes tracked for peak margin |
| `CTCL_MAIN_TO`, `CTCL_MAIN_CC` etc. | Email recipients per zone |

---

### `START_SERVER.bat`
**What it does:** Double-click this to start the server. Runs `uvicorn api:app --host 0.0.0.0 --port 8000` (auto-reload is intentionally OFF). After starting, open `http://localhost:8000` in a browser. **`--reload` was removed on purpose:** with it on, saving ANY `.py` file restarted uvicorn, which killed whatever pipeline was mid-run (a live download, a captcha/token wait, a long processing job). To apply code changes now, stop the server (Ctrl+C) and start it again — so reloads only ever happen when you decide, never in the middle of a live run.

### `SAVE.bat` / `ROLLBACK.bat`
**What they do:** Quick git snapshot (`SAVE`) and rollback (`ROLLBACK`) of the codebase. Run these before/after risky changes.

---

## `core/` — The main Python package

### `core/config.py`
**What it does:** Central configuration — loads `.env` and exposes today's date in two formats (`TODAY_DDMMYYYY`, `TODAY_YYYYMMDD`), plus all API keys, SMTP settings, FTP settings. Everything else imports from here instead of reading the environment directly.

**To change a global setting:** Change it here. All other files pick it up automatically.

---

### `core/short_allocation.py`
**What it does:** The **orchestrator** for the Short Allocation (SA-01 to SA-04) pipeline. It:
1. Reads the current run state from `run_state.txt`
2. Runs **Code-1** (file downloader) as a subprocess
3. If Code-1 succeeds, runs **Code-2** (SA calculation) as a subprocess
4. For runs 1-3, Code-2 then runs Code-3 (LASUPTO3); for run 4, Code-3 is LAS4
5. Streams all output live to the dashboard
6. Validates uploaded files (name format + date embedded in filename)

**Key functions:**
- `stream_sa_run(pipe)` — the main SSE generator; yields log lines as the run progresses
- `save_uploaded_bytes(pipe, filename, data)` — saves a file upload after validating name and date
- `move_uploaded(pipe, src)` — moves an already-placed file after validation
- `_name_ok(pipe, name)` — checks filename matches expected pattern for the pipe
- `_date_ok(name)` — checks the date embedded in the filename matches today
  - NSE LIS files: date format `DDMMYYYY`
  - MCX PeakMargin files: `YYYYMMDD`
  - NCDEX files: `YYYYMMDD`

**NSE password encryption:** Uses AES-256-ECB with a fixed key. The `_nse_encrypt()` and `_nse_decrypt()` functions convert between the plain password and the encrypted token stored in `.env`.

**To add a new file type to upload validation:** Edit `_name_ok()` and possibly `_date_ok()`.

---

### `core/pipeline_box.py`
**What it does:** Two things:
1. **PIPELINES registry** — a dictionary describing every pipeline (folder paths, log directories, file types, email recipients per zone). This is the single source of truth for what files each pipeline expects.
2. **`pb_client_lookup()`** — the backend for the CTCL client lookup feature. Given a client code and date range, it scans the `BACKUPFILES AUTOMATED` folder, reads CM/FO/CO collateral CSVs, MCX margin xlsx, NCDEX deposits CSV, and FULL_FILE output xlsx, and returns input deposit breakdown + output (CTCL vs NMASS result) per run.

**Key functions:**
- `pb_client_lookup(pipe, client_codes, from_date, to_date, runs)` — main lookup function
- `_pb_read_csv_polars(path, codes)` — fast CSV reading using Polars (Rust-based, ~10x faster than pandas)
- `_pb_read_xlsx_pandas(path)` — Excel reading; tries Polars/fastexcel first, falls back to openpyxl
- `_pb_col(df, *names)` — finds a column by any of its known name variants (case-insensitive)
- `pb_copy_backups(src_dir, dest_dir)` — copies backup files from one location to another
- `pb_stream_run(pipe, run_fn)` — generic SSE runner for any pipeline

**PIPELINES dict structure (add a new pipeline here):**
```python
PIPELINES["my_pipe"] = {
    "name": "My Pipeline Display Name",
    "module": "core.pipelines.my_module",
    "func": "run_my_func",
    "logdir": "my_logs",
    "runs": 5,                  # how many intraday runs
    "state_dir_env": "MY_FOLDER",
    "file_types": [...],        # what files users can upload
    "email_keys": {...},        # zone → [TO_key, CC_key]
}
```

**To change how client lookup reads a deposit column:** Find the relevant section in `pb_client_lookup()` — look for `# CM/FO/CO`, `# MCX`, or `# NCDEX` comments.

**Generic pipeline runner (what actually powers CTCL and CLI's Run buttons):**
- `_PB = {"proc": None, "cancel": False, "running": False, "pipe": None}` — the single source of truth for "is ANY generic px pipeline subprocess currently running, and which pipe does it belong to." Both `stream_pipeline()` and `_stream_code()` set `_PB["running"]=True` and `_PB["pipe"]=<key>` when they start a subprocess, and clear it when done.
- `pb_status(pipe)` — returns `{"windows", "runs", "run_state", "running", "now"}` for a given pipe; the dashboard polls `GET /px/status?pipe=ctcl` and `?pipe=cli` to know whether a run is genuinely active — **this is the only reliable signal**, because the dashboard's own `_pxRunning` JS variable is client-side-only (resets on page reload, doesn't see runs from another tab/device). Two real bugs were caused by code trusting `_pxRunning` instead of this: a stale "Files needed" banner during active runs, and a 30-second poll that skipped calling `pxRenderRuns()` (and therefore never disabled the Run buttons) for the whole duration of a run.
- `_stream_code(code, logdir_key)` — runs an arbitrary Python code string as a subprocess (`python -u -c <code>`) and streams stdout line-by-line. Always sets `PYTHONUNBUFFERED=1` **and** passes `-u` — both are required, because a subprocess writing to a pipe (not a real terminal) block-buffers stdout by default, so a slow/quiet script can look completely hung with a near-empty log for minutes even though it's working. `core/short_allocation.py`'s subprocess spawn was missing this for a long time and it caused exactly that symptom (SA runs showing a 73-byte log and "Downloading…" for 3-4 minutes) — **any new subprocess spawn added anywhere in this codebase must set both of these**, or it'll silently reproduce the same bug.
- `_pb_archive_stale_common_files(common_dir, globs, pipe)` — whenever a file is uploaded to Common Files, automatically moves any OTHER file matching the given glob patterns (e.g. old `RISK_REPORT_*.csv`) into `BACKUP FILES AUTOMATED/<DD-MM-YYYY>/` and archives it to the DB. Runs from `pb_upload()` whenever `target=="common"`. This replaced a fully manual daily process.

**Captcha / RSA-token handshake pattern (used by NMASS, MCX90, MCX Deposit, NCDEX):** entirely file-based, independent of the SSE log stream. A script writes `.captcha_state`/`.captcha_<source>.png`/`.token_state` (PENDING) to a known folder; the dashboard polls `GET /ctcl/captcha/pending`, `/sa/captcha/pending`, `/ctcl/token/pending`, `/sa/token/pending` every 2s via `captchaStartPoller()`. Whoever kicks off a subprocess that might trigger one of these underlying scripts **must call `captchaStartPoller()` itself** — it is not automatic, and forgetting it is why CLI's Download button once looked hung (the login it triggers under the hood can need a captcha, but nothing was polling for it).

---

### `core/db.py`
**What it does:** SQLite database (`rms_history.db`) that records every pipeline run. Stores these tables:
- `pipeline_runs` — one row per run (pipeline name, date, run number, timestamp)
- `ctcl_snapshots` — per-client CTCL data for trend queries
- `margin_snapshots` — per-client 15-min margin data
- `margin_alert_tracker` — per-client-per-day WhatsApp alert dedup state for Margin Shortage (15-min) (`get_margin_alert()`/`set_margin_alert()`) — replaces the old local `margin_alert_tracker.json` file so the dedup state is shared no matter which PC runs the pipeline
- `margin_whatsapp_log` — one row per WhatsApp alert ACTUALLY sent, tagged with its run number (`log_whatsapp_send()`/`list_whatsapp_sends()`) — powers the dashboard's "WhatsApp check" report (pick a date + run(s), download who was messaged)
- `run_files` — generic file-bytes storage for any pipeline (logs, outputs, backups), so any machine can pull a file another machine produced

Run `python core/db.py summary` in terminal to see a quick summary of recorded runs.

**To change what gets recorded:** Edit the `record_run()` function and the `_CREATE_SQL` schema.

---

### `core/db_postgres.py`
**What it does:** Optional Postgres version of the database — same structure as `db.py` but connects to a real Postgres server. Used when you need multi-machine access to the data.

---

### `core/agent.py` / `core/agent_run.py`
**What they do:** The AI assistant that answers RMS-related questions. `agent.py` contains the core reasoning logic using the Anthropic Claude API. `agent_run.py` wraps it in an SSE stream so the dashboard can show the answer word-by-word as it's generated.

**To change what the agent knows:** Edit the system prompt inside `agent.py`, or add documents to `data/circulars/`.

---

### `core/compliance.py`
**What it does:** Rule-based compliance Q&A. The `/ask` endpoint calls this. It searches `data/circulars/` text files for relevant paragraphs and returns them as the answer. Simpler and faster than the AI agent — no API call needed.

---

### `core/llm.py`
**What it does:** Thin wrapper around the LLM API (Claude / Groq). Centralises the API call so all other files just call `llm.complete(prompt)` without worrying about which model or key to use.

---

### `core/rag.py`
**What it does:** Retrieval-Augmented Generation — builds a searchable index over the `data/circulars/` documents. When the AI agent needs to answer a question, RAG finds the most relevant paragraphs first so the LLM only gets relevant context.

---

### `core/notify.py`
**What it does:** Sends emails (via SMTP/Netcore) and WhatsApp messages (via Gupshup). All pipelines call functions here to send their reports. Credentials come from `.env`.

**To change an email recipient:** Update the relevant `CTCL_MAIN_TO` (or similar) variable in `.env`. Don't touch this file.

---

### `core/penalty.py`
**What it does:** Calculates peak margin penalty according to SEBI rules. Reads the peak margin report and computes what penalty would apply if margin was collected short at any snapshot.

---

### CTCL vs NMASS — where the code runs (complete picture)

**All CTCL code now runs from `D:\CLAUDE\rms-copilot\` — nothing from `D:\MY CODES WORKING`.**

| What | File | Called by |
|---|---|---|
| NMASS file download | `ctcl_codes/NMASS_NCFE_API_MIX.PY` | `core/pipelines/nmass_download.py` (as subprocess) |
| MCX 90% file download | `ctcl_codes/MCX_90_PERCENT_FILE_API.py` | `core/pipelines/mcx90_download.py` (as subprocess) |
| CTCL vs NMASS computation | `core/pipelines/ctcl_vs_nmass.py` | `core/pipelines/ctcl_vs_nmass_run.py` → api.py |
| MarginTop20 email | `core/pipelines/margin_top20.py` | called inside `ctcl_vs_nmass.py` |
| Payout check | `core/pipelines/payout_check.py` | called inside `ctcl_vs_nmass.py` |

**CTCL run sequence (what happens when you click Run):**
1. Dashboard calls `GET /ctcl/download/nmass-stream` → runs `ctcl_codes/NMASS_NCFE_API_MIX.PY` as subprocess
   - NMASS captcha appears → dashboard polls `/ctcl/captcha/pending` → user submits via modal
2. Dashboard calls `GET /px/run-stream?pipe=ctcl` → runs `core/pipelines/ctcl_vs_nmass.py`
   - Pre-run check: looks for CM/FO/CO/ClientNet CSV files in `CTCL_NMASS_FOLDER` (`.env`)
   - Sends zone emails, MarginTop20 email, payout report, then backs up files
3. MCX 90% is a separate button (`GET /ctcl/download/mcx90-stream`) → runs `ctcl_codes/MCX_90_PERCENT_FILE_API.py`
   - RSA SecurID token required → dashboard polls `/ctcl/token/pending` → user enters token in modal

**State files (written by scripts, read by api.py):**
- `CTCL_NMASS_FOLDER/.captcha_state` + `.captcha_answer` — NMASS captcha handshake
- `CTCL_NMASS_FOLDER/.token_state` + `.token_answer` — MCX 90% RSA token handshake

---

### Margin Shortage (15-min) — where the code runs (complete picture)

Runs 9x/day (`MS15_RUN1_WINDOW` .. `MS15_RUN9_WINDOW` in `.env`: 09:30, 09:50, 11:30, 12:00, 13:00, 13:20, 14:30, 14:55, 15:15). Own full dashboard box, PIPELINES key `"ms15"`. Ported from the uploaded `15minutesfinal_whatsapp.PY` with zero calculation/threshold/email/WhatsApp logic changes — only I/O boundaries (folder paths, credentials, the alert tracker) moved to `.env`/DB.

| What | File | Called by |
|---|---|---|
| NMASS file download (own folder) | `ctcl_codes/NMASS_NCFE_API_MIX.PY` | `core/pipelines/nmass_download.py` with `pipe="ms15"` |
| MCX 90% file download (own folder, auto-shared with CTCL) | `ctcl_codes/MCX_90_PERCENT_FILE_API.py` | `core/pipelines/mcx90_download.py` with `pipe="ms15"` |
| Margin Shortage computation + WhatsApp alerts | `core/pipelines/margin_shortage_15min.py` | `core/pipeline_box.py` (`PIPELINES["ms15"]`) → `GET /px/run-stream?pipe=ms15` |

**Run sequence (what happens when you click each button):**
1. `GET /ms15/download/nmass-stream` → runs `NMASS_NCFE_API_MIX.PY` with `NMASS_SAVE_DIR=MARGIN_SHORTAGE_FOLDER` — always downloads its own copy (NSCCL/NCDEX are fast, ~40-50s, never shared with CTCL's box).
2. `GET /ms15/download/mcx90-stream` → checks `MARGIN_SHORTAGE_FOLDER` for today's MCX 90% file first; if CTCL's box already shared it (see below), skips the download entirely. Otherwise runs `MCX_90_PERCENT_FILE_API.py` with `MCX90_SAVE_DIR=MARGIN_SHORTAGE_FOLDER`.
3. `GET /px/run-stream?pipe=ms15` → runs `margin_shortage_15min.py`: pre-run file check → merge CM/FO/CO/NCDEX/MCX → compute DEPOSIT NMASS / TOTAL MARGIN / DIFFERENCE(F-G) / MARGIN WARNING % → main + zone emails → WhatsApp alerts (Gupshup) → backup + cleanup.

**MCX 90% sharing with CTCL vs NMASS (the cross-copy feature):** a fresh TCS BANCS export+RSA-token round trip takes 4-5 minutes, too slow to do twice at the same moment. `core/pipelines/mcx90_download.py` has a static pairing table (`_SHARE_PAIRS`) matched against each box's real run windows:

| CTCL run | CTCL window | Margin Shortage run | MS window |
|---|---|---|---|
| CTCL-02 | 09:50-10:00 | MS-02 | 09:50-10:05 |
| CTCL-03 | 11:50-12:00 | MS-04 | 12:00-12:15 |
| CTCL-05 | 14:50-15:00 | MS-08 | 14:55-15:10 |

Whichever box downloads MCX 90% first at one of these 3 paired times copies the result into the sibling box's folder (`_maybe_share_with_sibling()`); the sibling's own MCX90 button then finds today's file already present and skips its download. CTCL-01/CTCL-04 have no nearby MS run and are never shared. **Only the MCX 90% file is ever shared — NSCCL/NCDEX files are always downloaded independently by each box.**

**WhatsApp alerts (Gupshup, client-facing — separate from the Tata WA_AUTH_TOKEN used elsewhere):** first alert when a client's `MARGIN WARNING %` > 105; subsequent alerts only on a ±10% swing from the last-sent value, tracked per client per calendar day in the `margin_alert_tracker` DB table (`core/db.py`/`core/db_postgres.py` — `get_margin_alert()`/`set_margin_alert()`), NOT a local JSON file, so the dedup state is shared no matter which PC runs the pipeline. Client mobile numbers are looked up via the Mastertrust webpayout OAuth API (`MT_WEBPAYOUT_USER`/`PASSWORD` in `.env`).

**State files:** `MARGIN_SHORTAGE_FOLDER/.captcha_state`+`.captcha_answer`, `.token_state`+`.token_answer` — same handshake pattern as CTCL's, via `/ms15/captcha/*` and `/ms15/token/*` routes in `api.py`.

**🧪 Test run — REMOVED:** the box used to have a "🧪 Test run" button (`GET /ms15/test-run-stream`, `run_margin_shortage_15min(test_mode=True)`) that ran the full pipeline live except suppressing the client-facing WhatsApp send. Removed per explicit request — the pipeline no longer accepts a `test_mode` argument at all, WhatsApp/image alerts always send for real now (matching the reference script exactly), the `/ms15/test-run-stream` route is gone, and the button itself is removed from the DOM at box-render time via `platform/js/process-probable-box.js`'s `removeMs15TestButton()` (dashboard.html's native markup can't be edited on disk). `margin_shortage_15min.py` still calls `load_dotenv(override=True)` at import so each run picks up the CURRENT `.env` (email edits, etc.) rather than the server's startup snapshot.

**🧪 Safe test run (one-shot, self-discarding):** the *replacement* for the removed Test run, built on the opposite principle. The old one proved nothing because it **skipped** sends; this one **performs every send for real** — same Gupshup calls, same Tata Tele image upload, same SMTP with the same generated `.xlsx` attachments — and only swaps the **destination**. While armed: every per-client WhatsApp goes to `MS15_TEST_WA_TO` instead of the client (so no client is ever messaged), the shortage alert image goes there too instead of the RM/branch list, every email (main + all four zones) goes to `MS15_TEST_EMAIL_TO` with **CC lists dropped entirely** and the subject tagged `[TEST — IGNORE]`, and — critically — **the alert tracker is not written**. That last point is the real safety property: the tracker records "client X was already alerted at Y% today" and the next real run uses it to decide who to skip, so a test writing to it would make genuine margin breaches go unalerted on the following run. Skipping `_set_margin_alert()`/`_log_whatsapp_send()` means the next real run behaves exactly as if the test never happened.

Arming is one-shot **by construction**, not by convention: `POST /ms15/safe-test/arm` (password-gated) writes a marker file `MARGIN_SHORTAGE_FOLDER/.ms15_safe_test`, and `_run_margin_shortage_15min_impl()` **deletes** it the moment the run commits — there is no code path by which the flag survives into a second run and no way to forget to switch it off. It is consumed *after* `pre_run_file_check()` passes, deliberately: if the run aborts on a missing input file nothing was sent, so the arm must still be there for the retry — otherwise the operator's next click would silently be a LIVE run they believed was a test. A safe test whose redirect targets are blank/malformed **aborts rather than guessing** (`status: safe_test_misconfigured`), since a bad target could otherwise fall through to a live destination. Because the arm is a server-side file rather than browser state, it is visible to every screen: `GET /ms15/safe-test` reports armed state + the resolved targets, and `platform/js/process-probable-box.js` polls it to drive both the "🧪 Test ARMED" button label and a persistent amber strip on the box spelling out exactly where the run's messages will land, with a "Cancel test" button (`POST /ms15/safe-test/disarm`, deliberately *not* password-gated — disarming only ever moves toward normal live behaviour, and an accidental arm must always be instantly undoable).

**"Client data" + "WhatsApp check" (backup panel):** the Margin Shortage backup modal reuses CTCL's generic "🔍 Client data" lookup tab (`GET /px/client-lookup?pipe=ms15&...`, `pb_client_lookup()` in `core/pipeline_box.py`) — enter client code(s) + date range + run filter (1-9, ms15 runs 9x/day vs CTCL's 5x/day) and it scans that box's own backups for CM/FO/CO/NCDEX/MCX input deposits PLUS its own `Shortage_vs_CC_Deposit.xlsx` output per run (Zone, Deposit NMASS, Total Margin, Difference(F-G), Margin Warning %, Remarks) — same UI/columns pattern as CTCL vs NMASS's lookup, just reading ms15's own filenames/columns instead of `FULL_FILE_CTCL_VS_NMASS_NN.xlsx`. (An earlier single-file "Output by run" download tab was replaced by this, per user feedback that it should work "ditto same" as the CTCL lookup.) "📱 WhatsApp check" (`GET /ms15/whatsapp-report?date=&runs=`) is the other ms15-specific tab — builds an .xlsx of exactly which clients received a WhatsApp on that date/run(s) — Run, Client Code, Mobile, Margin Warning %, Sent At — read from the `margin_whatsapp_log` table.

**Known fixed bugs (this pass):** (1) the dashboard's captcha poller for ms15 stopped polling entirely once its modal opened (`if(!_captchaVisible&&!_tokenVisible)` guarded the whole block), so it could never detect the answer being accepted or a retry — it hung on "Checking…" until the Python side's own 3-minute timeout silently fell back to OCR text with no UI feedback; fixed to always poll (mirroring the CTCL block) and only gate the *first-show* on `!_captchaVisible`. (2) new Postgres tables added to `core/db_postgres.py`'s `_CREATE_SQL` only ever got created by manually running `python core/db_postgres.py setup` — `db.py`'s SQLite side self-heals every connection (`_init_db()` runs `CREATE TABLE IF NOT EXISTS` on every `_conn()`), but `db_postgres.py` did not, so `margin_whatsapp_log` silently didn't exist until someone remembered the manual step. `api.py` now calls `db_postgres.setup_db()` once at startup when `USE_POSTGRES=true` (wrapped in try/except), auto-healing this whole class of bug on every restart going forward — this run also uncovered a second latent bug in the SAME schema string: a comment mentioning the old path `D:\15 minutes\margin_alert_tracker.json` sat inside a normal (non-raw) Python string, so Python parsed `\15` as an *octal escape* (chr 13, a literal carriage return embedded in the SQL), which cut the `--` comment short and left `minutes\margin_alert_tracker.json...` as bare SQL — hence "syntax error at or near 'minutes'". Fixed by making both `db.py` and `db_postgres.py`'s `_CREATE_SQL` raw strings (`r"""..."""`), closing off this whole class of "Windows path in a SQL comment" bug for good. (3) `pre_run_file_check()`/`find_file()` inside `core/pipelines/margin_shortage_15min.py` only ever looked in `MARGIN_SHORTAGE_FOLDER` for RISK_REPORT, even though `/ms15/readiness` (the dashboard's readiness banner) already correctly also checked `SA_COMMON_DIR` — so the banner could say "ready" while the actual run then failed pre-flight with "Missing required files: RISK_REPORT". Fixed both functions to search `SA_COMMON_DIR` too, mirroring `ctcl_vs_nmass.py`'s `COMMON_DIR` fallback exactly (RISK_REPORT is a shared file dropped once into the common folder, not downloaded per-box).

**Generic "View / 🤖 Explain error" (applies to EVERY pipeline, not just this one):** CTCL vs NMASS had a hand-built, pipe-specific "View" summary modal (`ctclOpenView()`) with its own backend persistence (`/ctcl/run-log/<n>`) and AI fallback — every other pipe (ms15, and any future one) got nothing, so a failed ms15 run showed only a bare "Run finished with errors" pill with no way to see what happened or get it explained. Rather than hand-copy CTCL's modal a third time, built ONE generic version that piggybacks on infrastructure that was *already* pipe-agnostic: `stream_pipeline()` (`core/pipeline_box.py`) already yields `{"type":"done","ok":bool,"log":<filename>,"err":<last exception line>}` for every pipe's run, and `translate_error()` (`core/error_translator.py`, Groq free tier) was already pipe-agnostic too (nmass_download.py/mcx90_download.py already call it). New pieces: `GET /px/explain-error?pipe=&log=` (api.py) — reads that run's saved log via the existing generic `read_log()` and calls `translate_error()`; and on the dashboard, the generic run flow's completion handler (`_pxRunReal()`, factored out of `pxRun()`) captures `{log, hasError, err}` into `_pxRunLog[pipe]` (persisted to `localStorage` so it survives a refresh) and injects a "📋 View · 🤖 Explain error" button into that pipe's status area (`pxInjectViewBtn()`); `pxOpenView(pipe)` shows the outcome + a "↻ Retry"/"Run again" button + the AI-explain trigger. `ms15TestRun()`'s separate `/ms15/test-run-stream` handler feeds the SAME `_pxRunLog`/`pxInjectViewBtn`/`pxOpenView` (tagged `isTest:true` so the summary/retry button correctly says "🧪 Test" and re-runs the test, not a real run) rather than being its own copy. Because this hooks into the SAME generic path every non-CTCL, non-CLI pipe already uses, ms15 gets it automatically and so will any pipeline added in the future — no per-pipe reimplementation needed. (CTCL keeps its own richer multi-stage `ctclOpenView()` since its pipeline genuinely has 4 separable sub-stages with individual retry buttons; the generic version is intentionally simpler, matching what a single-stage pipeline like ms15 actually needs.)

**Fixed: Test run showing a false "WhatsApp submitted successfully":** `core/pipeline_box.py`'s `_status()` line-scanner used a loose regex (`whatsapp.*(sent|success|submitted)`) to detect a real WhatsApp send anywhere in stream output. Margin Shortage's test-mode line ("[TEST] would send WhatsApp to `<client_code>` …") could accidentally satisfy that pattern if the client code itself happened to contain a substring like "SENT" — a real, data-dependent false positive that made a **test** run look like it had sent a **real** WhatsApp message. Fixed two ways: (1) `_status()` now checks for a `[TEST]` tag FIRST and ignores the line entirely before any other pattern gets a chance to match it; (2) `_WA_OK`/`_WA_BAD` were tightened from a loose "whatsapp ... anything ... sent/success" scan to the *exact* phrases `send_whatsapp()` actually prints ("whatsapp submitted for", "whatsapp (api error|error) for") — WhatsApp is only ever sent by this one pipeline, so there was no reason for the pattern to be loose in the first place.

**Fixed: 9-run tile grid wrapping unevenly:** `pxRenderRuns()` used `grid-template-columns: repeat(auto-fit, minmax(165px,1fr))` for every pipe, which crammed 6 tiles into row 1 on wide screens (MS15-01..06) and left an awkward 3 on row 2. ms15 now gets a fixed `repeat(5, 1fr)` (5 tiles/row), so it always wraps as 5 (MS15-01..05) then 4 (MS15-06..09); other pipes (ctcl's 5 runs, cli's 2) keep the original auto-fit behavior.

**"Run" now auto-downloads NMASS/NCDEX first if missing (MCX 90% deliberately excluded):** previously, clicking a run tile's "Run" just called the pipeline directly, which would fail pre-flight if CM/FO/CO/ClientNetEffectiveDeposits weren't already sitting in the folder — the user had to remember to click "📥 NMASS/NCDEX" first, every time. `pxRun('ms15')` now routes through `ms15RunSmart()`: it checks `/ms15/readiness` for just the 4 NMASS/NCDEX keys (CM_CLIENT_LEVEL/FO_CLIENT_LEVEL/CO_CLIENT_LEVEL/CLIENT_NET_EFFECTIVE_DEPOSITS — RISK_REPORT and MCX_90_PERCENT are deliberately excluded from this check), auto-triggers `ms15NmassDownload()` (now accepts an `onDone(ok)` callback) only if any of those 4 are missing for today, then calls the actual run (`_pxRunReal('ms15')`, the generic SSE runner factored out of `pxRun()`) once the files are confirmed present — skipping the download step entirely if they're already there. MCX 90% is intentionally NEVER auto-triggered this way since it requires a person to type in an RSA SecurID token; it stays a separate manual button.

**Retired:** the old standalone "Margin Shortage" download-only tile (its own independent NSCCL/NCDEX downloader using EasyOCR for captcha, unrelated to `NMASS_NCFE_API_MIX.PY`) has been removed from the dashboard. `core/pipelines/margin_shortage.py` and `margin_shortage_run.py` are now stub files that raise a clear error if anything still imports them — everything above replaces them.

---

### CLI Prudential Limit — OLD vs NEW pipelines (complete picture)

**What "OLD" and "NEW" mean:** both compute `CLI_PRUDENTIAL_LIMIT.txt` (per-account prudential limit) from the same `Allocation_Detail` FTP export, then both continue through the identical Download → MCX Web Allocation → FTP upload → backup chain. They only differ in STEP 2's math:
- **OLD** — plain base-allocation sum per account: `CMCreditObligation + StockDiff + EpiCredit + MtfCashColl + ExchRepledgeAdj + FnoDebitBill + EPNLimit + HoldCashMgn + MTFExcess`, grouped by `AccountID`. No RISK_REPORT or Kambala involved.
- **NEW** — same base sum, PLUS an adjustment: for every account where `RISK_REPORT_<date>.csv`'s `MTF_STATUS == 'Y'`, add that account's Kambala Z-column (`abs()` of column index 25 in `Kambala_Omny_Depo_Adhoc_All_level.txt_<date>.txt`, `|`-delimited). NEW also runs a STEP 0 pre-check first: if either `RISK_REPORT_<date>.csv` or the Kambala file isn't available (checked locally, then copied from `SA_COMMON_DIR`/fetched from FTP), the run stops with a friendly `[MISSING_FILE]` message instead of silently producing wrong numbers.

| What | File | Called by |
|---|---|---|
| OLD pipeline (base math only) | `core/pipelines/cli_allocation.py` | `/cli/run-old-stream` |
| NEW pipeline (precheck + MTF/Kambala adjustment) | `core/pipelines/cli_allocation_new.py` | `/cli/run-new-stream` |
| Shared Allocation_Detail FTP fetch + FTP folder cleanup | `_pb_fetch_allocation_detail()`, `_pb_cleanup_allocation_ftp_folder()` in `core/pipeline_box.py` | both OLD and NEW |
| CM/FO/CO/NCDEX collateral download | `core/pipelines/cli_download.py` | `/cli/download-stream`, and automatically inside both Run chains |
| MCX Web Allocation download + convert | `core/pipelines/cli_mcx_download.py` | `/cli/mcx-download-stream`, and automatically inside both Run chains |
| FTP upload of final result | `core/pipelines/cli_upload.py` | automatically inside both Run chains |

**CLI run sequence (what happens when you click Run OLD or Run NEW):**
1. (NEW only) STEP 0 precheck — confirm today's RISK_REPORT and Kambala files exist (local → `SA_COMMON_DIR` copy → FTP fetch, in that order); stop with a clear message if either is missing.
2. STEP 1 — fetch `Allocation_Detail_<date>.csv` (local-first, FTP fallback via `_pb_fetch_allocation_detail()`), then clean up the FTP folder so only today's file remains (`_pb_cleanup_allocation_ftp_folder()`).
3. STEP 2 — build `Intermediate.csv` + `CLI_PRUDENTIAL_LIMIT.txt` (OLD: plain sum; NEW: sum + MTF/Kambala adjustment).
4. Download CM/FO/CO/NCDEX collateral files (skipped if already present today).
5. Log in to TCS BANCS (reusing `sa_codes/mcx_deposit_downloader.py`, same RSK383 report SA's MCX Deposit button uses) and download the MCX Web Allocation export — **recognizes either `MCX_DEPOSIT_HH.MM.xlsx` (its own output) or the raw `<random>-<14digits>.xlsx` export name** (for a manually-placed file) as "already downloaded today", so it doesn't re-trigger a fresh TCS BANCS login every run.
6. Convert the xlsx export to `MCX_WebAllocationDeallocation.csv` (finds the "UCC Code" header row, strips identifying columns, strips thousands-separator commas via `df.map()` — NOT `df.applymap()`, which newer pandas versions removed).
7. Upload the result to FTP, then archive.

**Dashboard buttons:** "Run OLD" and "Run NEW" are two separate always-visible buttons (`cliRunOld()` / `cliRunNew()` in `dashboard.html`), stacked in a `.cli-run-stack`, styled like CTCL's run button. Both trigger `captchaStartPoller()` on start because the Download step's underlying NMASS login can require a captcha even though CLI didn't ask for one directly.

---

### `core/ctcl_readiness.py`
**What it does:** Pre-flight check before running CTCL vs NMASS. Checks that all required input files are present and not stale. Returns a list of missing/problem files so the dashboard can warn the user before they click Run.

---

### `core/readiness.py`
**What it does:** General-purpose pre-flight checker used by multiple pipelines. Checks folder existence, file presence, and file dates.

---

### `core/daily_summary.py`
**What it does:** Generates a daily summary email covering all pipelines that ran that day — how many shortfalls, which clients, peak margin status, etc.

---

### `core/root_cause.py`
**What it does:** Given a client in shortfall, tries to identify the probable cause (missing file, wrong collateral, data timing issue) by examining the available files.

---

### `core/code_index.py`
**What it does:** Indexes all Python source files in the project so the AI agent can answer questions about the codebase itself (e.g. "where is the CTCL email sent?").

---

## `core/pipelines/` — Individual pipeline modules

Each pipeline has two files: `xyz.py` (the logic) and `xyz_run.py` (the SSE wrapper that streams output to the dashboard). The `_run.py` file is what `api.py` imports.

### `core/pipelines/ctcl_vs_nmass.py` + `ctcl_vs_nmass_run.py`
**What it does:** The main CTCL vs NMASS pipeline. Reads CM/FO/CO collateral files, NCDEX deposits, MCX margin file, and the RISK_REPORT, computes whether each client's Update Limits deposit covers the actual margin required, emails zone-wise reports, and backs up the output file.

**Input files needed:**
- `CM_ClientLevelCollaterals_<date>.csv` — CM segment collaterals from NSE
- `FO_ClientLevelCollaterals_<date>.csv` — FO segment collaterals from NSE
- `CO_ClientLevelCollaterals_<date>.csv` — CO segment collaterals from NSE
- `ClientNetEffectiveDeposits_<date>.csv` — NCDEX net deposits
- `Update Limits <date>.xlsx` — what was uploaded to exchange (CTCL)
- `RISK_REPORT_<date>.csv` — client-level margin data
- MCX deposit xlsx — client margin limit from MCX

**Output:** `FULL_FILE_CTCL_VS_NMASS_<run_number>.xlsx` saved and backed up in `BACKUPFILES AUTOMATED/`

**To change which columns are summed for a segment's deposit:** Edit the column name lookups in `ctcl_vs_nmass.py` (look for `Cash allocated`, `Cash-Others`, `Non-cash Securities`).

---

### `core/pipelines/peak_margin.py` + `peak_margin_run.py`
**What it does:** Tracks intraday peak margin across four snapshots (i01=10:30, i02=12:30, i03=13:45, i04=15:30). Each run fills one column of a cumulative report. New day = clean slate.

**Input:** NSE i01/i02/i03/i04 files downloaded by the 70 Files Downloader.  
**Output:** `peak_margin_report.json` in `PEAK_FOLDER`. Emailed after all 4 snapshots.

**To add a new client code to track:** Update `PEAK_FO_CODES` or `PEAK_CM_CODES` in `.env`.

---

### `core/pipelines/concentration.py` + `concentration_run.py`
**What it does:** Checks MTF concentration limits — whether any client holds too large a position in a single stock relative to the portfolio. Downloads index files (Nifty50/BSE200/BSE500) and the KORP shortage file, then flags violations.

**Credentials needed in `.env`:** `KORP_USERNAME`, `KORP_PASSWORD`, `KORP_CLIENT_IP`, `KORP_CLIENT_MAC`, `KORP_FIRM_ID`

---

### `core/pipelines/margin_shortage.py` + `margin_shortage_run.py` — RETIRED
**What they were:** the OLD standalone Margin Shortage download-only button's backend (its own independent NSCCL/NCDEX downloader using EasyOCR + pytesseract for captcha, unrelated to `NMASS_NCFE_API_MIX.PY`). Both are now stub files that raise a clear error if called. Replaced by `core/pipelines/margin_shortage_15min.py` (processing) + `nmass_download.py`/`mcx90_download.py` with `pipe="ms15"` (downloads) — see the "Margin Shortage (15-min) — where the code runs" section above.

---

### `core/pipelines/margin_shortage_15min.py`
**What it does:** The Margin Shortage (15-min) pipeline — ported from the uploaded `15minutesfinal_whatsapp.PY` with zero calculation/threshold/email/WhatsApp logic changes. Reads CM/FO/CO collateral files, NCDEX deposits, MCX 90% file, and RISK_REPORT (all from `MARGIN_SHORTAGE_FOLDER`), computes DEPOSIT NMASS / TOTAL MARGIN / DIFFERENCE(F-G) / MARGIN WARNING %, emails main + 4 zone reports, sends Gupshup WhatsApp alerts to clients above the threshold, then backs up + cleans the folder. Wrapped in `run_margin_shortage_15min()` with the same AI (Groq) `[FRIENDLY_MSG]` fallback pattern as `ctcl_vs_nmass.py`.

**Input files needed:** same 5 files as CTCL vs NMASS (CM/FO/CO/ClientNetEffectiveDeposits/RISK_REPORT) plus the MCX 90% file — all read from `MARGIN_SHORTAGE_FOLDER`, downloaded independently from CTCL's copies (or auto-shared for MCX 90% at 3 paired run times — see above).

**Output:** `Shortage_vs_CC_Deposit.xlsx` (main) + `Shortage_vs_CC_Deposit_<ZONE>.xlsx` (zone shortfall files, only for `FINAL SHORT CM ADJ(H+J)` < 0 rows), backed up to `BACKUPFILES AUTOMATED/<date>/<run>/`.

**WhatsApp alert dedup:** `margin_alert_tracker` DB table (see `core/db.py` below), NOT a local JSON file — see the run-sequence section above for the exact threshold/swing logic.

---

### `core/pipelines/files_downloader_70.py` + `files_downloader_70_run.py`
**What it does:** Downloads ~70 files from NSE, MCX, NCDEX, EPI (KORP), and other exchanges for a given trade date. This is the "morning download" that populates raw data for all other pipelines.

**Destinations:** SA CM/FO dirs, MCX INTRA/SAR dirs, NCDEX EODSAR dir, etc. (all in `.env`)

---

### `core/pipelines/nmass_download.py`
**What it does:** Downloads the NMASS collateral files (CM/FO/CO/NCDEX) by running `ctcl_codes/NMASS_NCFE_API_MIX.PY` as a subprocess. Streams progress live. `stream_nmass_download(pipe="ctcl")` — pass `pipe="ms15"` to save into `MARGIN_SHORTAGE_FOLDER` instead of `CTCL_NMASS_FOLDER` (sets env var `NMASS_SAVE_DIR`, which `NMASS_NCFE_API_MIX.PY`'s `DOWNLOAD_FOLDER` now reads instead of a hardcoded literal). Files are NEVER shared between the two pipes — each downloads independently.

**Script path:** env var `CTCL_NMASS_SCRIPT` (default: `D:\CLAUDE\rms-copilot\ctcl_codes\NMASS_NCFE_API_MIX.PY` — all CTCL code runs from this repo now, nothing from `D:\MY CODES WORKING`; see the "CTCL vs NMASS — where the code runs" section above).

---

### `core/pipelines/mcx90_download.py`
**What it does:** Downloads the MCX 90% file by running `ctcl_codes/MCX_90_PERCENT_FILE_API.py` as a subprocess. Requires RSA SecurID token (dashboard polls `/ctcl/token/pending` or `/ms15/token/pending`). TCS BANCS can take 4-5 minutes. `stream_mcx90_download(pipe="ctcl")` — pass `pipe="ms15"` for the Margin Shortage box (sets env var `MCX90_SAVE_DIR`, which `MCX_90_PERCENT_FILE_API.py`'s `INPUT_FOLDER` now reads instead of a hardcoded literal). Skips the download entirely if today's file is already present in the target folder (e.g. shared from the sibling box). After a successful download, checks the static `_SHARE_PAIRS` table against the current run window and copies the file into the sibling pipe's folder at the 3 confirmed paired times — see the "Margin Shortage (15-min)" section above for the exact pairing table.

---

### `core/pipelines/mcx_deposit_download.py`
**What it does:** Downloads the MCX client margin limit file (RSK383) from `eclear.mcxccl.com` via `sa_codes/mcx_deposit_downloader.py`. Requires RSA SecurID token entry (a popup appears on the desktop). Streams progress live. Writes a persistent log to `logs/short_allocation/MCX_DEPOSIT__<timestamp>.log`.

**Timeout:** 2 minutes per queue check — TCS BANCS server can be slow.

**Reused by CLI's Run NEW/OLD chain** (`core/pipelines/cli_mcx_download.py`) for the same RSK383 report — see the CLI Prudential Limit section below.

---

### `core/pipelines/payout_inspection.py` + `payout_inspection_run.py`
**What it does:** Inspects payout requests. Daily mode ingests new files and deduplicates; client mode shows the full payout history for specific client IDs. Uses Polars for fast CSV processing.

---

### `core/pipelines/mtf_order.py` + `mtf_order_run.py`
**What it does:** MTF (Margin Trading Facility) order report — checks which clients have open MTF positions and whether they meet the margin requirement.

---

### `core/pipelines/cnc_f11.py` + `cnc_f11_run.py`
**What it does:** CNC (Cash and Carry) F11 report — processes the F11 file from NSE and generates a shortfall report for CNC segment clients.

---

### `core/pipelines/ftp_download.py` + `ftp_download_run.py`
**What it does:** Downloads files from the NSE FTP server. Uses credentials from `.env` (`FTP_HOST`, `FTP_USER`, `FTP_PASS`).

---

### `core/pipelines/margin_top20.py`
**What it does:** Generates a Top-20 margin shortfall report — the 20 clients with the highest shortfall amount.

---

### `core/pipelines/code2_shortage.py`
**What it does:** CODE-2 shortage report — identifies clients whose collateral is below the required margin by a significant amount (the "shortage" list sent to RMs).

---

### `core/pipelines/report_image.py`
**What it does:** Converts Excel report sheets to images (PNG) so they can be embedded directly in WhatsApp messages or emails without the recipient needing Excel.

---

### `core/pipelines/reset_peak_day.py`
**What it does:** Resets the peak margin counter to start fresh for a new day. Run at start of day before the first i01 snapshot.

---

### `core/downloaders/nse.py`
**What it does:** NSE API client — handles authentication, session management, and file download requests from the NSE member portal. Used by `files_downloader_70.py`.

---

## `sa_codes/` — Short Allocation worker scripts

These are the actual SA scripts. They are run as **subprocesses** by `core/short_allocation.py` — never imported directly. They read settings from `.env` via the `_SAENV()` shim at the top of each file.

### `sa_codes/newshortallocationfiledownloader.py` ← Code-1
**What it does:** Downloads all input files for Short Allocation from the NSE extranet (LASUPTO3 system). Gets `.lis` and `.lis.gz` files for each allocation slot. If a file doesn't exist on the server (NSE hasn't published it yet), creates a blank placeholder file with the correct format so Code-2 doesn't crash.

**Blank file logic:** `_create_blank_file(filename, out_dir)` — handles `.lis` and `.lis.gz` patterns. The blank file contains a single line: `DD-MON-YYYY,EMP62,0.00,0.00,0.00,0.00,0.00`.

**Output:** `.lis` files in `SA_CM_DIR` and `SA_FO_DIR`.

---

### `sa_codes/shortallocation_final_new_with_co.py` ← Code-2
**What it does:** The core Short Allocation calculation engine. Reads the downloaded `.lis` files, applies the allocation logic (comparing available collateral against allocation requirements), and produces the Update Limits file for the exchange. Also handles CO (Commodity) segment.

**Output:** Update Limits `.xlsx` file placed in `SA_OUTPUT_DIR` with a run-number suffix.

---

### `sa_codes/LASUPTO3.py` ← Code-3 (runs 1, 2, 3)
**What it does:** LAS (Loan Against Securities) funding script for runs 1-3. Reads the SA output, calculates LAS funding amounts, generates the LAS report image, and emails/WhatsApps the result to the team.

**Run selection:** Reads the run suffix from `run_state.txt` — run 1 fills slot 1, run 2 fills slot 2, etc.

---

### `sa_codes/LAS4.py` ← Code-3 for run 4
**What it does:** Same as LASUPTO3 but for run 4 (end-of-day LAS). Has slightly different formatting/logic for the 4th run.

---

### `sa_codes/mcx_deposit_downloader.py`
**What it does:** Logs in to `eclear.mcxccl.com` (MCX clearing portal), navigates to the RSK383 report, and downloads the client margin limit file. Requires RSA SecurID OTP. The OTP popup appears on the Windows desktop.

**Output:** MCX deposit `.xlsx` file placed in `CTCL_NMASS_FOLDER`.

---

### `sa_codes/ncdex_downloader.py`
**What it does:** Downloads the NCDEX `ClientNetEffectiveDeposits` file from the NCDEX portal. Uses CAPTCHA solving.

---

### `sa_codes/las_email_downloader.py`
**What it does:** Downloads LAS-related email attachments automatically (e.g. the daily LAS funding file sent by the counterparty bank). Used as input to LASUPTO3/LAS4.

---

### `sa_codes/run_post_email_LAS4.py`
**What it does:** Post-email processing for LAS4 — runs after the LAS4 email is sent to do any cleanup or follow-up actions.

---

### `sa_codes/_checkpoint.py`
**What it does:** Checkpoint system for long-running SA scripts. Saves progress mid-run so that if the script crashes, it can resume from the last checkpoint instead of starting over.

---

## `ctcl_codes/` — CTCL download scripts (external, do not modify)

### `ctcl_codes/NMASS_NCFE_API_MIX.PY`
**What it does:** Downloads CM/FO/CO collateral files from `connect2nsccl.com` and NCDEX deposits from `ncfe.ncdex.com`. This is the **original script** — do not modify it. `core/pipelines/nmass_download.py` runs it as a subprocess.

### `ctcl_codes/MCX_90_PERCENT_FILE_API.py`
**What it does:** Downloads the MCX 90% file (used for MCX90 reporting). Run via `core/pipelines/mcx90_download.py`.

---

## `data/circulars/` — Knowledge base

Text files containing SEBI/NSE/MCX regulatory circulars and internal process documentation. The AI assistant and compliance checker search these files to answer questions.

| File | Contains |
|---|---|
| `ctcl_nmass_process.txt` | Full explanation of the CTCL vs NMASS process |
| `peak_margin_penalty.txt` | SEBI peak margin penalty rules |
| `collateral_haircut.txt` | NSE haircut rates for collateral valuation |
| `mtf_concentration.txt` | MTF concentration limit rules |
| `payout_process.txt` | Payout request handling rules |
| `pipeline_knowledge.txt` | Internal notes on how each pipeline works |
| `nsccl_margin_rules.txt` | NSCCL margin collection rules |

---

## `logs/` — Runtime logs

All pipeline runs write their full output to log files here. These logs persist even if the browser tab is closed. Viewable from the 🗂️ Logs modal in the dashboard.

| Subfolder | Contains logs for |
|---|---|
| `logs/short_allocation/` | SA-01/02/03/04 runs, MCX Deposit download |
| `logs/ctcl/` | CTCL vs NMASS runs, NMASS download, MCX90 |
| `logs/agent_log.jsonl` | AI agent conversation history |

**Log filename format:** `SA-01__<script_name>__<YYYYMMDD_HHMMSS>.log`

---

## `core/pipelines/` file naming convention

| Suffix | Purpose |
|---|---|
| `xyz.py` | Pure logic — functions that do the actual work |
| `xyz_run.py` | SSE wrapper — `stream_xyz()` generator that `api.py` imports |

**Adding a new pipeline:**
1. Write `core/pipelines/mypipe.py` with `def run_mypipe(): ...`
2. Write `core/pipelines/mypipe_run.py` with `def stream_mypipe(): ...` (SSE generator)
3. Import `stream_mypipe` in `api.py` and add a `@app.get("/mypipe-stream")` route
4. Add a button in `dashboard.html` that calls `startStream('/mypipe-stream', ...)`
5. Optionally add an entry to `PIPELINES` dict in `core/pipeline_box.py`

---

## Key data flows

### Short Allocation daily flow
```
Morning:
  Dashboard → /sa/download-stream → newshortallocationfiledownloader.py
    → downloads .lis files from NSE extranet
    → creates blank files for missing slots

User uploads:
  dashboard.html file-upload box → /sa/upload → core/short_allocation.py
    → validates filename + date → saves to CTCL_NMASS_FOLDER

Run button:
  Dashboard → /sa/stream?pipe=SA-01 → core/short_allocation.py
    → Code-1 (downloader) → Code-2 (SA calc) → Code-3 (LAS)
    → logs/short_allocation/SA-01__*.log
    → Update Limits .xlsx saved to SA_OUTPUT_DIR
```

### CTCL vs NMASS daily flow
```
Morning:
  Dashboard → /nmass-download-stream → NMASS_NCFE_API_MIX.PY
    → CM/FO/CO/NCDEX files saved to CTCL_NMASS_FOLDER

User uploads MCX file + Update Limits file via drag-and-drop

Run button:
  Dashboard → /ctcl-stream → ctcl_vs_nmass.py
    → reads all input files
    → computes shortfall per client
    → emails zone reports
    → FULL_FILE_CTCL_VS_NMASS_<N>.xlsx backed up
```

### Client Lookup flow
```
User types client code, picks date range, selects runs → clicks Search
  dashboard.html → GET /px/client-lookup?client_codes=SN120&from=...&runs=1,2,3
    → core/pipeline_box.py: pb_client_lookup()
      → scans BACKUPFILES AUTOMATED/ for date-stamped subfolders
      → reads CM/FO/CO CSVs with Polars (fast)
      → reads MCX xlsx with polars/fastexcel
      → reads FULL_FILE xlsx for output (DEP_NMASS, DEP_CTCL, DIFF)
      → returns JSON: {inputs: [...], outputs: [...]}
    → dashboard.html: pxShowLookupPopup()
      → Table 1: Input Deposits per run (CM/FO/CO/MCX/NCDEX + Total NMASS)
      → Table 2: CTCL vs NMASS output per run (Deposit CTCL, Difference, Remarks)
```

---

## Multi-user concurrency & generic error explanation (server-wide)

This dashboard is used by several people at once (2 today, more later). Two problems this
caused, and how they're fixed, in one place — since these mechanisms are shared by
every pipeline (ctcl/ms15/cli/sa/sa's MCX Deposit), not built per-pipe:

**1. Two people could silently launch conflicting subprocesses.**
`core/pipeline_box.py`'s `_PB` dict (`{proc, cancel, running, pipe, started_at}`) is
ONE shared "is any exchange-automation subprocess running right now" slot for the
WHOLE server — used by `stream_pipeline()` (ctcl/ms15/cli's main runs) and
`_stream_code()` (their retry/test sub-phases). It used to have no "already running?"
check at all, so a second Run click (from anyone, on any pipe) would just clobber
`_PB["proc"]` and leak the first subprocess untracked. `_PB_LOCK` (a `threading.Lock`)
now makes "check if busy, else claim it" atomic; if busy, the caller gets back a clear
`{"type":"error","msg":"⚠ X is already running..."}` SSE event instead of a second
subprocess quietly starting.

Short Allocation (`core/short_allocation.py`'s `stream_short_allocation()`) and its
MCX Deposit downloader (`core/pipelines/mcx_deposit_download.py`'s
`stream_mcx_deposit_download()`) used to run on a COMPLETELY separate dict (`_SA`) and
an untracked module attribute (`api._sa_mcx_dep_proc`) respectively — meaning either one
could run genuinely concurrently with ctcl/ms15/cli, or with each other, with nothing
stopping it. This was very likely the cause of reported captcha/RSA-token cross-talk
("ran CTCL, NCDEX captcha didn't show up; ran MCX Deposit in SA, captcha showed there
instead, then RSA token — previous one is lagging"). Both now ALSO claim the same
shared `_PB` slot before starting (`_PB["pipe"] = "sa"` / `"sa_mcx_deposit"`), releasing
it in a `finally` block no matter how they exit — so there is exactly ONE real
cross-subsystem lock, not five independent ones. `_busy_label_for(key)` in
`pipeline_box.py` turns whatever key is holding the slot into a human label (falls back
to `_EXTRA_BUSY_LABELS` for `sa`/`sa_mcx_deposit`, which aren't in the `PIPELINES` dict).

**2. A run's busy/failed state was only visible on the screen that started it.**
`pb_status(pipe)` (used by `GET /px/status`) and `GET /sa/status` both now also return
`server_busy`, `server_busy_pipe`, `server_busy_label`, `server_busy_since` — the real
backend state, not a per-browser-tab flag. `dashboard.html` polls `/px/status`/`/sa/status`
every 5s from EVERY connected browser (`pxRenderRuns()`/`saRenderRuns()`), and uses these
fields (not just the local `_pxRunning`/`_saRunning` flag) to grey out Run buttons and
show a "⚠ X is already running (started on another screen)" banner on every screen, not
only the one that clicked Run.

**3. Captcha/RSA-token modals didn't say WHICH box/pipe they belonged to.**
`_captchaSource`/`_tokenSource` (`'ctcl'|'ms15'|'sa'`) already existed to route the
Submit/Cancel actions to the right backend endpoint, but the modal itself only showed
the exchange portal name ("NCDEX CAPTCHA", "MCX 90% Authentication") — identical
wording whether it was CTCL's login or Margin Shortage's own login. A small badge
(`#captchaBoxLabel` / `#tokenBoxLabel`, driven by `_pipeBoxLabel(src)`) now shows the
owning box's name ("📍 CTCL vs NMASS", "📍 Margin Shortage (15-min)", etc.) inside both
modals.

**4. Error explanations used to require a manual "ask AI" click and gave vague guesses.**
`extract_known_failure_reason(log_text)` (`core/pipeline_box.py`) deterministically
parses the EXACT structured "Missing required files:" / wrong-date / stale-file blocks
that `pre_run_file_check()` already prints identically in both `ctcl_vs_nmass.py` and
`margin_shortage_15min.py` — checked BEFORE ever calling the AI. `GET /px/explain-error`
returns `{ok, explanation, source: "known"|"ai"}`; the dashboard's `pxOpenView()`/
`pxExplainError()` display it AUTOMATICALLY (no button click) with a clear visual
distinction: a red "known fact" box for `source:"known"`, vs. a violet box explicitly
labeled "🤖 AI best guess (not a confirmed cause)" for `source:"ai"` (Groq free-tier
fallback, only used when no known pattern matched).

---

## HTTPS (self-signed, for LAN access)

The dashboard runs over plain HTTP by default (`START_SERVER.bat` → `uvicorn ... --host 0.0.0.0 --port 8000`),
which is fine for `localhost`-only use but shows "Not Secure" / sends captcha and RSA-token
input unencrypted once a second machine reaches it via `http://<this-PC's-LAN-IP>:8000`.
`generate_ssl_cert.py` is a one-time script (`pip install cryptography --break-system-packages`
then `python generate_ssl_cert.py`) that creates a self-signed cert/key in `certs/`
(git-ignored — never commit it), covering `localhost`, `127.0.0.1`, the machine's hostname,
and its auto-detected LAN IP. `START_SERVER.bat` checks for `certs/cert.pem` + `certs/key.pem`
and automatically switches to `https://` (`--ssl-keyfile`/`--ssl-certfile`) when present,
otherwise falls back to plain HTTP with no other changes needed — `dashboard.html`'s
`API` constant already uses `location.origin`, so it follows whichever protocol the page
was loaded over automatically. Each browser shows a one-time "not trusted" warning on
first visit to a self-signed cert (expected — click through it); this is appropriate for
an internal LAN tool but not a substitute for a real CA-issued cert if this is ever
exposed outside the local network.

---

## Where to go to fix common issues

| Problem | File to open | What to change |
|---|---|---|
| Wrong email recipient | `.env` | `CTCL_MAIN_TO`, `CTCL_MAIN_CC`, etc. |
| Wrong folder path | `.env` | The relevant `*_FOLDER` or `*_DIR` variable |
| Blank file format wrong | `sa_codes/newshortallocationfiledownloader.py` | `_create_blank_file()` |
| Upload validation too strict | `core/short_allocation.py` | `_name_ok()` or `_date_ok()` |
| Client lookup not finding MCX file | `core/pipeline_box.py` | MCX detection logic in `pb_client_lookup()` |
| Client lookup column wrong | `core/pipeline_box.py` | `_pb_col()` call for that segment |
| UI table column name wrong | `dashboard.html` | `pxShowLookupPopup()`, `subTh()` or `_THL`/`_THR` calls |
| CTCL calculation logic | `core/pipelines/ctcl_vs_nmass.py` | The main `run_ctcl_vs_nmass()` function |
| SA calculation logic | `sa_codes/shortallocation_final_new_with_co.py` | (do not modify lightly) |
| Peak margin client list | `.env` | `PEAK_FO_CODES`, `PEAK_CM_CODES` |
| New pipeline needed | `api.py` + `core/pipelines/` + `dashboard.html` | See "Adding a new pipeline" above |
| AI agent wrong answer | `data/circulars/` | Add/update the relevant `.txt` file |
| A run looks "hung" with a near-empty log | Whatever file spawns the subprocess | Confirm `PYTHONUNBUFFERED=1` is in `env` **and** `-u` is passed to the interpreter — see "Generic pipeline runner" above |
| CLI OLD/NEW Run button stays clickable during a run | `dashboard.html` | Make sure the run function calls `pxStatus(pipe)` immediately (not just at the end) — see `cliRunOld()`/`cliRunNew()` |
| NCDEX/NCFE captcha download crashes (cv2 `!_src.empty()`) | Whichever downloader | Validate the response body before saving — check it doesn't start with `<` (HTML/XML error page), not the `Content-Type` header (NCDEX doesn't reliably send `image/*`) |
| Margin Shortage calculation/WhatsApp/email logic | `core/pipelines/margin_shortage_15min.py` | `_run_margin_shortage_15min_impl()` — ported as-is from `15minutesfinal_whatsapp.PY`, do not change the math/thresholds without explicit sign-off |
| MCX 90% not being shared between CTCL and Margin Shortage at a paired run time | `core/pipelines/mcx90_download.py` | `_SHARE_PAIRS` table / `_current_window_index()` — confirm the relevant `CTCL_RUN*_WINDOW`/`MS15_RUN*_WINDOW` env vars still match the pairing table in the ARCHITECTURE section above |
| Margin Shortage WhatsApp alert re-sent (or not sent) unexpectedly | `core/db.py` / `core/db_postgres.py` | `get_margin_alert()`/`set_margin_alert()` and the `margin_alert_tracker` table — confirm `USE_POSTGRES` matches across all machines running this pipeline |
| A run silently didn't start / no error shown when someone else was already running something | `core/pipeline_box.py` | `_PB_LOCK`/`_PB` — check `server_busy_pipe`/`server_busy_label` via `GET /px/status` or `GET /sa/status`; see "Multi-user concurrency" section above |
| Error explanation missing/vague on a failed run | `core/pipeline_box.py` | `extract_known_failure_reason()` — add a new header pattern to `_KNOWN_SECTION_HEADERS` if a pipeline prints a new kind of structured failure block |
| Captcha/token modal doesn't say which box it's for | `dashboard.html` | `_pipeBoxLabel(src)` — add an entry if a new pipe/box gets its own captcha or RSA token flow |

---

## Git / GitHub

Remote: `https://github.com/KartavyaDhillon/rms-copilot-demo.git` (branch `main`). A scheduled local task auto-commits on its own cadence (commit messages are throwaway — don't read anything into them), but **auto-commit does not auto-push**. Push manually when you want GitHub to reflect the current state:
```
git push origin main
```

**Never commit `*.db` / `*.db-wal` / `*.db-shm` / `*.db-journal`.** `rms_history.db` (the SQLite run-history database — see `core/db.py`) grows past GitHub's 100MB file limit and once it's in history, deleting it in a later commit does NOT fix a rejected push — the oversized blob is still in the history git tries to transfer. If this ever recurs, the fix is `git filter-repo --path rms_history.db --path rms_history.db-wal --path rms_history.db-shm --invert-paths --force` (rewrites history — re-add the `origin` remote afterward, filter-repo removes it as a safety measure), then a force push. `.gitignore` already excludes these patterns going forward.

---

*Last updated: July 2026 (through the CLI Prudential Limit OLD/NEW split, MCX relogin/tab-id fix, PYTHONUNBUFFERED subprocess fix, NCDEX captcha validation fix, Git history cleanup, the Margin Shortage (15-min) pipeline, RISK_REPORT COMMON FILES lookup, the generic View/Explain-error feature, deterministic-first error explanation (`extract_known_failure_reason`), the server-wide `_PB_LOCK` concurrency guard now shared by ctcl/ms15/cli/sa/sa's MCX Deposit, `server_busy`/`server_busy_pipe`/`server_busy_label` polling so Run buttons disable on every connected screen, and captcha/token modal pipe/box labeling). Update this file whenever you add a new pipeline, rename a key variable, or significantly change a function's behaviour.*
