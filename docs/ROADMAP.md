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

## 4. Other open items
- Loop on/off from the software (pH, T, stirrer), never manual actuator outputs — see HANDOFF.
- Dark / dimmed theme; consultation mode on the laptop; chart zoom with full-resolution data.
- Licence to choose before publishing as open source (PolyForm NC, GPL/AGPL, MIT…).
