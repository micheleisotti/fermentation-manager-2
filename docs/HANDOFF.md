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
- `main`: backend core (merged PR #1).
- Branch `frontend-shell`: React UI shell, Home, Fermentation screens.
- Branch `planning` (on top of frontend-shell): Planning, Profiles screens, this file.
  Merge frontend-shell first, then planning.
- Backend: FastAPI + SQLite (WAL) + acquisition thread + alert engine + analysis;
  23 pytest tests. Simulator driver works; **ez2-Control driver is a stub**
  (`backend/fm2/drivers/ez2control.py`). Inspection checklist: `docs/controller-inspection.md`.
- Demo: `FM2_SIM_SPEED=300 FM2_SAMPLING_INTERVAL_S=2 fm2-server` + `python scripts/demo_seed.py`.
- Approved mockups (Material 3, project palette): a Claude Design canvas
  "Fermentation Manager 2 — mockup" in Michele's artifacts.

## Decisions (do not re-open without asking)
- Time is always **hours since Start** (when recording begins), not since inoculation.
- An experiment is standalone. A **parallel run** is only logistics (R1|R2 switch, overlay);
  conditions/replicates are assigned per **analysis session**. Flag parallel-run pairs in
  sessions (info only, never affects statistics).
- Controller data immutable; manual entries editable with audit log.
- Setpoints (pH, T, stirrer) writable anytime incl. remote, with allowed range, max step,
  read-back and log. **pH control mode + PID only from planning (profile), before Start.**
  Actuators, dose counter reset, calibration: controller only.
- Base: use the controller's **cumulative volume** as-is (dose monitoring calibrated on the
  controller; no re-computation, no smoothing — it is a staircase). Rate in mL/min computed
  per 15-min / 1-h bin; keep pump activation % too.
- Process limits (pH, T) read from the controller, shown on charts; alerts:
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
- Deployment: code in `C:\FermentationManager`, data in `C:\ProgramData\FermentationManager\data`,
  Windows service (WinSW/NSSM or Task Scheduler) under a dedicated local account, starting at
  boot with no user logged in. Michele's own Windows account only for maintenance sessions;
  a generic local "Laboratorio" user for daily use (browser to http://localhost:8000).
- Licence: undecided (CC BY-NC is not meant for software; options PolyForm NC, GPL/AGPL, MIT).

## Next steps on the bioreactor PC
1. Check/install Python 3.12 (all users, add to PATH), Node.js LTS, Git.
2. Clone the repo into `C:\FermentationManager`, `pip install -e .`, `npm install && npm run build`
   in `frontend/`, run with the simulator, open from a phone via Tailscale.
3. With controllers connected: follow `docs/controller-inspection.md` (browser DevTools on
   the controller web UI; check the manual for OPC UA / Modbus first). Implement read first,
   then setpoint writes with read-back.
4. Service + autostart, backup job, consultation mode.

## Still to build in the UI
Experiment analysis & export, analysis sessions, settings, dedicated phone layout
(collapsible panels, full-screen landscape chart), push notifications.
