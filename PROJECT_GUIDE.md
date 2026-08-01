# RMS COPILOT — Complete Project Guide
### Last updated: 2026-07-02 | Every section explained, every change mapped

---

## SECTION 0 — PROJECT ARCHITECTURE

### How to access the dashboard
```
Open browser → http://localhost:8000
```
**Do NOT open `dashboard.html` as a local file.** The backend serves it at `/` — use that URL. Large file uploads (>5MB) are blocked by the browser when opened from the file system.

### Full Folder Structure

```
D:\CLAUDE\rms-copilot\          ← PROJECT ROOT
│
├── api.py                      ← FastAPI backend (ALL endpoints here)
├── dashboard.html              ← Entire frontend (HTML+CSS+JS, one file)
├── .env                        ← All secrets & settings (never commit)
├── requirements.txt            ← Python packages
├── rms_history.db              ← SQLite history database (auto-created)
├── PROJECT_GUIDE.md            ← This file
├── CODE_MAP.txt                ← Older reference (superseded by this file)
│
├── core\                       ← Backend logic (imported by api.py)
│   ├── config.py               ← Reads everything from .env (secrets go here)
│   ├── llm.py                  ← ONLY file that calls Groq LLM
│   ├── agent.py                ← RMS Ops Agent (tools, system prompt, chat loop)
│   ├── agent_run.py            ← Streaming wrapper for agent (SSE)
│   ├── rag.py                  ← Compliance Q&A from circulars/*.txt
│   ├── db.py                   ← SQLite history database
│   ├── db_postgres.py          ← PostgreSQL version (set USE_POSTGRES=true)
│   ├── daily_summary.py        ← Morning summary from real output files
│   ├── readiness.py            ← "Can this pipeline run right now?" checks
│   ├── root_cause.py           ← "Why is client X in shortfall?" explanation
│   ├── notify.py               ← WhatsApp + email sending
│   ├── compliance.py           ← /ask endpoint logic
│   ├── penalty.py              ← Margin penalty calculator (agent tool)
│   ├── code_index.py           ← Agent searches the codebase semantically
│   ├── short_allocation.py     ← Short Allocation orchestrator (runs SA scripts)
│   ├── pipeline_box.py         ← Generic pipeline runner (for other pipelines)
│   │
│   ├── pipelines\              ← One folder, 15+ pipelines (logic + stream pairs)
│   │   ├── peak_margin.py + _run.py
│   │   ├── margin_shortage.py + _run.py   ← RETIRED stub (see margin_shortage_15min.py)
│   │   ├── margin_shortage_15min.py       ← ACTIVE: Margin Shortage (15-min), 9x/day, own dashboard box
│   │   ├── nmass_download.py / mcx90_download.py  ← shared downloads, pipe="ctcl" or pipe="ms15"
│   │   ├── ctcl_vs_nmass.py + _run.py
│   │   ├── concentration.py + _run.py
│   │   ├── cnc_f11.py + _run.py
│   │   ├── mtf_order.py + _run.py
│   │   ├── ftp_download.py + _run.py
│   │   ├── files_downloader_70.py + _run.py
│   │   ├── payout_inspection.py + _run.py
│   │   ├── payout_download.py
│   │   ├── payout_check.py
│   │   ├── margin_top20.py
│   │   ├── code2_shortage.py
│   │   ├── report_image.py
│   │   └── reset_peak_day.py
│   │
│   └── downloaders\
│       └── nse.py              ← NSE extranet login/download (Peak Margin i01-i04 snapshots)
│
├── las_image_generator.py      ← Pillow-based LAS table image generator (no Excel)
├── mcx_peak_margin_downloader.py ← Standalone MCX peak margin downloader (test tool)
│
├── sa_codes\                   ← Short Allocation scripts (run as subprocesses)
│   ├── newshortallocationfiledownloader.py    ← Code-1: downloads SA + MCX peak files
│   ├── shortallocation_final_new_with_co.py   ← Code-2: SA processing + emails
│   ├── LASUPTO3.py                             ← Code-3: LAS Funding (runs 1/2/3)
│   ├── LAS4.py                                 ← Code-4: LAS Funding (run 4)
│   ├── _checkpoint.py                          ← Step checkpoint tracker (resume system)
│   ├── ncdex_downloader.py                     ← NCDEX 5-file downloader (AES+CAPTCHA)
│   └── SHORT & LAS.bat                         ← Original batch (reference only)
│
├── data\
│   └── circulars\
│       ├── pipeline_knowledge.txt  ← Agent's deep knowledge (edit to make agent smarter)
│       ├── collateral_haircut.txt
│       ├── mtf_concentration.txt
│       └── peak_margin_penalty.txt
│
├── logs\
│   ├── agent_log.jsonl             ← All agent Q&A (Audit Log tab)
│   ├── last_error.json             ← Last pipeline failure traceback
│   └── short_allocation\           ← Per-run SA logs + upload logs
│
└── .phase0_bak\                ← Manual file backups from June 20 session
```

### Architecture Flow

```
BROWSER (http://localhost:8000)
    │
    │  HTTP/SSE to port 8000
    ▼
api.py  (FastAPI + uvicorn)
    │
    ├──[Pipeline button clicked]──► core/pipelines/*_run.py  (streaming wrapper)
    │                                       │ yields log lines via SSE
    │                                       └──► core/pipelines/*.py  (actual logic)
    │                                               ├── core/notify.py     (WA + email)
    │                                               ├── core/db.py         (history DB)
    │                                               └── core/downloaders/  (exchange I/O)
    │
    ├──[Chat message]──────────────► core/agent_run.py  (streaming)
    │                                       └──► core/agent.py  (LLM loop + tools)
    │                                               ├── core/llm.py        (Groq API)
    │                                               ├── core/rag.py        (circulars)
    │                                               └── core/root_cause.py
    │
    └──[SA button clicked]─────────► core/short_allocation.py
                                            └──► sa_codes/*.py  (as subprocesses)

OUTPUT FOLDERS (on Windows disk, configured in .env):
    C:\SHORT ALLOCATION          ← SA files (input + output)
    C:\LAS FUNDING               ← LAS files
    C:\Peak Margin               ← Peak margin snapshots + images
    D:\CTCL VS NMASS             ← CTCL output files
    D:\CONCENTRATION             ← Concentration output
    D:\15 minutes                ← Margin shortage files
    D:\MTF ORDER FILE            ← MTF order files
    C:\CNC_F11                   ← CNC F11 output
    D:\PAYOUT ASHISH             ← Payout files
    D:\INSPECTION                ← Payout inspection output
    D:\MARGIN TOP20              ← Top-20 margin report
    D:\70 FILES DOWNLOADER       ← 70 exchange files
    C:\FTP FILES                 ← FTP downloaded files
```

---

## HOW TO USE THIS GUIDE
- **"I want to change something on the frontend"** → go to Section 4 (Dashboard UI Map)
- **"I want to add/edit a pipeline"** → go to Section 5 (Pipeline Reference)
- **"Something is broken"** → go to Section 7 (Troubleshooting)
- **"I want to roll back changes"** → go to Section 9 (Git Backup System)
- **"I want to add PostgreSQL"** → go to Section 8 (Database Guide)

---

## SECTION 1 — WHAT IS THIS PROJECT?

RMS Copilot is an internal tool for the Risk Management System (RMS) team at Mastertrust. It:

- **Runs compliance pipelines** (CTCL, Peak Margin, Concentration, Short Allocation, etc.) from a web UI instead of running scripts manually
- **Streams live logs** to the browser so you can see exactly what is happening
- **Sends reports** via WhatsApp and email automatically or after human approval
- **Has an AI agent** (RMS Agent) you can chat with to ask questions about data, rules, and errors
- **Stores history** in a database so you can look back at past shortfalls and trends

**Stack:** Python + FastAPI (backend) · Single HTML file (frontend) · Groq LLM · SQLite/PostgreSQL (DB)

---

## SECTION 2 — HOW A REQUEST FLOWS (THE BIG PICTURE)

```
You click a button in the browser (dashboard.html)
        │
        │  HTTP request (or SSE stream)
        ▼
  api.py  (FastAPI backend, port 8000)
        │
        ├─► Pipeline runner  (core/pipelines/*_run.py)
        │       │  yields live log lines back to browser
        │       └─► Pipeline logic  (core/pipelines/*.py)
        │               └─► core/downloaders/  (exchange login/download)
        │               └─► core/notify.py     (WhatsApp + email)
        │               └─► core/db.py         (save results to DB)
        │
        └─► AI Agent  (core/agent.py → core/agent_run.py)
                └─► LLM  (core/llm.py → Groq API)
                └─► RAG  (core/rag.py → data/circulars/*.txt)
                └─► Tools (can READ files, RUN pipelines, QUERY DB)
```

**Config & secrets:** Everything lives in `.env`. The code reads it via `core/config.py`. Never hardcode credentials anywhere else.

---

## SECTION 3 — FILE-BY-FILE REFERENCE

### Root Level

| File | What it does | When to edit |
|------|-------------|--------------|
| `api.py` | FastAPI backend. ALL endpoints live here. Maps UI actions to pipeline/agent functions. | When adding a new API endpoint or a new pipeline button in the UI |
| `dashboard.html` | The ENTIRE frontend in one file (HTML + CSS + JS). No build step. | When changing UI layout, adding buttons, changing colors, adding new views |
| `.env` | All secrets and settings (API keys, SMTP, FTP, folders, NSE credentials). Never commit this to git. | When adding new settings or credentials |
| `requirements.txt` | Python packages list. Run `pip install -r requirements.txt` to install. | When adding a new Python library |
| `run_peak_margin.py` | Local test script — runs the peak-margin pipeline end-to-end without sending. | For debugging peak margin only |
| `reset_peak_day.py` | Deletes today's peak-margin run state (report + log + images) so you can restart the day fresh. | If peak margin is stuck or you want to rerun |
| `ask.py` | Quick smoke-test: types a question to the LLM and prints the answer. | Testing if Groq API key works |
| `ask_compliance.py` | Terminal RAG test — ask a compliance question from command line. | Testing if RAG / circulars are loaded |
| `check.py` | Prints all config values loaded from .env (without secrets). Use to confirm settings are right. | Diagnosing config problems |
| `check_nse.py` | Tests NSE extranet login. | Diagnosing NSE login failures |
| `CODE_MAP.txt` | Older code reference (now superseded by this file). | Read-only reference |
| `rms_history.db` | SQLite history database (auto-created). | Never edit manually — use `python core/db.py summary` |
| `rms-copilot.zip` | Archive zip of the project. | Ignore |

---

### core/ — THE BRAIN

#### Core Infrastructure

| File | What it does | When to edit |
|------|-------------|--------------|
| `core/config.py` | **The ONLY place secrets are read from .env.** Exports: GROQ_API_KEY, SMTP settings, FTP settings, WhatsApp settings, exchange login details, folder paths. | Add new `.env` variable → add it here |
| `core/llm.py` | **The ONLY file that talks to the LLM (Groq).** Uses `llama-3.3-70b-versatile` as primary, falls back to `llama-3.1-8b-instant` if quota hit. | Change LLM model or switch providers here |
| `core/agent.py` | **The RMS Ops Agent — the biggest file.** Contains: system prompt, tool definitions, FOLDER_MAP (which folder each pipeline writes to), PIPELINE_REGISTRY (structured knowledge of every pipeline), `run_agent_chat()` loop, `_match_known_error()` (instant answers without LLM), `_load_last_run_error()` (attaches real traceback to error questions). | Add new agent tool, change agent behaviour, update system prompt, add new pipeline to FOLDER_MAP |
| `core/agent_run.py` | Thin wrapper: runs the agent in a background thread and yields SSE events to the browser. | Rarely — only if streaming logic changes |
| `core/rag.py` | RAG (Retrieval-Augmented Generation) over `data/circulars/*.txt`. Uses local MiniLM embeddings to find relevant chunks, then passes them to the LLM. This is how the agent answers compliance questions accurately. | Change chunking size, add new document type |
| `core/code_index.py` | Lets the agent search the codebase semantically. Indexes every Python file. Powers the `search_code` and `list_indexed_code` agent tools. | Change which folders get indexed |
| `core/notify.py` | Sends WhatsApp (Tata Business API) and email (Netcore SMTP). Only fires after human approval or AUTO_SEND=true. Returns clear per-channel success/failure. | Change message format, add new recipients, change WhatsApp template |
| `core/compliance.py` | Compliance assistant logic — called by `/ask` endpoint. | Edit compliance question handling |
| `core/penalty.py` | Margin penalty calculator — exposed as an agent tool (`calculate_penalty`). | Change penalty rules |

#### Data/History

| File | What it does | When to edit |
|------|-------------|--------------|
| `core/db.py` | **SQLite history database.** Tables: `pipeline_runs`, `ctcl_snapshots`, `margin_snapshots`, `run_files`. Records every pipeline run. Lets you query: "which clients have been in shortfall 3+ days?". Run `python core/db.py summary` to inspect. | Migrating to PostgreSQL (see Section 8) |
| `core/daily_summary.py` | Reads today's real output files (no LLM) and returns a one-glance morning summary: shortfall counts, violation counts, blocked payouts. Powers `/daily-summary` endpoint. | Change what the morning summary shows |
| `core/readiness.py` | Checks which pipelines can run RIGHT NOW based on whether required input files exist. Powers the dashboard readiness tiles. | Add new pipeline readiness check |
| `core/root_cause.py` | "Why is client X in shortfall?" — decomposes deposit vs margin vs shortfall from the real file. Explicitly refuses to guess if it can't see the data. | Change root-cause analysis logic |

#### Pipeline Orchestrators

| File | What it does | When to edit |
|------|-------------|--------------|
| `core/short_allocation.py` | **Short Allocation orchestrator.** Runs `newshortallocationfiledownloader.py` → `shortallocation_final_new_with_co.py` → `LASUPTO3.py` or `LAS4.py` exactly like the batch file did. Supports stop, log streaming, backups, DB archiving. | Change short allocation run logic, add new SA step |
| `core/pipeline_box.py` | **Generic pipeline runner** (for pipelines that aren't yet integrated into the main system). Supports run, stop, logs, config, upload, backups across multiple pipeline types. | Add a new pipeline to the generic box system |

---

### core/pipelines/ — THE 15 PIPELINES

Each pipeline has TWO files:
- `xxx.py` — pure logic (download, compute, write files)
- `xxx_run.py` — thin streaming wrapper (yields live log lines to the browser)

**You almost always edit the `.py` logic file, not the `_run.py` wrapper.**

#### Peak Margin

| File | UI Section | What it does |
|------|-----------|--------------|
| `core/pipelines/peak_margin.py` | Dashboard → Peak Margin tiles (i01/i02/i03/i04) | Logs into NSE, downloads snapshot, computes cumulative column-wise report. 4 intraday snapshots (10:30 / 12:30 / 13:45 / 15:30). |
| `core/pipelines/peak_margin_run.py` | Same | Streaming wrapper. Also handles AUTO_SEND logic. |
| `core/pipelines/report_image.py` | Dashboard → report preview images | Renders the peak-margin report as a PNG image (for WhatsApp/email). No Excel needed. |
| `core/pipelines/reset_peak_day.py` | Dashboard → Reset button | Clears today's peak state so you can rerun from scratch. |

**To change which codes are used for CM/FO → edit `.env`: `PEAK_FO_CODES`, `PEAK_CM_CODES`**

#### Margin Shortage / CTCL

| File | UI Section | What it does |
|------|-----------|--------------|
| `core/pipelines/margin_shortage.py` | — RETIRED — | Was the old standalone download-only tile (its own EasyOCR-based NSCCL+NCDEX downloader). Now a stub that raises if called. |
| `core/pipelines/margin_shortage_15min.py` | Margin Shortage (15-min) box, own 9-tile "Run" | Full pipeline: merges CM/FO/CO/NCDEX/MCX 90%, computes DEPOSIT NMASS/TOTAL MARGIN/DIFFERENCE(F-G)/MARGIN WARNING %, sends main + zone emails, sends Gupshup WhatsApp alerts (>105%, ±10% swing), backs up. Ported from `15minutesfinal_whatsapp.PY`, runs 9x/day. See ARCHITECTURE.md for the full run sequence and MCX-90-sharing pairing table. |
| `core/pipelines/nmass_download.py` / `mcx90_download.py` | Margin Shortage box's own Download buttons | Same shared download scripts CTCL uses, called with `pipe="ms15"` so files land in `MARGIN_SHORTAGE_FOLDER`. MCX 90% auto-shares with CTCL's box at 3 confirmed paired run times. |
| `core/pipelines/ctcl_vs_nmass.py` | Dashboard → CTCL tile | Core CTCL logic: NMASS vs CTCL diff, flags SHORTFALL/OK/VIRTUAL ID, zone emails, TOP20, payout check, backup. |
| `core/pipelines/ctcl_vs_nmass_run.py` | Same | Streaming wrapper (legacy path — the modern 5-tile CTCL box actually runs through `core/pipeline_box.py`'s generic `PIPELINES["ctcl"]`). |
| `core/pipelines/margin_top20.py` | (no separate tile — runs as part of CTCL) | Top-20 margin utilisation report. |

**IMPORTANT — CTCL sign vs Margin sign:**
- CTCL shortfall = `DIFFERENCE(G-F)` is POSITIVE (CTCL > NMASS)
- Margin shortfall = `DIFFERENCE(F-G)` is NEGATIVE (margin > deposit)
- These are OPPOSITE signs. Never mix them up in queries.

#### Concentration

| File | UI Section | What it does |
|------|-----------|--------------|
| `core/pipelines/concentration.py` | Dashboard → Concentration tile | Index-bucket + scrip-concentration violation check. Downloads INDEX files + KORP shortage. |
| `core/pipelines/concentration_run.py` | Same | Two separate streams: download step, then compute step. |

#### CNC / MTF / CODE2

| File | UI Section | What it does |
|------|-----------|--------------|
| `core/pipelines/cnc_f11.py` | Dashboard → CNC F11 tile | PositionBook buy/sell match → writes Sheet4 → sends zone emails. |
| `core/pipelines/cnc_f11_run.py` | Same | Streaming wrapper. |
| `core/pipelines/mtf_order.py` | Dashboard → MTF Order tile | Margin match → generates KAMB + O319 order files → zone emails → backup. |
| `core/pipelines/mtf_order_run.py` | Same | Streaming wrapper. |
| `core/pipelines/code2_shortage.py` | (manual trigger) | Tuesday/Thursday CODE2 shortage report. |

#### Payout Inspection

| File | UI Section | What it does |
|------|-----------|--------------|
| `core/pipelines/payout_download.py` | Compliance → Payout section | Downloads payment-request log from BOAPI/KORP. |
| `core/pipelines/payout_check.py` | Same | Flags payout requestors with negative margin. |
| `core/pipelines/payout_inspection.py` | Compliance → Daily / Client tabs | Daily ingestion of payout data + per-client historical inspection. |
| `core/pipelines/payout_inspection_run.py` | Same | Streaming wrapper (two streams: daily + client). |

#### File Downloads

| File | UI Section | What it does |
|------|-----------|--------------|
| `core/pipelines/ftp_download.py` | Downloads → FTP tile | Downloads KAMB/O319/NOREN/SYMF files via FTP → distributes to 8 destination folders. |
| `core/pipelines/ftp_download_run.py` | Same | Streaming wrapper. |
| `core/pipelines/files_downloader_70.py` | Downloads → 70 Files tile | "70 Files": NSE (CM/FO/CO/CD) + MCX + NCDEX + EPI exchange files. |
| `core/pipelines/files_downloader_70_run.py` | Same | Streaming wrapper. Saves real traceback to `logs/last_error.json` on failure. |

---

### core/downloaders/ — EXCHANGE I/O LAYER

| File | What it does | When to edit |
|------|-------------|--------------|
| `core/downloaders/nse.py` | Login to NSE extranet, handle captcha, download Peak Margin i01-i04 snapshot files. The ONLY code that talks to NSE. | NSE login changes, captcha handling changes |

---

### sa_codes/ — SHORT ALLOCATION SCRIPTS

These are the original standalone scripts that `core/short_allocation.py` runs as subprocesses. They are NOT changed by the main system — they run exactly as they did before when called from the batch file.

| File | What it does |
|------|-------------|
| `newshortallocationfiledownloader.py` | Code-1: Downloads short allocation files. Also writes `run_state.txt`. |
| `shortallocation_final_new_with_co.py` | Code-2: Core SA processing — always runs after Code-1 succeeds. |
| `LASUPTO3.py` | Code-3: LAS Funding (runs 1, 2, 3). |
| `LAS4.py` | Code-4: LAS Funding (run 4 only). |
| `SHORT & LAS.bat` | The original batch file (kept for reference). The dashboard exactly reproduces its logic. |

---

### data/ — KNOWLEDGE BASE

| Path | What it does | When to edit |
|------|-------------|--------------|
| `data/circulars/pipeline_knowledge.txt` | **The agent's deep knowledge.** Per-pipeline logic, "why" explanations, AND the error reference for the known-error matcher. **Edit this to make the agent smarter — no code change needed.** | Add new error/question/answer pairs. Add pipeline documentation. |
| `data/circulars/collateral_haircut.txt` | NSCCL collateral haircut circular (for RAG). | Update when new circular is issued |
| `data/circulars/mtf_concentration.txt` | MTF concentration rules (for RAG). | Update when rules change |
| `data/circulars/peak_margin_penalty.txt` | Peak margin penalty rules (for RAG). | Update when penalty rules change |

---

### logs/

| Path | What it does |
|------|-------------|
| `logs/agent_log.jsonl` | Every agent Q&A appended here. The Audit Log tab reads this. |
| `logs/last_error.json` | The last failed pipeline's real traceback. The agent attaches this when you ask about an error. |
| `logs/short_allocation/` | Per-run logs for the short allocation pipeline. |

---

## SECTION 4 — DASHBOARD UI MAP

**File: `dashboard.html`** — everything is in this one file.
- **CSS** → the `<style>` block at the top (look for `:root { --bg: ... }` for colors)
- **HTML structure** → the `<div class="shell">` block
- **JavaScript** → the `<script>` block at the bottom

### Sidebar Navigation

The sidebar has these nav items. Each calls `showView('viewName')` in JS.

```
Dashboard   → showView('home')
Downloads   → showView('downloads')
Compliance  → showView('compliance')
Short Alloc → showView('short')
Agents      → showView('agent')
Data Expl.  → showView('data')
Audit Log   → showView('audit')
```

**To add a new sidebar item:** Add a `<div class="nav" onclick="showView('myview')">` in the sidebar HTML, then add a `<div id="myview" class="view" ...>` section in the main area.

### Dashboard View (home)

| What you see | What code drives it | API endpoint |
|-------------|--------------------|----|
| Peak Margin tiles (i01/i02/i03/i04) | `runPeakMargin(idx)` in JS | `GET /peak-margin-stream/{idx}` |
| Report images (F_filtered / C_filtered) | `loadReportImages()` | `GET /report-image/{name}` |
| Approve button | `approveSend(token)` | `POST /approve-send` |
| Readiness tiles (Input Files Ready) | `loadReadiness()` | `GET /folder-readiness` |
| Concentration tile | `runConcentration()` | `GET /concentration-download-stream` then `/concentration-compute-stream` |
| CTCL vs NMASS tile | `runCTCL()` | `GET /ctcl-vs-nmass-stream` |
| CNC F11 tile | `runCNCF11()` | `GET /cnc-f11-stream` |
| MTF Order tile | `runMTFOrder()` | `GET /mtf-order-stream` |

### Downloads View

| What you see | API endpoint |
|-------------|-------------|
| Margin Shortage box — NMASS/NCDEX download | `GET /ms15/download/nmass-stream` |
| Margin Shortage box — MCX 90% download | `GET /ms15/download/mcx90-stream` |
| Margin Shortage box — Run (9 tiles) | `GET /px/run-stream?pipe=ms15` |
| FTP Download tile | `GET /ftp-download-stream` |
| 70 Files tile | `GET /files-downloader-70-stream` |

### Compliance View (payout inspection)

| What you see | API endpoint |
|-------------|-------------|
| Daily Ingestion button | `GET /payout-inspection-daily-stream` |
| Client Inspection form | `GET /payout-inspection-client-stream?clients=X&fy=Y` |

### Short Allocation View

| What you see | API endpoint |
|-------------|-------------|
| Run 01/02/03/04 buttons | `GET /sa/run-stream?run=01` |
| Status display | `GET /sa/status` |
| Readiness check | `GET /sa/readiness` |
| Upload files | `POST /sa/upload` |
| Backup list/restore | `GET /sa/backup/list` · `POST /sa/backup/copy` |
| Config settings | `GET /sa/config` · `POST /sa/config` |
| Logs | `GET /sa/logs` · `GET /sa/log` |

### Agents View (Chat UI)

| What you see | API endpoint |
|-------------|-------------|
| Chat messages | `POST /agent-chat-stream` |
| Tool activity badges | Streamed as SSE events: `tool_start` / `tool_done` |
| File upload for context | `POST /upload-file` |
| Source panel (right side) | Populated from agent's `lookup_rule` tool calls |

**To change how the chat looks:** Find `renderMessage()` in the `<script>` block.
**To change what the agent can do:** Edit `core/agent.py` — specifically the `TOOLS` list and the tool execution code.

### Data Explorer View

| What you see | API endpoint |
|-------------|-------------|
| Pipeline dropdown | Drives `pipeline` param |
| Plain-English question → | `GET /ask-data?pipeline=X&question=Y` |
| Manual query form → | `GET /query-file?pipeline=X&group_by=Y&...` |

### Audit Log View

- Reads from `GET /agent-log` endpoint
- Which reads `logs/agent_log.jsonl`

### Live Console (bottom bar, always visible)

Every pipeline streams to this. It's driven by a shared `startStream(url)` function in JS that connects to any SSE endpoint and appends log lines.

**"Ask agent about this error"** button appears when a stream ends with an `error` event.

---

## SECTION 5 — PIPELINE QUICK REFERENCE

### Pipeline Input Folders (configured in .env)

```
PEAK_FOLDER          = C:\Peak Margin
CONCENTRATION_FOLDER = D:\CONCENTRATION
CTCL_NMASS_FOLDER    = D:\CTCL VS NMASS
CNC_F11_FOLDER       = C:\CNC_F11
MTF_ORDER_FOLDER     = D:\MTF ORDER FILE
FTP_BASE_LOCAL       = C:\FTP FILES
MARGIN_SHORTAGE_FOLDER = D:\15 minutes
MARGIN_TOP20_FOLDER  = D:\MARGIN TOP20
PAYOUT_FOLDER        = D:\PAYOUT ASHISH
```

### Adding a New Pipeline (step-by-step)

1. Create `core/pipelines/mypipeline.py` — write the logic, return a status dict
2. Create `core/pipelines/mypipeline_run.py` — wrap it in a `stream_mypipeline()` generator that yields `{"type":"log","msg":"..."}` events
3. In `api.py`: add a `GET /mypipeline-stream` endpoint that calls `stream_mypipeline()`
4. In `dashboard.html`: add a tile with a button that calls `startStream('/mypipeline-stream')`
5. In `core/agent.py`: add `mypipeline` to `FOLDER_MAP` and add a `run_mypipeline` tool to the `TOOLS` list
6. In `data/circulars/pipeline_knowledge.txt`: add documentation for the new pipeline

---

## SECTION 6 — CONFIGURATION (.env) REFERENCE

```
# ── AI ───────────────────────────────────────────────────────
GROQ_API_KEY=your_groq_key           # LLM API key

# ── Email ────────────────────────────────────────────────────
SMTP_SERVER=smtp3.netcore.co.in
SMTP_PORT=587
SENDER_EMAIL=kartavya.dhillon@mastertrust.co.in
SMTP_PASSWORD=your_password

# ── WhatsApp (Tata) ──────────────────────────────────────────
WA_TATA_API_KEY=...
WA_TATA_FROM=...

# ── FTP ──────────────────────────────────────────────────────
FTP_HOST=...
FTP_USER=...
FTP_PASS=...

# ── NSE ──────────────────────────────────────────────────────
NSE_MEMBER_CODE=06436
NSE_LOGIN_ID=M0213815
NSE_PASSWORD=Yxs2Hg1/h0KUKKoF5SqEGQ==

# ── Peak Margin ──────────────────────────────────────────────
PEAK_FO_CODES=JDA563,JDA937,...      # FO codes to process
PEAK_CM_CODES=06436,PRO_06436,6436  # CM codes to process
PEAK_FOLDER=C:\Peak Margin

# ── Auto Send ────────────────────────────────────────────────
AUTO_SEND=false                      # true = send automatically, false = wait for approval

# ── Short Allocation ─────────────────────────────────────────
SA_CODES_DIR=D:\CLAUDE\rms-copilot\sa_codes
SA_STATE_FILE=C:\KARTAVYA\SHORT ALLOCATION\run_state.txt
SA_OUTPUT_DIR=C:\KARTAVYA\SHORT ALLOCATION
SA_LAS_FUNDING_DIR=C:\KARTAVYA\LAS FUNDING
SA_PYTHON=python

# ── PostgreSQL (NEW — see Section 8) ─────────────────────────
PG_HOST=localhost
PG_PORT=5432
PG_DB=rms_copilot
PG_USER=postgres
PG_PASSWORD=your_postgres_password
USE_POSTGRES=true
```

---

## SECTION 7 — TROUBLESHOOTING

### "NoneType object is not subscriptable" in 70 Files
- The NSE login page returned an unexpected layout. The page scraping failed.
- Check NSE extranet is accessible. Check `NSE_LOGIN_ID` / `NSE_PASSWORD` in .env.
- The agent knows this error — ask "why did 70 files fail?"

### "Rate limit" from Groq
- The primary model (llama-3.3-70b) hit its daily free-tier limit.
- `core/llm.py` auto-falls back to `llama-3.1-8b-instant`. 
- If both are exhausted, wait until midnight UTC (Groq resets daily).

### Agent gives wrong/guessed answer
- Check `data/circulars/pipeline_knowledge.txt` — add the correct answer there.
- The agent will find it via the known-error matcher (instant, no LLM, no hallucination).

### Pipeline shows "file not found" in readiness tiles
- Check that the required input file exists in the expected folder.
- Check `CTCL_NMASS_FOLDER`, `CONCENTRATION_FOLDER` etc. in `.env` point to the right paths.
- Use `check.py` to print all configured paths.

### Short Allocation stuck on "Running"
- Press the Stop button in the dashboard.
- If it doesn't respond, restart the backend (`uvicorn api:app --reload`).

### Database / DB errors
- Run `python core/db.py summary` from the project folder to check the DB is accessible.
- If using PostgreSQL: check `PG_HOST`, `PG_USER`, `PG_PASSWORD` in .env. Check Postgres service is running.

---

## SECTION 8 — DATABASE GUIDE

### Current Setup (SQLite)
- File: `rms_history.db` in the project root
- Zero setup, single file, auto-created on first run
- Good for one-machine use

### New Setup (PostgreSQL) — see `core/db_postgres.py`
The project now supports PostgreSQL. When `USE_POSTGRES=true` in `.env`, all pipeline history, backups, and file archives go to Postgres instead of SQLite.

**Why Postgres over SQLite for backups/archives:**
- Multiple machines can read/write simultaneously (SQLite breaks with concurrent writes)
- Files archived in `run_files` table are accessible from ANY machine that hits this server
- Better for large binary file storage (SQLite starts getting slow above ~100MB)
- Proper user permissions and audit trail

**Tables in Postgres (same schema as SQLite):**

```sql
pipeline_runs      — one row per pipeline execution (summary metrics)
ctcl_snapshots     — per-client CTCL data at each run (for trend queries)
margin_snapshots   — per-client 15-min margin data at each run
run_files          — actual file bytes (logs, outputs, backups) for all pipelines
```

**To switch to Postgres:**
1. Install psycopg2: `pip install psycopg2-binary`
2. Add Postgres settings to `.env` (see Section 6)
3. Run the migration: `python core/db_postgres.py setup`
4. Set `USE_POSTGRES=true` in `.env`
5. Restart the backend

**To inspect the Postgres DB directly:**
```
python core/db_postgres.py summary
python core/db_postgres.py history ctcl 7
python core/db_postgres.py trend FIXTAR0001
```

---

## SECTION 9 — GIT BACKUP SYSTEM

### How Backups Work

Every time we make significant changes, we commit to git. This gives you a full history of every change and the ability to roll back any file to any point in time.

### Git History (commits so far)

```
6a1cebe  Initial RMS Copilot                    ← original baseline
d4583b7  Snapshot 2026-06-26 — pre-postgres      ← all changes up to today
```

### How to Take a Backup (before making changes)

```bash
cd D:\CLAUDE\rms-copilot
git add -A
git commit -m "Backup before [describe what you are about to do]"
```

### How to Roll Back One File

```bash
# Undo changes to one file (back to last commit):
git checkout HEAD -- core/db.py

# Undo to a specific old commit (find commit hash with git log):
git checkout 6a1cebe -- dashboard.html
```

### How to Roll Back EVERYTHING to a Previous State

```bash
# DANGER: discards all uncommitted changes
git reset --hard HEAD           # back to last commit
git reset --hard 6a1cebe       # back to a specific commit
```

### How to See What Changed

```bash
git status                      # which files are modified
git diff                        # see exact line-by-line changes
git log --oneline               # list all commits
git show 6a1cebe                # see what was in a specific commit
```

### .phase0_bak/ — Manual Backups

The `.phase0_bak/` folder has file-level manual backups from the June 20 session. These are redundant now that git is being used, but kept for reference.

### What is NOT in git (intentionally)

- `.env` — secrets, never commit
- `rms_history.db` — database file, too large and changes every run
- `logs/` — log files, auto-generated
- `__pycache__/` — Python cache, auto-generated

---

## SECTION 10 — HOW TO START THE SYSTEM

```bash
# 1. Go to project folder
cd D:\CLAUDE\rms-copilot

# 2. (First time only) Install dependencies
pip install -r requirements.txt

# 3. Start the backend
uvicorn api:app --reload

# 4. Open the dashboard
# Go to: http://localhost:8000
# (or http://<this-machine-IP>:8000 from another computer on LAN)

# 5. Run directly (production / shared server mode)
python api.py    # binds to 0.0.0.0:8000 — everyone on LAN can access
```

### Running with PostgreSQL
```bash
# Make sure Postgres is running (Windows Services → postgresql)
# Check .env has PG_HOST, PG_USER, PG_PASSWORD, USE_POSTGRES=true
uvicorn api:app --reload
```

---

## SECTION 11 — CHANGE LOG

| Date | What Changed | Files |
|------|-------------|-------|
| 2026-06-20 | Phase 0 reliability: guarded NSE login scraping, real traceback on errors, agent error-diagnosis, dashboard restyle | `files_downloader_70.py`, `files_downloader_70_run.py`, `agent.py`, `pipeline_knowledge.txt`, `dashboard.html` |
| 2026-06-20 | New features: `core/db.py` (SQLite history), `core/daily_summary.py`, `core/readiness.py`, `core/root_cause.py`, `core/short_allocation.py`, `core/pipeline_box.py` | Multiple |
| 2026-06-26 | Git snapshot commit (all above changes committed) | All modified files |
| 2026-06-26 | Added PostgreSQL support: `core/db_postgres.py` | New file |
| 2026-06-26 | Created this guide | `PROJECT_GUIDE.md` |
| 2026-06-26 | Upload fix: split body-read from disk-write timeout (was causing "Failed to fetch") | `api.py` |
| 2026-06-26 | Added SA2_SMTP_USER/PASS to .env (SA Processing email was hardcoded in script) | `.env` |
| 2026-06-26 | Dashboard fixes: readiness bar (only missing, separate rows), WA masking (dots, no eye), upload auto-close, file:// large-file warning | `dashboard.html` |
| 2026-06-26 | Added Section 0 with full folder tree and architecture diagram | `PROJECT_GUIDE.md` |
| 2026-07-02 | MCX Peak Margin: replaced Selenium-based download with `requests` session (login→CSRF→download). SA-01 downloads peak_01, SA-02 downloads peak_02, etc. | `sa_codes/newshortallocationfiledownloader.py`, `mcx_peak_margin_downloader.py` (standalone test) |
| 2026-07-02 | LAS image generator: replaced xlwings+Excel screenshot (6 blocks in LAS4, 7 in LASUPTO3) with Pillow-based `xlsx_to_image()`. No Excel dependency. Sizing doubles everything to match Excel's `ScaleWidth(2,0)` export. | `las_image_generator.py` (new), `sa_codes/LAS4.py`, `sa_codes/LASUPTO3.py` |
| 2026-07-02 | Checkpoint/resume system: `_checkpoint.py` writes step JSON before each major LAS/SA section. Dashboard polls `/sa/las-checkpoint` every 8s and shows banner if status is "running". Resume calls `/sa/las-rerun` with `LAS_RESUME_FROM` env var. | `sa_codes/_checkpoint.py` (new), `sa_codes/LAS4.py`, `sa_codes/LASUPTO3.py`, `api.py`, `dashboard.html` |
| 2026-07-02 | NSE credential fix: loginId `"RMS"` was blocked; updated to `"M0213815"` with new encrypted password in all code defaults and `.env`. | `sa_codes/newshortallocationfiledownloader.py`, `core/pipelines/files_downloader_70.py`, `core/downloaders/nse.py`, `.env` |
| 2026-07-02 | NCDEX import fix: added fallback sys.path so `ncdex_downloader` import works from any working directory (not just `sa_codes/`). | `sa_codes/newshortallocationfiledownloader.py` |

---

*This file is the source of truth. Update the "Change Log" section every time something significant is changed.*
