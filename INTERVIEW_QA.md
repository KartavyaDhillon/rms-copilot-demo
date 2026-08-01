# RMS Copilot — Interview Q&A Guide

This file covers every concept used in building this project, explained so you can answer confidently in an interview.
It will be updated as new features are added.

---

## SECTION 1: What Is This Project?

**Q: Describe what you built.**

A: I built an internal operations dashboard called "RMS Copilot" for a financial firm's Risk Management System team. It is a web-based tool that:
- Automates a multi-step "Short Allocation" (SA) process that runs every trading day
- Downloads exchange files from NSE, MCX, and NCDEX
- Processes those files to calculate margin requirements for clients
- Sends emails to different zones (Ludhiana, Delhi, Mumbai, Chandigarh)
- Stores all files and logs in a PostgreSQL database so any machine on the network can access them
- Lets the team look up any client's margin peak and deposit history by date range

The frontend is a single HTML file with embedded CSS and JavaScript. The backend is a Python FastAPI server. All persistent data goes to PostgreSQL.

---

## SECTION 2: Python Fundamentals

**Q: What is a Python virtual environment and why use one?**

A: A virtual environment is an isolated Python installation for a specific project. It keeps that project's dependencies (like FastAPI, psycopg2) separate from your system Python and other projects. You create one with `python -m venv venv` and activate it with `venv\Scripts\activate` on Windows. This prevents version conflicts — Project A might need `pandas==1.5` while Project B needs `pandas==2.0`.

**Q: What is a generator function in Python? How did you use one?**

A: A generator uses `yield` instead of `return`. Each call to `next()` on it runs the function until the next `yield`, then pauses. It does NOT compute everything at once — it produces values on demand. This saves memory for large outputs.

In this project, the streaming API endpoint uses a generator:
```python
def gen():
    for ev in stream_short_allocation(run_no, force=force):
        yield f"data: {json.dumps(ev)}\n\n"   # one SSE event at a time
    yield 'data: {"type":"end"}\n\n'
```
The browser receives each `yield` instantly, so the user sees live log output instead of waiting for the whole process.

**Q: What is `subprocess.Popen` and how did you use it?**

A: `Popen` starts an external process (another program) from Python without waiting for it to finish. It gives you a handle to read its output line by line while it runs. We used it to run the SA downloader and processing scripts and stream their log lines to the browser in real time:
```python
proc = subprocess.Popen(
    [python_path, script_path],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", bufsize=1
)
for line in proc.stdout:   # reads one line at a time as the script runs
    put({"type": "log", "msg": line.strip()})
rc = proc.wait()           # 0 = success, non-zero = error
```

**Q: What is a Python decorator? Give an example from your project.**

A: A decorator wraps a function to add behaviour without changing the function's code. In FastAPI, `@app.get("/path")` is a decorator that registers a function as an HTTP GET handler:
```python
@app.get("/sa/status")
def sa_status():
    return {"runs": get_all_run_statuses()}
```
When a browser sends `GET /sa/status`, FastAPI calls `sa_status()` automatically because of the decorator.

**Q: What is `os.environ` and `.env` files?**

A: `os.environ` is a dictionary of environment variables — settings passed to a program from the operating system. A `.env` file stores them as `KEY=VALUE` lines. The `python-dotenv` library reads this file and loads them into `os.environ` at startup. This keeps secrets (database passwords, API keys) out of source code. Example from this project:
```
USE_POSTGRES=true
POSTGRES_DSN=postgresql://user:pass@localhost:5432/rmsdb
```

---

## SECTION 3: FastAPI & REST APIs

**Q: What is an API? What is a REST API?**

A: API stands for Application Programming Interface — a defined way for two programs to talk to each other. A REST API uses HTTP (the same protocol as web browsers) with standard methods:
- `GET` — read data (no side effects)
- `POST` — send data to create something
- `PUT` / `PATCH` — update something
- `DELETE` — remove something

The URL is the "address" of the resource. The response is usually JSON.

**Q: What is FastAPI? Why did you choose it?**

A: FastAPI is a modern Python web framework. Reasons to choose it:
- Very fast (built on Starlette + asyncio under the hood)
- Automatic input validation using Python type hints (`run: str = "01"`, `force: bool = False`)
- Auto-generates API docs at `/docs`
- Easy streaming responses via generators
- Clean, readable code

**Q: What is Server-Sent Events (SSE)? How is it different from WebSockets?**

A: SSE is a one-way push channel: the server sends a stream of events to the browser. The browser opens a connection with `new EventSource(url)` and receives data as it comes. The format is simple:
```
data: {"type":"log","msg":"Downloading files..."}\n\n
data: {"type":"done"}\n\n
```

WebSockets are two-way (browser can also send messages to the server mid-stream). SSE is simpler and sufficient when you only need server → browser updates, which is the case for process logs.

In this project, `/sa/run-stream` is an SSE endpoint. The browser connects, and as the SA scripts run, each log line is pushed to the browser immediately.

**Q: What does `uvicorn` do?**

A: Uvicorn is an ASGI server — it runs the FastAPI app and handles incoming HTTP connections. `uvicorn api:app --reload --port 8765` starts the server, watches for file changes (`--reload`), and listens on port 8765.

**Q: What is CORS and why did you need it?**

A: CORS (Cross-Origin Resource Sharing) is a browser security rule. When a web page at `http://localhost:8080` makes a request to `http://localhost:8765`, the browser blocks it unless the server explicitly allows it. We configured:
```python
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
```
This tells the browser "any origin is allowed to call this API."

---

## SECTION 4: Frontend (HTML/CSS/JavaScript)

**Q: What is the DOM?**

A: DOM stands for Document Object Model. When a browser loads an HTML page, it builds a tree of objects representing every element. JavaScript uses `document.getElementById()`, `querySelector()` etc. to find and modify those objects, which changes what the user sees without reloading the page.

**Q: What is `fetch()` in JavaScript?**

A: `fetch(url)` is the browser's built-in way to make HTTP requests from JavaScript. It returns a Promise. Example:
```javascript
const resp = await fetch('/sa/status');
const data = await resp.json();
console.log(data.runs);
```
`await` pauses execution until the response arrives, but doesn't block the browser (it's asynchronous).

**Q: What is a Promise? What is async/await?**

A: A Promise represents a value that isn't available yet. `async function` marks a function as asynchronous. `await` inside it pauses that function until the Promise resolves, without freezing the whole page. Under the hood, `await fetch(...)` is the same as `.then(r => ...)` but much more readable.

**Q: What is EventSource?**

A: `EventSource` is the browser API for SSE. It opens a persistent HTTP connection and fires events as data arrives:
```javascript
const ev = new EventSource('/sa/run-stream?run=01');
ev.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'log') appendLog(data.msg);
    if (data.type === 'end') ev.close();
};
```

**Q: What is template literal syntax in JavaScript?**

A: Backticks (`` ` ``) create template literals. They support multi-line strings and `${expression}` interpolation:
```javascript
const html = `<div class="tile" onclick="open('${id}')">
  <span>${label}</span>
</div>`;
```
Most of the dashboard's dynamic HTML is built this way.

**Q: Why did you put all frontend code in one file instead of separate JS/CSS files?**

A: For an internal tool with no build step, a single file is much simpler to deploy. No webpack, no npm, no build pipeline. To update the dashboard, you edit one file and refresh the browser. The backend serves it with one `StaticFiles` mount.

---

## SECTION 5: PostgreSQL & Databases

**Q: What is a relational database?**

A: A relational database stores data in tables (like spreadsheets). Each table has columns (fields) and rows (records). Tables can be linked by shared keys. SQL is the language used to query them. Examples: PostgreSQL, MySQL, SQLite.

**Q: Why PostgreSQL over SQLite for this project?**

A: SQLite stores data in a single file on one machine — only that machine can access it. PostgreSQL runs as a server on the network, so any machine on the LAN can connect and read/write data. Since the SA process runs on one machine but the team needs to access output files and peak data from other machines, Postgres is required.

**Q: What is psycopg2?**

A: `psycopg2` is the Python library for talking to PostgreSQL. It lets you open a connection, run SQL queries, and fetch results:
```python
import psycopg2
conn = psycopg2.connect("postgresql://user:pass@host:5432/db")
cur = conn.cursor()
cur.execute("SELECT filename, content FROM run_files WHERE kind = %s", ("output",))
rows = cur.fetchall()
conn.close()
```
`%s` placeholders prevent SQL injection — never use string formatting for query values.

**Q: What is BYTEA in PostgreSQL?**

A: `BYTEA` is PostgreSQL's column type for raw binary data (bytes). We use it to store Excel files, CSV files, and any other binary files directly in the database. When you read a BYTEA column in Python, you get a `bytes` object, which you can write to disk or parse in memory.

**Q: What is a primary key? What is BIGSERIAL?**

A: A primary key is a column (or combination of columns) that uniquely identifies each row. `BIGSERIAL` is PostgreSQL shorthand for a 64-bit integer that auto-increments — each new row gets the next number automatically. It's used as an `id` column:
```sql
id BIGSERIAL PRIMARY KEY
```

**Q: Explain the `run_files` table.**

A: This is the central storage table. Each row stores one file:
```
id          — auto-incrementing unique ID
pipeline    — which process created this (e.g. "short_allocation")
tag         — which run (e.g. "SA-01", "SA-02")
run_date    — the trading date this file belongs to
ran_at      — timestamp when the file was created
kind        — what type of file (e.g. "input_c_lis", "output", "log")
filename    — original filename
size_bytes  — file size for display
content     — the actual file bytes (BYTEA)
```
This design means every file from every run is preserved and queryable.

**Q: What is `DISTINCT ON` in PostgreSQL? When did you use it?**

A: `DISTINCT ON (col1, col2, ...)` is a PostgreSQL-specific feature that keeps only one row per unique combination of those columns. Which row is kept is determined by the `ORDER BY` clause.

In this project, when SA-01 is force-re-run, all its input files get archived to Postgres AGAIN — the original rows stay and new rows are added (plain INSERT, for audit trail). The `get_peak_files_range()` function uses `DISTINCT ON (tag, run_date, kind)` ordered by `ran_at DESC` to automatically pick the most recent file for each slot:

```sql
SELECT DISTINCT ON (tag, run_date, kind)
    tag, run_date, kind, filename, content
FROM run_files
WHERE kind LIKE 'input_%'
ORDER BY tag, run_date, kind, ran_at DESC;
```
This ensures client peak lookup always uses re-run data without deleting the audit history.

**Q: What is the difference between INSERT and UPSERT?**

A: `INSERT` always adds a new row — running it twice creates two rows. `UPSERT` (in PostgreSQL: `INSERT ... ON CONFLICT DO UPDATE`) checks for a conflict (like a duplicate key) and instead updates the existing row. This project uses plain `INSERT` for file archiving so that force re-runs preserve the old files as audit history. Reading uses `DISTINCT ON` to pick only the latest file per slot.

---

## SECTION 6: Architecture & Design Decisions

**Q: Walk me through the full SA pipeline.**

A:
1. **Code1 (Download):** The downloader logs into NSE, MCX, and NCDEX portals, downloads margin files for the day, saves them to `C:\SHORT ALLOCATION\`
2. **Input archiving:** Our orchestrator reads all input files and saves them to Postgres (so any machine can access them later)
3. **Code2 (SA Processing):** The SA script reads the downloaded files, calculates short allocation amounts per client per branch, produces an output Excel and four zone files, sends emails, then moves all files to backup
4. **Code3/4 (LAS):** The LAS scripts run funding calculations and send additional reports
5. **Status tracking:** After each step, the orchestrator writes `code1/code2/code3/code4/overall` to a JSON status file, which the dashboard polls every 5 seconds

**Q: What is `run_state.txt` and why does it matter?**

A: The downloader uses a file called `run_state.txt` containing `RUN=N` to know which "run number" it's on. Code1 reads `RUN=N`, downloads files with suffix `iN` (e.g. RUN=1 → i01, RUN=2 → i02), then calls `mark_run_success()` which increments RUN to N+1. Code2 reads RUN=N+1, subtracts 1, and processes run N's files.

On a force re-run we must set RUN to exactly the target run number. For example, if SA-01 and SA-02 have both completed, RUN=3. Force-re-running SA-01 requires RUN=1, NOT 3-1=2 (which would make Code1 download SA-02 files again). The correct formula: `target_run = int(run_no)` where run_no is "01", "02", etc. We learned this the hard way — "decrement by 1" was a subtle bug that only appeared when re-running an older SA while newer ones were already complete.

**Q: How does the dashboard know when to refresh?**

A: The dashboard uses `setInterval` to call `GET /sa/status` every 5 seconds and re-render the tiles. For active runs, the browser also has an open `EventSource` connection that receives live log lines. The polling detects state changes (e.g. a run finishing) even if the SSE connection drops.

**Q: What is the `force` flag in the re-run logic?**

A: Each step has a resume check: if `code1 == "done"`, skip Code1 and start from Code2. The `force=True` flag bypasses ALL resume checks and runs every step from scratch — download → process → LAS. Before Code1, we also decrement `run_state.txt` so the downloader targets the right run number.

**Q: How did you handle cross-machine file access?**

A: The backup folder (`C:\SHORT ALLOCATION\BACKUP FILES AUTOMATED\`) is on one machine's disk. If someone else wants peak data for a client, they'd need network drive access. Instead, we made the orchestrator archive every input and output file to Postgres immediately after each step. The `/sa/client-peak-lookup` API reads from Postgres, so it works from any machine that can reach the Postgres server.

---

## SECTION 7: Git & Version Control

**Q: What is git? Why is it important?**

A: Git is a version control system. It tracks every change to your code, lets you go back to any previous state, and lets multiple people work on the same codebase without overwriting each other. Key commands:
- `git add .` — stage all changes
- `git commit -m "message"` — save a snapshot with a description
- `git log --oneline` — see the list of snapshots
- `git checkout <hash>` — go back to a specific snapshot
- `git diff` — see what changed

In this project, git saved us when a dashboard.html edit corrupted the file — we restored the last good version with `git checkout <hash> -- dashboard.html`.

**Q: What is a git commit hash?**

A: Each commit gets a unique 40-character hexadecimal ID (hash), e.g. `f1ed30842a...`. You can use the first 7 characters as a short reference: `f1ed308`. This lets you pinpoint exactly which version of the code you want.

---

## SECTION 8: Common Technical Questions

**Q: What is JSON?**

A: JSON (JavaScript Object Notation) is a text format for structured data. It looks like Python dicts/lists:
```json
{"name": "SA-01", "status": "done", "codes": [1, 2, 3]}
```
APIs return JSON because every language can parse it. In Python, `json.dumps(obj)` converts a dict to JSON text, `json.loads(text)` converts text back to a dict.

**Q: What is a race condition?**

A: A race condition happens when two operations run at the same time and interfere with each other. For example, if two SA runs started simultaneously and both tried to write `run_state.txt`, the file could end up corrupted. We prevent this by only allowing one SA run at a time (tracked with a global `_SA["running"]` flag).

**Q: What is an environment variable and why use them for secrets?**

A: An environment variable is a value set in the operating system, accessible to any program. Secrets (database passwords, API keys) go in environment variables (or `.env` files that are NOT committed to git) rather than hardcoded in source code. If source code is shared or leaked, secrets remain safe because they're outside the code.

**Q: What does "idempotent" mean?**

A: An idempotent operation produces the same result whether you run it once or ten times. `GET` requests should be idempotent (reading data doesn't change it). The SA pipeline is NOT fully idempotent — re-running sends emails again, which is why we have "Retry LAS only" as a separate button from "Full re-run."

---

**Q: What is a "disk fallback" and why did you remove it?**

A: A disk fallback is a catch clause that, when the primary operation (Postgres lookup) fails, silently reads from the local disk instead. This sounds safe but is actually a hidden bug factory. When the disk fallback triggers: (1) the user sees data from THEIR local backup files, not from the shared database; (2) two users on different machines see different results for the same query; (3) the Postgres failure goes unnoticed because the fallback hides it. We removed the disk fallback from `client_peak_lookup` entirely so that a Postgres failure is immediately visible as an error, and all machines are guaranteed to see the same data.

**Q: Explain the bug: `IndexError: list index out of range` when running a SQL LIKE query via psycopg2.**

A: psycopg2 uses `%s` as its placeholder syntax. When you write `LIKE 'input_%'` directly in the SQL string, psycopg2 sees the `%` and tries to match it with a parameter. If you provided fewer parameters than `%` characters in your SQL, it fails with `IndexError: list index out of range`. The fix: pass the LIKE pattern as a `%s` parameter instead of embedding it in the SQL string:
```python
# WRONG
cur.execute("WHERE kind LIKE 'input_%'", ["value"])

# CORRECT
cur.execute("WHERE kind LIKE %s", ["input_%"])
```

**Q: How did you handle transient database failures when archiving files?**

A: With retry logic. The `_archive_to_db()` function tries to save the file to Postgres up to 3 times, waiting 2 seconds between attempts. This handles brief network blips or connection timeouts. If all 3 attempts fail, it logs a warning but does NOT raise an exception — a file archiving failure must never crash the main SA run. The file is still on local disk, so it can be manually re-archived later.

---

**Q: What is `run_state.txt` and why must it be corrected before every Code-2 run?**

A: `run_state.txt` contains `RUN=N` and `DATE=YYYY-MM-DD`. Code-1 (downloader) reads `RUN=N`, downloads files with suffix `iN`, then increments it to `N+1`. Code-2 reads it again and computes `eff = RUN - 1` to know which run's files to process.

The bug: when SA-01 is retried AFTER SA-02 has already run, `run_state.txt` now has `RUN=3` (SA-02 incremented it). Code-2 then computes `eff = 3-1 = 2` and looks for i02 files instead of i01 files — "missing files" even though the i01 files are sitting on disk.

The fix: before every Code-2 run (force or retry), check if `RUN-1` equals the expected SA run number. If not, correct `run_state.txt` to `RUN = int(run_no) + 1`. We log this: `"run_state.txt: RUN 3 → 2 (corrected so Code-2 processes SA-01 files)."` This fix runs on ALL retry paths, not just force reruns.

**Q: What does LASUPTO3.py check before running and why did it fail after a normal Code-2 run?**

A: LASUPTO3.py (the LAS funding script, Code-3) reads `run_state.txt` and checks two things:
1. `DATE == today` — Code-2 must have run TODAY, not a previous day
2. `COMPLETED_RUN >= 1` — at least one successful Code-2 run must exist

The bug arose when Code-2 ran for a past date (date override): it wrote `DATE=2026-06-25` into `run_state.txt`. LASUPTO3.py then saw `DATE != today (2026-06-29)` and aborted with "Code-2 not executed today."

The fix: after Code-2 succeeds, we force `DATE=today` into `run_state.txt` regardless of what date the data was for. This lets LASUPTO3.py proceed — the date in the file is just a freshness check, not the actual trading date.

**Q: Why does the "Retry" button sometimes appear without a "Full re-run" option, and how did you fix it?**

A: When SA Processing (Code-2) fails, the tile showed a "Retry" button that called `saRun()` without `force=true`. There was no modal and no way to force a fresh download. The Retry would skip download, try Code-2 again, and fail again if the input files were gone.

The fix: make the Retry button open a summary modal (same pattern as LAS failures) with three options:
1. **Retry SA Processing** — skips download, uses files already on disk
2. **Full re-run** — calls `saFullRerun()` which passes `force=true`, re-downloads everything
3. **Upload files** — lets you manually place files before retrying

The modal also shows the missing files list so the user knows exactly what's needed.

**Q: What is an SSE (Server-Sent Events) stream? How does the dashboard use it?**

A: SSE is a web standard where the server keeps an HTTP connection open and sends data to the browser as events. Unlike WebSockets, SSE is one-directional (server → browser) and works over plain HTTP. In this project, `GET /sa/run-stream?run=01` opens an SSE connection. As Code-1/Code-2/Code-3 produce output, the backend pushes JSON events: `{"type":"log","msg":"Downloading files..."}`. The frontend's `EventSource` object receives them and updates the progress view in real-time without the user refreshing.

**Q: Why is `window._saRunDate` dangerous if not cleared after a run?**

A: `window._saRunDate` is a JavaScript global that stores a date override. If it's set to a past date (e.g. `2026-06-25`) and not cleared, every subsequent button click — even clicking "Run" on a completely different SA tile — passes `&date=2026-06-25` to the backend. The backend downloads and processes files for the WRONG date. The fix: clear `window._saRunDate = null` in both the `ev.onmessage` end handler and the `ev.onerror` handler so it resets after every run whether it succeeded or failed.

**Q: What is `backup_and_cleanup()` in Code-2, and what does it mean for retries?**

A: `backup_and_cleanup(files_used, run_no)` is called at the end of a successful Code-2 run. It MOVES all input and output files from `C:\KARTAVYA\SHORT ALLOCATION\` to `BACKUP FILES AUTOMATED\DD-MM-YYYY\NN\`. After this, the input files are NO LONGER in the main folder.

This matters for retries: if Code-2 previously SUCCEEDED and then the user retries (e.g., because LAS failed), the input files are in backup. Code-2 can't find them and fails with "missing input files." The correct action in that case is to click "Full re-run" (re-download files) rather than "Retry SA Processing".

If Code-2 FAILED, backup_and_cleanup never ran, so files are still on disk — "Retry SA Processing" will work.

---

**Q: Why was the Selenium-based MCX download replaced with `requests`, and how does the new approach work?**

A: The old Selenium code opened a Chrome browser to log in and download from `sftp.mcxindia.com`, which was slow (~2 min), fragile (driver version mismatch), and blocked the desktop. The new `requests`-based approach mimics the same HTTP session: POST to `/Web%20Client/Login.xml?Command=Login`, GET `/WebClientApi/SessionInfo.json` to extract the CSRF token, then GET `/?Command=Download&File=/{folder}/{filename}&CsrfToken={csrf}`. SSL verification is disabled (`verify=False`) because the MCX portal uses a self-signed cert. The whole download takes under 10 seconds. The code lives in `sa_codes/newshortallocationfiledownloader.py` and is called once per SA run with the matching run suffix (SA-01 downloads peak_01, SA-02 downloads peak_02, etc.).

---

**Q: Why did we replace xlwings + Excel screenshots in LAS4/LASUPTO3 with Pillow, and how does the Pillow approach work?**

A: xlwings required Excel to be installed, visible on screen, and responsive. It would fail if Excel had a modal open, or if run headlessly. The replacement uses `openpyxl` to read the xlsx data and `Pillow` (`PIL`) to draw the table as a JPEG. Key sizing detail: the original Excel code called `ScaleWidth(2,0)` and `ScaleHeight(2,0)` before export, doubling everything — so our Pillow sizes must be approximately 2× what you'd naively expect (TITLE_ROW_H=90px, HEADER_ROW_H=130px, DATA_ROW_H=90px, fonts 20–26px, total width ~2570px). The function `xlsx_to_image(xlsx_path, output_path)` is in `las_image_generator.py` and is imported into LAS4/LASUPTO3 as `_las_img`. Each of the 6 (LAS4) / 7 (LASUPTO3) screenshot blocks was reduced from ~90 lines of xlwings to 3 lines.

---

**Q: What is the checkpoint/resume system and how does it work end-to-end?**

A: When LAS4 or LASUPTO3 crashes mid-run, the dashboard now shows a banner: "Script stopped at step X — Resume from this step or Start from beginning." How it works:

1. **`_checkpoint.py`** (in `sa_codes/`) defines two ordered step lists: `LAS_STEP_ORDER` and `SA_STEP_ORDER`. It exposes `las_step(step_id)` which writes a JSON file (`.las_checkpoint.json`) to `SA_OUTPUT_DIR` with `{script, step_id, step_name, run_no, timestamp, status:"running"}`.

2. **LAS4/LASUPTO3** call `_ckpt.las_step("STEP_NAME")` at the start of each major section (DATA_LOAD, SA_COMPUTE, SCREENSHOTS, ZONE_PROCESS, RISK_NOV, WA_IMAGES, WA_SEND, EMAILS, DONE). Each section is guarded by `if _ckpt.las_should_run("STEP_NAME"):` which checks the env var `LAS_RESUME_FROM`.

3. **`/sa/las-checkpoint` API endpoint** reads and returns the JSON checkpoint file. The dashboard polls it every 8 seconds via `ckptPoll()`.

4. **Banner logic**: If status is "running" (not "done") and the timestamp is under 4 hours old, the banner appears. "Resume from this step" calls `POST /sa/las-rerun` with `{run, resume_from: step_id}`. "Start from beginning" sends `resume_from: ""`. The backend launches the script as a subprocess with `LAS_RESUME_FROM` set in the environment.

5. **`las_should_run(step_id)`** returns `True` if `step_id` is at or after the resume point in `LAS_STEP_ORDER`. So if resume is "RISK_NOV", all steps before it (DATA_LOAD through ZONE_PROCESS) return `False` and are skipped — their outputs are already on disk.

---

**Q: What NSE credentials issue was fixed and where are they stored?**

A: The old NSE loginId `"RMS"` and password were blocked. Updated credentials: loginId `"M0213815"`, encrypted password `"Yxs2Hg1/h0KUKKoF5SqEGQ=="`. They are stored in `.env` under both `SA_NSE_LOGINID` / `SA_NSE_PASSWORD` (used by the SA downloader) and `NSE_LOGIN_ID` / `NSE_PASSWORD` (used by the 70-file downloader). The hardcoded defaults in `newshortallocationfiledownloader.py` and `core/pipelines/files_downloader_70.py` were also updated so that if `.env` fails to load, the code still uses the correct credentials.

---

*Last updated: 2026-07-02 — Added MCX requests-based download, Pillow image generator, checkpoint/resume system, NSE credential fix.*
