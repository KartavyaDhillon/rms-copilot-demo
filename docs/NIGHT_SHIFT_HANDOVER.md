# Night-shift handover — how the tracker gets from the console to the batch machine

## The short version

The **database is and stays the real tracker.** Nothing about that changed. Every
WhatsApp alert the console sends still writes a row into the `margin_alert_tracker`
table, exactly as before, and every run still reads its dedup decisions from there.

The JSON file is a **copy on the way out** — a translation of that table into the
format the old batch script understands, so the night-shift machine can carry on
where the day shift left off. If the JSON export failed tomorrow, the console
would carry on working perfectly; only the handover would break.

```
   CONSOLE (day shift, 09:30 → 16:50)          NIGHT MACHINE (after 16:50)
   ┌────────────────────────────────┐          ┌─────────────────────────┐
   │  margin_alert_tracker  (DB)    │          │  batch script           │
   │        ▲                       │          │        ▲                │
   │        │ every alert written   │          │        │ reads          │
   │        │ here — the source of  │          │        │                │
   │        │ truth                 │          │        │                │
   │        │                       │          │        │                │
   │        └──► exported after ────┼──────────┼──► margin_alert_        │
   │             every run          │  copy    │    tracker.json         │
   └────────────────────────────────┘          └─────────────────────────┘
```

Today that copy is a file you move by hand. The rest of this document explains the
option that removes the "by hand" part, in case you want it later.

---

## Right now: the manual way

After the 16:50 run finishes, the console has written a fresh file to:

```
D:\15 minutes\margin_alert_tracker.json
```

Copy that file onto the night machine, into the folder its batch script runs from.
That's it — the script picks it up and knows exactly who the day shift already
alerted.

**The one thing to watch:** copy it *after* the 16:50 run, not before. The file is
rewritten after every run, so copying it at 15:00 gives the night operator a file
that's missing the 15:15, 16:30 and 16:50 alerts, and those clients get messaged
twice.

---

## What a "shared network folder" actually is

Think of the office network as a corridor, and each computer as a room with a
locked door. Normally nothing gets in or out. **Sharing a folder is putting a
hatch in one door** and saying "anyone in the corridor may reach into *this one
folder* — nothing else in the room."

Once that hatch exists, other computers can open that folder as if it were their
own. Same thing you'd do with `D:\` or `C:\Users\...`, just pointing somewhere
across the network:

```
\\NIGHTPC\rms-handover\margin_alert_tracker.json
  └──┬───┘ └────┬─────┘ └──────────┬───────────┘
     │          │                  └─ the file
     │          └─ the share name (the label on the hatch)
     └─ the name of the computer hosting it
```

That `\\computer\share\file` form is called a **UNC path**. Windows treats it just
like a normal path — programs open it, save to it, and mostly don't need to know
it's on another machine.

Optionally you can **map it to a drive letter** so it looks like `Z:\` in File
Explorer. That's purely cosmetic; `Z:\file.json` and `\\NIGHTPC\rms-handover\file.json`
are the same place.

---

## The catch for *your* setup — and what to do about it

The obvious idea is "share a folder on my laptop and let the night machine read
it." **That won't work here**, and it's worth understanding why before anyone
spends an afternoon on it.

A shared folder only exists while the computer hosting it is **powered on and on
the network**. Your laptop leaves with you at 16:50. Ten minutes later the night
operator opens the share and gets "network path not found" — the room is gone.

So the share has to live on a machine that **stays on**. In your case that's
almost certainly:

**Option A — host the share on the NIGHT machine.** It runs all night anyway. The
console writes the JSON *onto* it during the day; by the time you leave, the file
is already sitting there. Nothing to copy, and nothing depends on your laptop
still being around.

**Option B — host it on a server or NAS**, if your IT team has one. Cleanest
long-term, but needs IT involvement.

**Option C — carry on copying by hand.** Completely fine. It's one file, once a
day. Only worth changing if someone forgets.

Option A is the one I'd suggest, because it needs no new hardware and no IT
ticket — just a folder on a machine that's already running.

---

## Setting up Option A (on the night machine, once)

1. Make a folder, e.g. `C:\rms-handover`.
2. Right-click it → **Properties** → **Sharing** tab → **Share…**
3. Add the Windows user account your laptop logs in as, and give it
   **Read/Write** (the console needs to *write* the file).
4. Note the path Windows shows you — something like `\\NIGHTPC\rms-handover`.
5. On your laptop, open `\\NIGHTPC\rms-handover` in File Explorer once, to confirm
   it opens and you can create a file there.
6. In the console's `.env`, change one line:

   ```
   MS15_TRACKER_JSON=\\NIGHTPC\rms-handover\margin_alert_tracker.json
   ```

7. Restart the console. From the next run onward the file writes itself straight
   onto the night machine.
8. Point the night machine's batch script at `C:\rms-handover\margin_alert_tracker.json`
   (from its own side it's just a local folder).

The export writes to a temporary file and then renames it, so the night script can
never catch a half-written file mid-copy — even if it happens to read at the exact
moment the console is writing.

**If the share is unreachable** (network blip, machine rebooting), the console
prints a warning and carries on. The run still completes, emails and alerts still
go out, the database is still correct — only that one export is skipped. Copy the
local file by hand that day.

---

## When the console gets properly deployed

All of this is a workaround for one specific situation: the console lives on a
laptop that goes home. Once it's deployed to a server that stays on, better
options open up — the night script could read the database directly, or fetch the
tracker from the console over HTTP, and the file copy disappears entirely.

Worth revisiting then. Until that day, the JSON copy is the pragmatic answer.

---

## Quick reference

| | |
|---|---|
| Real tracker | `margin_alert_tracker` table in the database — unchanged, always authoritative |
| Handover copy | `margin_alert_tracker.json`, rewritten after **every** run |
| Where it's written | whatever `MS15_TRACKER_JSON` in `.env` points to |
| Default location | `D:\15 minutes\margin_alert_tracker.json` |
| Format | `{"YYYY-MM-DD": {"CLIENTCODE": last_pct}}` — what the old script already reads |
| Safe test runs | do **not** export (a test doesn't write the tracker, so its export would lie) |
| If the export fails | warning printed, run continues normally, database unaffected |
