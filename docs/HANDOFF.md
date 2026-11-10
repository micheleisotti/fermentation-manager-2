# Handoff — context for a new Claude session on the bioreactor PC

Read this first. It summarises the decisions taken while designing and building
Fermentation Manager 2 (Nov 2026), so a new session can continue without re-deciding.

## Who / what
- Owner: Michele Isotti (PhD, DiSAFA UniTo / DeFENS UniMi), works with *L. plantarum*
  Lp187 (WT) and Lp187_B2 (riboflavin overproducer). Answers in Italian; app UI in English.
- Two Applikon **ez2-Control** controllers (R1, R2) on a local Ethernet switch with a
  dedicated Windows 11 PC (Wi-Fi on eduroam). Opening one controller IP in a browser
  shows both. The web UI is not a screen mirror but is sufficient.
- Typical use: start two fermentations in parallel ~18:00, run overnight, monitor and
  change setpoints from phone/home (e.g. lower T early morning).

## State of the code (github.com/micheleisotti/fermentation-manager-2)
- `main`: backend core, React UI (Home, Fermentation, Planning, Profiles).
- Backend: FastAPI + SQLite (WAL) + acquisition thread + alert engine + analysis; 30 pytest tests.
- **ez2-Control driver working on both real controllers** (WebSocket protocol, address map and
  verification in `docs/controller-inspection.md`): reads everything needed; **writes pH / T /
  stirrer setpoints** (allow-list of 3 addresses, controller range/decimals check, read-back),
  confirmed live on R2-dx. Writes need a controller login: the driver logs in (Engineer)
  only around each write and logs out, and **never claims** a login held on the display/web
  UI (the command then fails, nothing is written). Password in
  `C:\ProgramData\FermentationManager\ez2-password.txt` (ACL: service + admins), never in git.
  **pH control from the profile** (planning only, before Start): PID loads P, I, D, dead band,
  cycle time; Adaptive loads the same (P/I/D as starting values the controller then adapts) plus settling time.
  External control is never written. Profiles can be deleted (refused while a planned
  experiment uses them; started experiments keep their plan snapshot).
- Reactor names: **R1-sx = 192.0.2.42, R2-dx = 192.0.2.41** (PC Ethernet 192.0.2.10/24, no gateway).
- Home shows every reactor: running experiment card, or "Idle · not recording" card with live values.
- **Phone layout** of a fermentation (mockups M3Phone/M3PhoneChart), used on narrow screens and on
  landscape phones: reactor tabs, 4 key values, chart presets, collapsible events / add measurement /
  setpoints (bottom sheet), full-screen landscape chart (axes, 1 h / 6 h / All with full-resolution
  data for the window, overlays, pinch-zoom, tap for values). Desktop unchanged.
- Demo: `FM2_SIM_SPEED=300 FM2_SAMPLING_INTERVAL_S=2 fm2-server` + `python scripts/demo_seed.py`.
- Approved mockups (Material 3, project palette): a Claude Design canvas
  "Fermentation Manager 2 — mockup" in Michele's artifacts.

## Decisions (do not re-open without asking)
- Time is always **hours since Start** (when recording begins), not since inoculation.
- An experiment is standalone. A **parallel run** is only logistics (R1|R2 switch, overlay);
  conditions/replicates are assigned per **analysis session**. Flag parallel-run pairs in
  sessions (info only, never affects statistics).
- Controller data immutable; manual entries editable with audit log.
- Experiments can be deleted when not recording (type the code to confirm; all data removed).
- Setpoints (pH, T, stirrer) writable anytime incl. remote, with allowed range, max step,
  read-back and log. **pH control mode + PID only from planning (profile), before Start.**
  Actuators, dose counter reset, calibration: controller only.
- Base: use the controller's **cumulative volume** as-is (dose monitoring calibrated on the
  controller; no re-computation, no smoothing — it is a staircase). Rate in mL/min computed
  per 15-min / 1-h bin; keep pump activation % too.
- Process limits (pH, T) read from the controller, shown on charts. **Writable since 9 Nov 2026**
  (Michele's decision; they are alarm limits only, the interlock limits that act on actuators stay
  controller-only): in the profile (loaded at planning) and during the run (phone/desktop, read back,
  logged). Alerts:
  out-of-limits (immediately at Start, then after 3 min, once per excursion),
  setpoint reached (once per setpoint; "already at setpoint" at Start), controller silent,
  setpoint changed on controller, fermentation probably finished.
- Colours: green = running/good, blue = completed, red only for alerts/limits.
- Analysis: Gompertz (Zwietering) on OD600 or AFU, base-dosing parameters, Welch + CI,
  ANOVA + Tukey; ggplot-style figures (points every 1/2/4 h, SD error bars, facets).
- Data lives on the lab PC; PC always on. Plus a read-only "consultation mode" on the
  laptop opening a backup copy (to build). Never open the live DB from a synced folder.
- Remote access via Tailscale (service). Backups to a dedicated cloud account or
  institutional folder, not Michele's personal OneDrive.
- Sampling every **5 s** (`FM2_SAMPLING_INTERVAL_S`): the base volume is integrated by the
  controller, so short additions are never lost; 5 s only adds time resolution.
- **Channels per experiment** (planning panel, editable during the run): Temperature (sensor +
  heating/cooling %), pH (sensor + base pump: cumulative, mL/min, %), Stirrer (sensor + motor output),
  dO2 (probe + N2/air valve %; notice: air not configured yet). Default T + pH + stirrer. A channel
  switched off is hidden, raises no alerts and is not written by the profile, but is still recorded.
- Deployment: code in `C:\FermentationManager`, data in `C:\ProgramData\FermentationManager\data`,
  Windows service (WinSW/NSSM or Task Scheduler) under a dedicated local account, starting at
  boot with no user logged in. Michele's own Windows account only for maintenance sessions;
  a generic local "Laboratorio" user for daily use (browser to http://localhost:8000).
- Licence: undecided (CC BY-NC is not meant for software; options PolyForm NC, GPL/AGPL, MIT).

## Done on the bioreactor PC (9 Nov 2026)
- Python 3.12 (all users), Node.js LTS, Git; repo in `C:\FermentationManager`, `.venv`, frontend built.
- Tailscale (Michele's account for now; move to a dedicated account later, with backups),
  "Run unattended", key expiry disabled; firewall rule: TCP 8000 only from 100.64.0.0/10.
- Windows service `fm2` via WinSW (`deploy/windows/`), virtual account `NT SERVICE\fm2`,
  data in `C:\ProgramData\FermentationManager\data`, logs in `…\logs`; survives reboot
  with nobody logged in; reachable from the phone.
- Service runs the real controllers (read-only). Update procedure (admin PowerShell): `git pull`;
  if dependencies changed: `Stop-Service fm2` first (the service locks `fm2-server.exe`), then `pip install -e .`;
  `npm.cmd run build` in `frontend`; `Start-Service fm2` / `Restart-Service fm2`.
  **Now one command:** `deploy\windows\update.ps1` (pull, pip if deps changed, npm, build, restart, health
  check; asks before restarting while an experiment records, `-Force` to skip). `grant-update-rights.ps1 -User <name>`
  (admin, once) lets that account restart only fm2, so updates also work from a normal shell.

## Settings, notifications, backups (9 Nov 2026)
- Settings page (mockup M3Settings): bioreactors (read-only), remote access (switch + allowed ranges and
  max step), notifications (ntfy or Telegram; one switch per rule; alert timing/tolerances), data and
  backup, credits. Stored in table `app_settings`, applied without restart.
- Notifications are sent on a background thread; every event is still stored in the app.
- Backups: `docs/backup.md`. **Destination not chosen yet** (Michele); the folder must be writable by
  `NT SERVICE\fm2` and reachable with nobody logged in.

- Home shows Server and Remote access (Tailscale CLI, or a Tailscale address on the PC as fallback).
- Phone: "New" floating button above the bottom bar. "Plan on R1/R2" preselects the reactor.
- Settings › Media and buffers: list and add (target of New → Medium / Buffer).

## Experiment analysis (9 Nov 2026)
- Analysis › single experiment (mockup M3ExperimentAnalysis), also reached from the experiment page
  (Fermentation | Analysis tabs). Summary, faceted time course (ggplot grey; manual points mean ± SD per
  time point, fitted model line; controller data averaged over 15 min / 1 / 2 / 4 h), kinetic table on
  every manual variable, base-dosing table, export.
- Models (`analysis/kinetics.py`): modified Gompertz and logistic (Zwietering 1990), Baranyi-Roberts
  (1994), all on y = ln(X/X0) with X0 = first point; ± = SE from the fit covariance; R²/RMSE on the ln scale.
- Export: XLSX (one sheet per block) or CSV zip; blocks controller (optional resampling), manual,
  parameters, metadata (+ plan snapshot, change log, events). Figures rendered server-side with
  matplotlib (PNG/SVG/PDF, width in mm, dpi, themes grey/bw/classic). **New dependency: run
  `pip install -e .` on the PC** after pulling.
- Desktop navigation rail stays fixed while the page scrolls.
- To do (Michele, 9 Nov): **loop on/off** from the experiment page — start the control of a loop (pH, T,
  stirrer) or stop it (control off and its actuator stopped). Never manual actuator outputs: those stay
  on the physical controller. Data recording stays separate. Needs an inspection session to find the
  loop on/off address per loop; then confirmation dialog, read-back, change log, remote disabled by
  default. Until then the driver never writes loop or actuator states.

## Analysis sessions (9 Nov 2026)
- Analysis › sessions (mockup M3Analysis): conditions are session-only labels (ordered: A, B, ...);
  experiments with the same condition are replicates. Auto-saved on every change.
- Comparison per parameter: mean ± SD, Welch's t-test with Δ and 95 % CI (2 conditions), one-way ANOVA +
  Tukey HSD (3+); no test with n < 2. Parallel runs listed for information only.
- Curves: manual variables interpolated per experiment on a 1/2/4 h grid within the replicates' common
  range; controller variables averaged per interval then mean ± SD. Line = model fitted on the pooled
  replicates (each normalised to its first point).
- Fits now try several starting points and pick the best (pooled replicates with near-identical times
  used to give a step-shaped fit).
- Export: CSV zip / XLSX (experiments + per-experiment values, comparisons, mean curves of every
  variable, session info); figure PNG/SVG/PDF.
- Fixed: trend chart kept the previous variable's setpoint series (ECharts merge); phone full-screen
  chart closed after a few seconds (effect re-run on every poll).

## Users and desktop shortcut (9 Nov 2026)
- Sign-in screen before the app: pick a user (optional 4–8 digit PIN) or Guest; remembered per
  browser/device until "Switch user" (rail, bottom; Settings › Users on phones).
- Identity only, no roles. The server signs a token (HMAC, secret in `data\auth-secret.txt`) and writes
  the user on experiments, profiles, measurements and edits, setpoint/limit/profile commands, sessions,
  and Start/End events. The `author` sent by a browser is ignored.
- A user with a PIN can be renamed/archived/PIN-changed only when signed in as them; a PIN change
  signs out their other devices. 5 wrong PINs → 5 min lock. Forgotten PIN (admin shell):
  `.venv\Scripts\python -m fm2.users reset-pin "Name" --data-dir C:\ProgramData\FermentationManager\data`.
- Desktop/Start-menu shortcut for all Windows users: `deploy\windows\create-shortcut.ps1` (admin, once);
  opens http://localhost:8000 in an Edge/Chrome app window with the FM icon (`fm2.ico`).

## Library, Experiments page, End of experiment (9 Nov 2026)
- Home shows "Last experiments": recording and planned first, then the most recent, 10 rows, archived hidden.
- Experiments tab: full list, status chips (incl. Aborted), search, "Analysed" filter, "Show archived".
  Labels: "Analysed · N" (number of analysis sessions using it; derived, never blocks reuse) and "Archived".
- Archive (completed/aborted only): hidden from Home and session pickers, kept in sessions already using it;
  reversible; nothing deleted.
- One "End of experiment" button (desktop and phone) → Completed (valid, offered in analysis) or Aborted
  (run failed, kept but not offered), optional note shown on the experiment page and in the event.
- Library (replaces Profiles; media and buffers moved out of Settings): controller profiles, media,
  buffers, manual variables. Edit, archive/restore, delete only when unused. New menu links here.
- Manual variable roles: biomass (growth fit, μmax = specific growth rate), product (fit as accumulation:
  λ/μmax describe accumulation), substrate and other (plotted/exported, no growth fit). Yields
  (Yx/s, Yp/x, qs, qp) exist in `analysis/kinetics.compute_kpis` but are not shown yet.
## Remote decided by the server (9 Nov 2026)
- Fixed: "local"/"remote" was declared by the browser and the frontend always sent "local", so the
  Remote access switch never applied to the phone. Now the server decides from the connection:
  loopback (the lab PC itself) = local, anything else (Tailscale) = remote; logged per command.
- Settings › Remote access and › Data and backup are read-only from remote devices (server returns 403).

## Installable app (PWA) and HTTPS (9 Nov 2026)
- `frontend/public`: manifest.json, icons (any + maskable), apple-touch-icon, favicon, `sw.js`
  (service worker that caches nothing: live data never stale; registered only in secure contexts;
  will host native push).
- HTTPS through Tailscale: `deploy\windows\enable-https.ps1` (admin; needs MagicDNS + HTTPS
  certificates enabled in the Tailscale admin console) → `tailscale serve --bg 8000` →
  https://<pc>.<tailnet>.ts.net. Requests then reach FastAPI from 127.0.0.1 with X-Forwarded-For /
  Tailscale-User-* headers: `is_local` treats those as remote.
- "No connection to the lab PC" banner (network error or proxy 502/503/504) + grey controller pills;
  polling refreshes immediately when the phone screen comes back on.
- Telegram: pasted tokens normalised (spaces, quotes, "bot" prefix, whole URL), format checked, 401/403
  hints; "Send test" asks to save first (the test uses saved settings).

## Appearance, licence, Telegram helper (10 Nov 2026)
- Settings › Appearance (per device, localStorage `fm2.theme`): Light; Grey (#DADFE1 page, white cards); Dim (dark teal-slate page derived
  from the palette's data slate #455e6f, white cards and charts); Dark (dark cards, charts on light
  panels so they read like the exports). Implemented with CSS variables (`html[data-theme]`).
- Licence GPL-3.0-or-later: `LICENSE` (verbatim GPLv3), notice in README and Settings › Credits.
  Credits order: DeFENS, University of Milan · DiSAFA, University of Turin.
- Telegram: clearer labels (bot token vs your own chat id) and "Find it" (getUpdates via
  POST /settings/notifications/telegram-chats, bots skipped).

## Experiment copies to an archive folder (10 Nov 2026)
- Settings › Data and backup › Experiment copies: archive folder (e.g. a USB stick, `E:\FM2-archive`),
  format XLSX / CSV zip, "offer a copy at End of experiment" (default on). Local-only section.
- End of experiment dialog: "Save a copy of the data to …" (ticked by default when a folder is set); the run
  ends even if the copy fails (stick unplugged), and the dialog says why.
- Analysis › Export: "Download" (browser) and "Save to archive" (written by the lab PC, any device).
- File names: `YYYY-MM-DD_HHMM_<code>_<name>.<ext>` (local time; characters invalid on Windows replaced);
  downloads use the same name. Each copy is a new file (never overwritten) and is logged as an event.
- No periodic backup during a run (decided): every reading is committed to SQLite (WAL) as it arrives and
  the service resumes recording after a power cut; set "Restore on AC power loss" in the PC BIOS.

## Pre-start reminder and profile in the plan (10 Nov 2026)
- "Start recording" first shows a reminder (no checkboxes, as Michele asked): check probe calibration,
  dose monitoring calibration, cooling and condenser water valves. Cancel / Start recording.
- Planning: "Controller profile" is part of the plan and can be chosen at creation with the bioreactor
  off; a separate card "Controller Rx: check and load" compares and loads it once the controller answers
  (an unreachable controller is a warning while planning, not an error; Start still needs it reachable).

## Next steps
1. Live check of pH control loading on R2 (cycle time change) and of the phone layout on a real phone.
2. Choose the backup destination; consultation mode on the laptop.
3. Chart zoom that fetches full-resolution data for the selected window (browser gets ≤2000 points).

## Still to build in the UI
App push notifications (need HTTPS:
`tailscale serve`) and PWA install, phone layout for planning, dark / dimmed theme (Settings, not urgent).
