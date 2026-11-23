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
- Reactor names: **R1-sx** and **R2-dx**; their IP addresses (and the PC's static address on the controllers'
  switch, no gateway) are in `C:\ProgramData\FermentationManager\data\local.env`, never in the repository.
- Home shows every reactor: running experiment card, or "Idle · not recording" card with live values.
- **Phone layout** of a fermentation (mockups M3Phone/M3PhoneChart), used on narrow screens and on
  landscape phones: reactor tabs, 4 key values, chart presets, collapsible events / add measurement /
  setpoints (bottom sheet), full-screen landscape chart (axes, 1 h / 6 h / All with full-resolution
  data for the window, overlays, pinch-zoom, tap for values). Desktop unchanged.
- Demo: `FM2_SIM_SPEED=300 FM2_SAMPLING_INTERVAL_S=2 fm2-server` + `python scripts/demo_seed.py`.
- Approved mockups (Material 3, project palette): a Claude Design canvas
  "Fermentation Manager 2 — mockup" in Michele's artifacts.

## 2.3.0 (23 Nov 2026, branch `v2.3`)
- Bioreactors are rows of the `reactors` table (Settings › Bioreactors & probes): name, driver, address,
  reading interval 2–300 s (default 5), enabled; drivers re-created on change without restart; seeded at
  first start from the old configuration (FM2_REACTORS / local.env). Idle reactors keep 3 h of live values
  in memory only (`GET /api/reactors/{id}/history`).
- User levels admin / operator / observer, enforced in one middleware (`ROLE_RULES` in app.py) **only once
  an administrator exists**; the first administrator is set on the lab PC by a user with a PIN; the last
  administrator cannot be demoted or archived; new users are operators; users without PIN act as observers.
- Buffer: concentration (M) and equivalents in the Library, frozen into the experiment at plan time with the
  available volume; totals in mmol in analysis/export; event "buffer_low" once when below 10 %.
- Templates (`experiment_templates`), per-channel plan values (`channel_params`: dO2, stirrer limits —
  saved only, not loaded on the controller yet), unified timeline `GET /api/experiments/{id}/timeline`.
- UI: side column (≥1000 px), notification centre, Controller page (loops & actuators, idle trends),
  Latest events, Sampling volume, End warning with loops on, logo (wordmark + bioreactor icon).
- PWA: `sw.js` caches the interface only (never `/api`); offline screen "Lab PC not reachable".
- 23 Nov live tests (docs/controller-inspection.md): manual `2.20` and prime `2.46` work with an Operator
  login; **no controller-side stop** (prime ran until the off command, also after disconnect); **the controller
  does not enforce the loop interlock on manual/prime** (only its UI does); display login → `-5`.
  Loop configuration commands `3.51/3.52/3.53` (Engineer, loop off). All for 2.3.x, not written yet.

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
  setpoint changed on controller, fermentation probably finished, base counter reset (info).
- Loop configuration (10 Nov 2026, `fm2/loops.py`, `GET /api/reactors/{id}/loop-config`): read only, cached
  20 s. Planning shows a compact box per channel in use (loop on/off, control type, actuators with side,
  cascades, warnings: not installed, no actuator, pump not calibrated / dose monitoring off, dO2 probe ~0 % or
  SP > 100 %, cascade starts another loop). Assignments are changed only on the controller display.
  Profiles also hold the temperature loop (mode + P/I/D/dead band/cycle/settling, like pH) and optionally
  `loop_assignments` recorded from R1/R2 ("Record from"): if the controller's actuators for a loop differ,
  load-profile skips that loop's control settings (status "skipped") but loads setpoints and limits.
  New channels: Level / antifoam (contact, antifoam mL and %), Redox (mV); acid (Addition 2) mL and % in pH.
- Base counter resets (power cycle / reset on the display: seen 9→10 Nov, 1.61 → 0.00 mL): the raw
  counter is stored as read; readings, charts, totals and exports use it continued across resets
  (`base_dosing.stitch_counter`, drop > 0.05 mL = reset); exports then also carry `base_counter_raw_ml`.
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
- **Loop on/off** (done 10 Nov, PR #23): Start / Stop selected loops on the experiment page writes
  `1.14.<sensor>` (0/1, like the controller's own buttons) for pH, T, stirrer, dO2, level — only loops
  enabled in Settings › Loop control (per reactor, lab PC only; default pH, T, stirrer; remote switching
  off by default), set up on the controller and, to start, with an actuator; a loop that starts others
  in cascade (dO2 → stirrer) needs those enabled too. Read-back, event per loop; alert
  `loop_switched_off` when a loop of a channel in use goes off not by FM2 (info when switched on).
  Never manual actuator outputs, assignments, dose counters, calibration: controller only.

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
- Licence: GPL-3.0-or-later until 19 Nov 2026, then AGPL-3.0-or-later (see "2.2.1 rest" below): `LICENSE`, notice in README and Settings › Credits.
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

## Preparation phase and editable plan values (10 Nov 2026, revised)
- New status `preparing`. Planning → **Go to fermentation**: the same live page as a running experiment,
  bar "● Not recording · t = —" with **Start selected loops / Stop selected loops** (loops = actuators
  only, sensors keep being read) and **Start experiment** (reminder
  popup) in the place of End of experiment. Live values, setpoint/limit edits and alerts/notifications
  work (no base-dosing "finished" rule; at-start messages say "when monitoring began"); **nothing is
  recorded before Start**, so charts stay empty. Phase events get negative times at Start.
  "Back to planning" in the header. One preparing/fermenting experiment per reactor.
- Planning: one table "Controller Rx: plan, check and load" — Parameter | Plan | Edit | Controller | Check;
  PID parameters as separate rows; controller off → Controller column empty, Check "Controller off".
  Values are the plan's (`Experiment.plan_params`, copied from the profile); any edit → "Custom · based on
  <profile>, not saved" (Reset available). Load profile to controller / plan-check use the plan values
  (planned or preparing).

## Versioning
- PEP 440 in `pyproject.toml` / `fm2/__init__.py` (frontend `package.json` mirrors it). 2.0.0.dev1–dev19
  followed the merged PR numbers. Since the loop-control work (new functions) the line is **2.1.0.devN**,
  N counted from 1 and increased at each merged PR (2.1.0.dev1 = counter-reset handling, PR #21; 2.1.0.dev2 = loop configuration in planning, PR #22; 2.1.0.dev3 = loop start/stop, PR #23; 2.1.0.dev4 = planning save fix + temperature dead band + no bot token in logs). After the test fermentation (10–12 Nov 2026) the roadmap uses semver: **2.1.1** = corrections (this release), 2.1.2 = controller use without an experiment, 2.1.3 = analysis of loop performance, 2.2 = interface + manual + citability.
  Release 2.1.0 (git tag) after the test fermentations; then semantic versioning — bug fixes 2.1.1,
  new features 2.2.0, incompatible changes (data/format) 3.0.0. The version is shown in Settings › Credits.

## Next steps
1. Live check of pH control loading on R2 (cycle time change) and of the phone layout on a real phone.
2. Choose the backup destination; consultation mode on the laptop.
3. Chart zoom that fetches full-resolution data for the selected window (browser gets ≤2000 points).

## Still to build in the UI
App push notifications (need HTTPS:
`tailscale serve`) and PWA install, phone layout for planning, dark / dimmed theme (Settings, not urgent).

## Dead band (13 Nov 2026)
The ez2-Control shows and uses "Dead Band" (3.9.<sensor>) only where `1.29.<sensor>` (deadbandEnabled, read
only) is 1. On R1 and R2: pH 1 (dead band 0.10 pH on 10 Nov), temperature 0. FM2 reads the flag
(`PhControl.deadband_enabled`), hides the temperature dead band in Profiles/Planning, does not write it and
ignores it in read-back checks.

## 2.1.1 (13 Nov 2026)
Planning save fix and temperature dead band (from 2.1.0.dev4); base-rate legend colour; manual variables
also after the end of a run (sampling given as t (h) since Start or date/time: `MeasurementIn.t_h`);
trend axes/toggles remembered per browser (`localStorage` "fm2.trendPrefs"); events and change log show the
latest 8/5 with "Show all"; manual variables moved under the charts; Settings › Credits shows branch and
commit (`fm2/build_info.py`, reads .git without the git executable) and warns when not on main.

## 2.1.2 (13 Nov 2026)
- **Reactor monitoring page** `/reactors/:id` (`ReactorPage.tsx`): live values and commands (setpoints, process
  limits, loops, dose counters) with or without an experiment; events and change log of the last 3 days
  (`GET /reactors/{id}/activity`). Commands without an experiment are logged as reactor events
  (`Event.experiment_id = None`). Home: idle cards open it; running cards link "Controller"; completed pages
  show "Monitor Rx".
- **Home cards** show every value of the experiment's channels (base and Addition 2 cumulative and 15-min
  rate — `acid_rate_bins` in the analysis —, stirrer, dO2 + N2/air, level, redox); idle cards show what the
  controller reports. The preview chart is chosen per reactor (localStorage "fm2.homePreview").
- **Loops** button: one dialog, Start/Stop per loop (`components/ReactorControls.tsx`).
- **Reset counters** (decision 13 Nov 2026, rule change): FM2 may reset the dose counters base (29), acid (28),
  antifoam (30) by writing "1" to 2.13.<actuator>, exactly the controller's "Reset Dose Monitor" (any login
  level); the driver refuses any other value. `POST /reactors/{id}/counters/reset`, read-back (≤ 0.05 mL),
  event `counter_reset_manual:<counter>`, no duplicate "counter_reset" alert. Data: `_readings_df` keeps the
  stitched total (analysis); `GET /readings` shows the counter restarting from 0 at FM2's resets and adds
  `<x>_total_ml`.
- Fermentation page: header and command bar sticky (`.sticky-head`, off on screens < 760 px tall).

## 2.1.3 (13 Nov 2026)
- **Regulation analysis** (`analysis/control.py`, decision 12 Nov: descriptive metrics first, rule-based
  suggestions in 2.2). Per loop (pH, T, stirrer, dO2): regulation starts the first time the error changes sign
  or is within the tolerance (pH 0.05, T 0.3 °C, stirrer 5 rpm, dO2 5 %); from there to the end, time-weighted
  (reading weight capped at max(120 s, 3 sampling intervals)), using the setpoint in force at each reading:
  effective mean ± SD, bias, MAE, RMSE, max above/below, % time within tolerance, crossings of the band per hour,
  overshoot in the first hour. Readings before Start (t < 0) are excluded.
- **Dose episodes** on the pH loop (base and Addition 2): runs of counter increases, merged across pauses
  < max(60 s, 2.5 sampling intervals); pH at pump start / at last dose / peak in the 5 min after, volume,
  interval, distance from setpoint at start.
- **Loop on/off recorded** at each reading from 2.1.3 (`loop_<key>` extra fields, 1/0). With it, only readings
  with the loop on count; older runs assume the loop on from the time the setpoint was reached.
- Shown in Analysis › single experiment ("Regulation" card); key values in the session comparisons
  (pH/T effective mean, bias, % within tolerance, pH at base pump start) and in the export "Parameters".

## 2.2.0 (13 Nov 2026)
- **Estimated end of fermentation** (`base_dosing.estimate_end`): the rule of the "probably finished"
  notification applied to the recorded series, with the thresholds of Settings › Notifications (fraction of
  peak, hold time, 15-min window): the end is when the base rate fell below the threshold for good (the
  notification fires `hold` later). Dashed vertical line in the trend, buffer-dosing, home/phone and analysis
  charts; value in the analysis summary, session comparisons (`t_end_h`) and export.
- **Buffer dosing** panel (was "Base dosing"): base buffer (green) and, when dosed, acid buffer = Addition 2
  (blue) rates and cumulative volumes.
- **Controller panel** redesigned (`ReactorControls.tsx`, `.ctrl-row`): name, value, target/limits; one Edit
  (`ControlEditDialog.tsx`: target and limits, limits sent first, each read back).
- **Manual variables in the trend chart**: keys `m:<name>`, shown as points.
- **Loops as "below target / above target"** (`loopText.ts`, `loops.sides_text`) in planning, the Loops dialog,
  profiles and the assignment-difference messages (which now say to change it on the display or record again).
- **Hints on the loop parameters** (`control.hints`, decision 12 Nov "level 2"): from the regulation metrics and
  the plan's mode/parameters; oscillation → smaller P; persistent offset → integral action (no assumption on
  whether I is a time or a gain); pump starting far below target → dead band / P / cycle time; peak after
  doses → smaller doses or mixing; temperature overshoot. Adaptive: evaluation only.
- **Settings**: order Users, Bioreactors, Loop control, Notifications, Remote access, Data and backup,
  Appearance, Credits. QR code to open the app on a phone (`AppQr.tsx`, npm `qrcode`; address from Tailscale
  name/IPs or saved in `remote.app_url`). Short changelog in Credits (`frontend/src/changelog.ts`).
- Deferred to 2.2.1 (13 Nov decision): license review, "cite as"/CITATION.cff/DOI, manual.

## 2.2.1 (19 Nov 2026)
- **Profiles editable** (`PUT /api/profiles/{id}`, Edit in Library › Controller profiles). Experiments store
  `profile_ref` = the profile's values when the plan was saved (create, or a patch that changes profile or plan).
  Before an edit, every experiment using the profile gets `plan_params` (if missing) and `profile_ref` (if missing)
  = the old values, so they keep their values. `ExperimentOut.profile_changed` = ref differs from the profile now
  (name not compared: a rename shows only the new name, `profile_name`). "Custom" (`profile_modified`) compares the
  plan with `profile_ref`. Warning in planning (with "Use the new values of …") and in the experiment header;
  export metadata gets the profile name and the flag. Profiles list shows "used by N experiments"; names unique (409).
- **Names first**: experiment and session titles show the name bold and the code small (`ExpName`, `.code-chip`);
  the experiment list is a row list on phones (≤ 640 px). Live payload includes the experiment name.
- **Monitoring page**: one compact bar (back, title, subtitle, experiment link, Loops / Reset counters) and one
  status line (recording state, refresh icon + age of the last reading, `fmtAge`: s / min / h). On phones only these
  stay at the top; Loops / Reset counters scroll with the page. "Plan on Rx" removed there and from idle Home cards.
- **Box-and-whisker** of any per-experiment parameter by condition in analysis sessions (only parameters with values;
  every experiment as a point; quartiles as numpy) and `GET /api/analysis-sessions/{id}/boxplot.{fmt}?param=`.
- **Black & white figures**: `mono=true` on the experiment, session and box-plot figures (black, line types,
  filled/open markers, hatching); file names end in `-bw`.
- **Style** (all themes): dotted page (`--dot`), sticky headers painted with the same fixed pattern; main titles
  `.display` (Plex 700 + 0.8 px stroke, one highlighted word in `--accent-text`); "Live" pill (`.live-pill`). Themes
  Light, Cream, Sky, Dim, Dark; stored "grey" migrates to "sky" (`theme.ts`). Plex Sans 700 bundled.
- Still open for 2.2.1: license review, "cite as"/CITATION.cff/DOI, online manual (draft in Michele's Claude Docs).

## 2.2.1, rest (19 Nov 2026, branch v2.2.1-rest)
- Licence: `LICENSE` is now the verbatim AGPLv3 (from GitHub's choosealicense text, 661 lines), "or later" kept.
  Michele is checking with the university/PhD office that he can license the software (rights); the repository is
  still private. Do not make it public before that confirmation and before the git history check (see below).
- Citation: `backend/fm2/citation.py` is the single source (title, author, year, URL, DOI = None until the first
  Zenodo release). Used by `/system` (Settings › How to cite: reference + BibTeX). Exports (Metadata / Session sheet) and
  PNG/PDF/SVG figures carry only "Fermentation Manager 2 (FM2) vX" as provenance: no invitation to cite in
  exported files (Michele, 19 Nov 2026).
  `CITATION.cff` (ORCID still to fill in), README "How to cite", `THIRD_PARTY.md` = `frontend/src/thirdParty.ts`.
- Settings: sections Manual (placeholder, manual not reviewed yet), How to cite, Credits and license (+ Third-party software).
- UI: New button dark blue (primary) in Light/Cream/Sky, unchanged in Dim/Dark; chips, segmented controls, phone
  tabs, select and channel boxes have a solid surface (white) instead of transparent.
- Not done on purpose: corrections after the 19 Nov test, manual text, "Methods" section, example dataset.
