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
- Service runs the real controllers (read-only). Update procedure: `git pull`,
  `pip install -e .` if dependencies changed, `npm.cmd run build` in `frontend`, `Restart-Service fm2` (admin).

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

## Next steps
1. Live check of pH control loading on R2 (cycle time change) and of the phone layout on a real phone.
2. Choose the backup destination; consultation mode on the laptop.
3. Chart zoom that fetches full-resolution data for the selected window (browser gets ≤2000 points).

## Still to build in the UI
Experiment analysis & export, analysis sessions, app push notifications (need HTTPS:
`tailscale serve`) and PWA install, phone layout for planning, dark / dimmed theme (Settings, not urgent).
