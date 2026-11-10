# Roadmap — longer-term ideas (not scheduled)

Collected 9 Nov 2026 from discussions with Michele. None of this is being built now; the point is to
avoid choices that close these doors.

## 1. Windows installer (standalone setup)
- One `FermentationManager-Setup.exe` (Inno Setup) built by GitHub Actions on each release: embedded
  Python with all wheels, prebuilt frontend (no Git/Node on the target), WinSW service, firewall rule,
  desktop shortcut that opens the browser.
- First-run setup **inside the app**: reactor names/IPs with "Test connection", controller level/user/
  password (stored in the protected file), data and backup folders, notifications.
- Caveats: unsigned exe → SmartScreen warning (code-signing certificate ~200–400 €/yr); ~200–300 MB;
  Tailscale installed separately; updates = new setup over the old one (data and settings kept);
  must be tested on a clean Windows machine/VM. Estimate: 1–2 work sessions.
- Do it once the software is stable (after the first real fermentations).

## 2. Remote access without Tailscale, users
Three levels, increasing complexity:
1. **Tunnel** (e.g. Cloudflare Tunnel + Access): outbound connection from the lab PC, public HTTPS URL,
   login by email OTP/Google before reaching the app; free (needs a domain). Also enables the
   installable app and native push. The lab PC that writes setpoints becomes reachable from the
   internet → requires level 2 and checking university IT rules.
2. **Users and roles in the app** (viewer / operator / admin), per-user preferences and profiles,
   automatic authorship on measurements and commands. Prerequisite for any use by other people.
3. **Hybrid edge + cloud**: the lab PC is an agent (reads controllers, executes commands, buffers offline)
   and a cloud backend holds data, users, backups and the web app; several labs per account.
   Commands go through a confirmed queue; local control always authoritative. In practice a product
   (hosting, security, GDPR, maintenance).
- Suggested order: level 2 + level 1 for colleagues in the same lab; level 3 only if it goes multi-lab.
- Keep: driver, command-with-read-back logic, analysis and export independent of where data live.

## 3. Up to 8 controllers
- Applikon's web interface handles up to 8 ez2-Control units: support 1–8 reactors.
- Reactors configured in Settings (name, short label like "R1-sx", IP, enabled) instead of the
  service environment; codes/labels no longer hard-coded R1/R2 (today ~60 places in code).
- Home: one card per reactor, compact cards when more than 2–4; phone: reactor list instead of two tabs.
- Parallel runs of 2–8 experiments (today the UI assumes one partner): reactor tabs from the run's
  members, overlay of any member, analysis sessions already handle n experiments.
- Acquisition: 8 controllers polled every 5 s is light (one WebSocket each); check timing with a
  per-controller thread or async polling.

## 4. Reactors managed from Settings (with 3.)
- Add / remove reactors in Settings: name, short label, IP, driver (ez2-Control, simulator), enabled,
  sampling interval (choices 1, 2, 5, 10, 30, 60 s; default 5 s). Applied without restarting the service.
- 1 s is possible (one small WebSocket exchange per value) but with 8 reactors it is ~8×12 queries/s and
  ~86 400 rows per reactor per day: keep 1–2 s for short runs only; 5 s is the sensible default (short base
  doses are still caught by the cumulative counter, which never misses volume).

## 5. Timers and reminders (after native push)
- New → Timer: one-off ("in 45 min") or repeating ("every 1 h from 10:00, 8 times"), optionally tied to an
  experiment ("sample FM-031"). Fired by the service (works with the phone off), sent by push/Telegram/ntfy,
  shown in the app; snooze / done from the notification. Optional link to "add measurement".

## 6. Ownership and visibility (with users and roles, level 2)
- Each experiment, profile, medium, buffer and variable records its creator (already true for experiments
  and profiles) and a visibility: private (creator only) or shared (everyone, creator shown).
- Only meaningful with real roles (viewer / operator / admin) and PIN or password sign-in; otherwise
  "private" would be a filter, not a protection. Cost: a visibility column + a filter in each list/API,
  and rules for references (a shared experiment using a private medium → the medium becomes visible
  read-only). Medium effort; do it together with roles.

## 7. Open Science Framework (OSF)
- OSF has a public REST API (api.osf.io/v2) with personal access tokens; files go through WaterButler
  (files.osf.io/v1/resources/<project>/providers/osfstorage/). Upload, new versions and folders are possible.
- Fits: **export/publishing** — "Send to OSF" on an experiment or analysis session: uploads the XLSX/CSV,
  figures and a README with metadata into a chosen OSF project/component (versioned by OSF).
- Fits less: **routine backups** — private projects are capped at 5 GB (public 50 GB), the database grows
  continuously and private lab data should not be published by mistake. Keep backups on a network share /
  second disk / rclone, use OSF for curated datasets.
- Effort: settings (token, default project) + one upload client + a dialog: about one work session.
- Plan (10 Nov 2026): OSF becomes a second "archive destination" next to the archive folder: same
  End-of-experiment and "Save to archive" actions; optional snapshot of the running experiment every N min
  as a new version of one file. Per-user destinations only with real sign-in (PIN required) and roles.

## 8. Other open items
- Loop on/off from the software (pH, T, stirrer), never manual actuator outputs — see HANDOFF.
- Dark / dimmed theme; consultation mode on the laptop; chart zoom with full-resolution data.
- Licence: GPL-3.0-or-later (chosen 10 Nov 2026; LICENSE file in the repository).
