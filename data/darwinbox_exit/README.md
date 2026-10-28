# data/darwinbox_exit - the Darwinbox exit-clearance mailer's own folder

Scheduler process: **DARWINBOX EXIT CLEARANCE** (code: `core/scheduler/darwin_lib.py`,
`core/scheduler/processes/darwinbox_exit_clearance.py`). Schedule it twice a day (10:00 and 15:00).

| File | What it is | In git? |
|---|---|---|
| `darwinbox_sent_records.json` | **The memory**: every employee e-mailed, the replies, who was submitted. It is why nothing is ever sent twice. Never delete or edit it. | yes |
| `.darwinbox_session.json` | Saved Darwinbox login cookies. Treat like a password (keep the repository PRIVATE). If it is rejected the script logs in again by itself. | yes |
| `darwinbox_device.json` | Browser fingerprint captured once (needed for the login). | yes |
| `Darwin_Act_Preview.json` | What the Act step built / submitted last. | yes |
| `Employee_Resign.xlsx`, `Darwin_Act_Preview.xlsx`, `*.log` | Rebuilt automatically from the memory. | no (ignored) |
| `reference/` | One-time recordings of the Darwinbox login page / calls used to build the script. Not used at run time. | yes |

Setting it up on a new machine
1. `git pull`.
2. Scheduler > Processes > DARWINBOX EXIT CLEARANCE > **Credentials**: enter the Darwinbox user name / password, the second
   mailbox (SMTP + POP3, sandeep.kumar2) and the WhatsApp token. They are stored in `.env.local`, never in git.
3. Add the two schedules (10:00 and 15:00, daily).
4. Run it once by hand and read the log. It must say "already sent earlier - skipped" for the old employees.

Rules
- Run the schedule on ONE machine only. Two machines would each keep their own memory file and could send the same
  employee twice.
- If `darwinbox_sent_records.json` is missing the run refuses to start (a missing file would re-send every old mail).
  Restore it from git / a backup. For a brand-new start only: set `DARWIN_ALLOW_EMPTY_STATE=1` for one run.
- Turn the **Submit the Act form** setting OFF to test: the form is built and saved to `Darwin_Act_Preview.xlsx`, nothing is
  submitted.
