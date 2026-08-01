# Running RMS Copilot 24×7, and knowing who did what

Two decisions, written up for discussion with the senior developer. They are
related — both are really "this stops being a laptop tool and becomes a system".

---

# PART 1 — Deployment: how it runs 24×7

## The constraint that rules out most options

Before comparing hosting, be clear about what this application actually touches:

- Local Windows paths — `D:\15 minutes`, `C:\COMMON FILES`, `D:\CTCL VS NMASS`
- A PostgreSQL database on the office network
- An internal FTP server
- Exchange portals (NSE/NCDEX/MCX) reached from the office network
- Windows-specific code — `taskkill`, `os.name == "nt"` branches, drive letters

**This means public cloud is effectively off the table.** Not because of cost —
because the files it reads live on your office network. A server in AWS cannot
see `D:\15 minutes`. Making it work would mean a site-to-site VPN, or
restructuring the whole application to stop using file shares. Neither is a
deployment task; both are projects.

There is also a data question: this handles client margin and collateral data.
Putting that on public cloud infrastructure is a compliance conversation, not a
technical one.

So the real question is not "which cloud" but **"which machine in the office
stays on"**.

## The options

### Option A — A VM from your IT team ★ recommended

Most companies of this size already run VMware or Hyper-V. Ask IT for a small
Windows VM: 2 vCPU, 8 GB RAM, 100 GB disk is plenty.

| | |
|---|---|
| Cost | Nothing new — uses infrastructure that already exists |
| Uptime | Best. Lives in the server room: UPS, cooling, proper network |
| Reboots | IT patches it on a known schedule you can align to market hours |
| Downside | Depends on IT's timeline and goodwill. Raise it early |

This is the correct long-term answer. Everything below is a way of not waiting
for it.

### Option B — A spare office PC that never sleeps

Any desktop that can be left on. Free if the hardware exists.

Works, with caveats that matter:

- **Power cuts.** A desk PC without a UPS dies with the power. In a server room
  that's handled; under a desk it isn't. Budget for a small UPS.
- **Somebody switches it off.** Label it. Genuinely — this happens.
- **Windows Update reboots.** Configure active hours so it never restarts
  between 09:00 and 17:00.
- **Sleep is the silent killer.** A sleeping PC looks on but serves nothing.
  Disable sleep, hibernate and "turn off hard disk" in Power Options.

### Option C — The night-shift machine

It already runs 24×7 for the batch script, so the uptime problem is solved. It
also removes the night-shift file handover entirely — same machine, same
database, no copying.

The catch: whoever is on nights is now sitting at the server. If they reboot it
or run something heavy, the console goes with it. Acceptable if the team is
disciplined; a real risk if not.

### Option D — Cloud VM (AWS / Azure / GCP free tier)

**Not suitable, for the reasons above.** Listed only so it can be ruled out with
a reason rather than left as an open question. Free tiers are also 12 months
only and undersized for this.

### Option E — Raspberry Pi or a mini PC

Cheap hardware, but the application is Windows-bound: drive letters, `taskkill`,
OCR libraries, `os.name == "nt"` branches throughout. Porting to Linux is real
work. "Free hardware, expensive effort" — not a shortcut.

---

## Making it actually stay running

Choosing a machine is half the job. Today the console is started by
double-clicking `START_SERVER.bat`, which means it dies when that window closes
or the user logs out. For 24×7 you need three things:

### 1. Run it as a Windows Service — NSSM (free)

[NSSM](https://nssm.cc) wraps any program as a proper Windows service, so it:

- starts automatically at boot, **before anyone logs in**
- restarts itself if it crashes
- survives the user logging out

Roughly:

```
nssm install RMSCopilot "C:\Python\python.exe" "-m uvicorn api:app --host 0.0.0.0 --port 8000"
nssm set RMSCopilot AppDirectory C:\rms-copilot
nssm set RMSCopilot Start SERVICE_AUTO_START
nssm start RMSCopilot
```

The alternative — Task Scheduler with "run at startup, whether user is logged on
or not" — also works and needs no download, but gives you no restart-on-crash.
NSSM is worth the five minutes.

### 2. A fixed address on the network

The console is reached at `http://<server>:8000`. If DHCP changes that address,
every bookmark breaks. Ask IT for a **static IP or a DHCP reservation**, and
ideally a hostname (`http://rms-copilot:8000`) so nobody memorises numbers.

### 3. Know when it dies

A service that stops at 11:00 with nobody noticing until the 11:30 run is worse
than one that never started. Minimum viable monitoring: a scheduled task on
another machine that requests the health endpoint every few minutes and emails
if it fails. The console already has the endpoints for this.

## Cost summary

Genuinely zero in new spend for Options A, B and C — existing hardware, NSSM is
free, PostgreSQL is already running. The only likely purchase is a UPS if you go
with Option B.

---

# PART 2 — Login: real attribution

## Why the alternatives don't survive an audit

| Approach | What it proves |
|---|---|
| Typed name (today) | Nothing. Anyone can type any name |
| Machine IP (already captured) | Which PC. Not which person |
| IP → name map | Which PC, with a nicer label. Same limit |
| Shared password (already used) | That someone knew the shared password. Everyone does |
| **Per-user login** | **That this account's credentials were used** |

Only the last one answers "who ran this". Note the wording though — even a
perfect login proves an *account* was used, not that a particular human was at
the keyboard. That is as good as it gets short of biometrics, and it is the
standard every audit accepts.

## What it involves

Smaller than it sounds, because pieces already exist. The console already has a
password-check helper and password-gated actions; `bcrypt` is already a
dependency. What's missing:

1. A user table — username, password hash (bcrypt), display name, active flag
2. A login page, and a redirect to it when not signed in
3. A signed session cookie, replacing the current `rms_user` free-text one
4. Reading the username from the session instead of the cookie — the audit
   trail, lock strips and log headers already display whatever they're given,
   so this is one function, not a rewrite
5. A small admin screen, or a CLI script, to add and disable users

Realistically a couple of days for 5–10 internal users. The code is not the hard
part.

## The difficulties — the honest list

These are the things that actually cause pain, in rough order of how likely they
are to bite you:

### 1. Lockout during market hours

**The one that matters.** If login breaks at 09:25 and the 09:30 run cannot
start, that is a worse day than any audit gap. Decide in advance:

- A documented **break-glass** path — a script on the server that resets a
  password or creates an emergency account, runnable by someone with server
  access without going through the UI.
- Whether an authentication failure should block runs or fall back to
  "unattributed but allowed". **For an ops console with fixed run windows, I'd
  argue fall back and log loudly.** A missed 09:30 run has immediate financial
  consequence; an unattributed one is a note in a log.

Write this down before switching login on, not during the first incident.

### 2. Somebody has to administer it

New joiners, leavers, forgotten passwords. Today that's you. It's ten minutes a
month until you're on leave and someone's locked out. Name a backup person and
make sure they have server access.

### 3. The team will try to share one login

Because it's convenient. The moment they do, the entire audit value is gone and
you won't notice — the logs will look perfectly healthy, all attributed to one
account. This is a policy problem that no code prevents. Everyone needs to
understand *why* it exists, or it quietly stops working.

### 4. Session length is a real trade-off

Too short and people re-authenticate mid-shift and resent it. Too long and a
walk-away machine stays signed in as them. **12 hours** is a sensible default:
covers one shift, expires by the next.

### 5. Login is not a security boundary on its own

It protects the console's UI. It does not protect the folders, the database, or
the scripts on disk — anyone with file access to that machine can still run them
directly. If the goal is "nobody can run this without being recorded", server
access needs controlling too. Worth stating explicitly so nobody assumes more
protection than exists.

### 6. Passwords must be stored properly

Hashed with bcrypt, never plaintext, never recoverable — only resettable. This
is easy to get right and embarrassing to get wrong. `bcrypt` is already in the
project.

## Suggested sequence

1. **Deploy first.** Login on a laptop that goes home at 16:50 solves nothing.
   Get it onto an always-on machine with a fixed address.
2. **Then add login**, with the break-glass path written down *before* it goes
   live.
3. **Then tighten** — session length, disabling leavers, reviewing the audit
   trail periodically. An audit trail nobody ever reads provides no assurance.

Doing login before deployment is building the lock before the door.
