# ez2-Control — inspection results (9–10 Nov 2026)

Inspected read-only from the bioreactor PC through the controllers' own web UI
(page scripts and live reads). Both units run firmware **mE.2.9.2.4** and expose the
**same addresses**, so one driver serves both.

| Name in FM2 | Controller name | IP |
|---|---|---|
| R1-sx | Bioreactor 1 sx | see `local.env` |
| R2-dx | Bioreactor 2 dx | see `local.env` |

Network: the controllers sit on a separate switch, private subnet (addresses in `local.env`, not in the repository). The PC's
Ethernet adapter has the static address on that subnet **without a gateway**; Wi-Fi
(eduroam) carries Internet and Tailscale. Never move the controllers onto eduroam.

## Interface
- **OPC UA**: present in firmware but `opcuaServerAllowed` (0.92) = 0 (not licensed).
  Not used.
- **WebSocket** `ws://<ip>/`, binary frames of ASCII text, used by the web UI itself
  (`/Scripts/data/data.js`). No authentication needed to read.
  - read: `J<address>S\r` → answer `J<address>A<value>\r` (several per frame allowed;
    answers may be split over frames). Values like `1.0E+05` contain an `E`: a line is
    data if its `A` precedes any `E`.
  - write: `J<address>s<value>\r` (lowercase `s`), value as plain text, as the web UI's
    setpoint dialog does (`rightpanelsensors.js` → `controlData.setpoint()`; the UI only
    checks min ≤ value ≤ max of the sensor). FM2 writes **only** `1.13.0/2/13` (setpoints).
  - **Login** needed to write (levels 0 View, 1 Operator, 2 Engineer, 3 Service):
    `J0.11s<level>|<user>|<base64(password)>\r`, state read back from `0.11`
    (`<level>|<user>|…`), logout `J0.11s0|0|\r` (`login.js`). One login at a time; the UI
    can "claim" it from another user (`0.12`). **FM2 never claims**: it logs in only around
    one setpoint write and logs out; if refused, nothing is sent. The password lives in
    `C:\ProgramData\FermentationManager\ez2-password.txt` (service + admins only), never in git.
  - The controller **drops idle connections after a few seconds without a close frame**.
    The driver reconnects and retries once (cost ≈ 50–200 ms).
- **HTTP history** `/Data/history.csv?<sensor>|<divider>`: ~500 lines
  `value|alarm high|alarm low|setpoint|output`, no timestamps. Not used (could backfill).

## Address map (sensor index: pH 0, dO2 1, T 2, stirrer 13; base pump = actuator 29)

| Quantity | Address | Notes |
|---|---|---|
| Process value | `1.15.<s>` | `NO CONT` etc. → none |
| Setpoint | `1.13.<s>` | |
| Process (alarm) limit high / low | `1.4.<s>` / `1.5.<s>` | shown on charts, used by alerts; writable for pH and T |
| Interlock limit high / low | `1.20.<s>` / `1.21.<s>` | act on actuators: **never written** |
| Loop active | `1.14.<s>` | |
| Control type | `1.19.<s>` | 0 PID, 1 Adaptive, 2 External, 3 Level, 4 Flow, 5 P-Adaptive, 6 TCUSP |
| PID P / I / D | `3.0.<s>` / `3.3.<s>` / `3.6.<s>` | |
| Loop interval / deadband | `3.12.<s>` / `3.9.<s>` | pH: 10 s / 0.10 |
| Actuator name | `2.1.<a>` | 29 = "Alkali" |
| Actuator assignment / side | `2.7.<a>` / `2.8.<a>` | Alkali (29): sensor 0, +1 (below SP); Addition 2 (28): sensor 0, −1 (acid, not calibrated) |
| Actuator output (flow) | `2.12.<a>` | mL/min for pumps |
| **Dose monitoring, cumulative** | `2.13.<a>` | **mL** |
| Dose factor (calibration) | `2.4.<a>` | Alkali: 4.86 mL/min (R1), 4.80 (R2) |

Verified live on R2: Alkali on manually for ~20 s → `2.13.29` 0.00 → 1.61 mL
(0.08 mL/s = 4.80 mL/min), `2.12.29` = 4.80 while on, 0 after. The controller display
briefly showed 1.56 and then 1.61: display refresh lag, the network value is the true one.

Pump activation % = `2.12.29 / 2.4.29 × 100`.

## Display refresh (observed 9 Nov 2026)
The controller's own display does not refresh an open page when a value changes remotely
(setpoint/loop parameters written by FM2, or the dose counter after a pump stop): it shows
the old value until the page is left and re-entered. The network value is the true one
(verified: pH SP 6.30 → 6.20 and cycle time 10 → 15 loaded from a profile, display showed
the old SP until the page was reopened).

## Differences between the units (read, not configured by FM2)
- Temperature loop: R2 Adaptive, R1 PID (P 25.2 / I 3053.5 / D 28.3).
- Stirrer alarm limits: R2 20–500 rpm, R1 200–900 rpm.
- Controller clocks differ by ~1 min → FM2 timestamps use the PC clock.

## Second inspection (10 Nov 2026, R2-dx): loops, level/antifoam, Addition 2

Source: a read-only dump of every address that answers (7 522 values) and the web-UI
scripts, saved with `scripts/ez2_inspect.py` (`dump`, `web`, `watch`; output in the git-ignored
`data/inspect/`; it sends only `J…S` reads, never logs in). The **meaning of each address is taken from the web UI's own
accessor names** (`Scripts/data/sensordata.js`, `actuatordata.js`, `controldata.js`,
`systemdata.js`), not guessed. Bioreactor freshly mounted, no run, all loops off.

### Accessor names (from the web UI)
Sensors `1.<p>.<s>`: 0 defaultName · 1 name · 3 unit · 4/5 alarm limit H/L · 11 available ·
12 levelType · 13 setpoint · **14 active (loop on/off)** · 15 value · **16 inLoop** (loop has
an actuator) · 17 output · 19 control type · 20/21 interlock H/L · 22 alarmState ·
23 interlockState · 24 levelReactState · 25/26 affectedActHigh/Low (actuator bit masks) ·
27 interlockDeadband · 28 interlockHoldTime · 48 interlockStatus · 49 alarmStatus ·
50 connected · 52/53 assigned/position (a sensor used as actuator, cascade) · 58 decimals ·
64/65 digital label high/low.

Actuators `2.<p>.<a>`: 0 defaultName · 1 name · **3 doseMonEnabled** · 4 factor (calibration) ·
5 unit · 6 domain (1 = on/off) · **7 assigned (sensor)** · **8 position** (+n = n-th actuator on
the "below setpoint" side, −n on the "above" side) · 9/10 limit high/low · 11 type · **12 value
(output)** · **13 doseValue (cumulative)** · 15 timeFactor · 17 doseTime · 18 doseFactor ·
**19 isControlled** · **20 manualValue** · 21 available · 23 delay · 24 on time · 25 off time ·
29 inhibit · 44 decimals · 46 prime · **47 interlockStatus** · **49 active** · 51 interval.

Loops `3.<p>.<s>`: as before (0 P … 12 cycle time, 71 settling time) plus 22 pulseTime,
25 deadTime, 49 levelFirstAction (0 dead time, 1 pulse time), 51/52/53 add actuator on the
positive / negative side / remove (configuration commands: **never written by FM2**),
54–70 feed profile, 83 feedPaused, 85/86 cascade limits.

### Loop on/off
- State **and** command: `1.14.<s>` (0 off, 1 on). The web UI's Start/Stop buttons write
  exactly this (`senData.active(sensorId, 1/0)`); "Start all" turns on every loop with
  `1.11 == 4` (available) and `1.16 == 1` (has an actuator) that is not being calibrated.
- Today on R2: `1.14` = 0 for every sensor. `1.16` = 1 for pH, dO2, T, stirrer; **0 for Level**
  (no actuator assigned: "Start all" would not start it).
- While a loop is on, its actuators cannot be driven manually (`manualcontrol.js` disables the
  controls when the assigned loop is active or the actuator is interlocked, `2.47 == 1`).
  Manual drive uses `2.20` (manualValue) and `2.46` (prime): **never written by FM2**.
- What FM2 can read during a run, per loop: `1.14` (on/off), `1.17` (controller output),
  `1.22`/`1.23`/`1.48` (alarm / interlock state), per actuator `2.12` (output), `2.49` (active),
  `2.47` (interlocked).

### Level / antifoam
| What | Address | R2 today |
|---|---|---|
| Level sensor | sensor **3** "Level" (short name **Foam**); sensor 4 "Level 2" unused | |
| Probe type / sensitivity | `5.3.0` (0 Level, 1 Foam) / `5.3.1` (0 Low, 1 High) | Foam / High |
| Value | `1.15.3`: `CONTACT` or `NO CONT` (labels `1.64.3` / `1.65.3`) | NO CONT |
| Control type | `1.19.3` = 3 (Level) | Level |
| Loop on/off | `1.14.3` | off |
| Level loop: pulse time / dead time / first action | `3.22.3` / `3.25.3` / `3.49.3` | 0 s / 0 s / pulse first |
| Antifoam pump | actuator **30** "Antifoam" (DigitalPump 3) | |
| Assigned to the Level loop? | `2.7.30` | **−1: not assigned** |
| Pump on / off / delay times | `2.24.30` / `2.25.30` / `2.23.30` (s) | 1 / 0 / 0 |
| Calibration / unit | `2.4.30` / `2.5.30` | 100 % (**not in mL/min**) |
| Dose counter | `2.13.30` (enabled: `2.3.30` = 1) | 0 |

So today the antifoam is **not wired into any loop**: the controller would only report the
foam contact. To use it, on the display (Engineer): assign Antifoam to the Level loop, set
pulse and dead time, calibrate the pump in mL/min (otherwise the counter is not in mL), then
start the Level loop.

### Addition 2
| What | Address | R2 today |
|---|---|---|
| Pump | actuator **28** "Addition 2" (DigitalPump 1) | |
| Assignment | `2.7.28` / `2.8.28` | pH loop, −1 = acid (above setpoint) |
| Dose monitoring | `2.3.28` | **0: disabled** |
| Calibration | `2.4.28` | **0.00: not calibrated** |
| Output / counter | `2.12.28` / `2.13.28` | 0 / 0 |

- **As pH-high (acid)**: already configured that way. For FM2 to record acid in mL the pump
  must be calibrated and dose monitoring enabled on the display; then FM2 reads `2.13.28`
  exactly like the base counter.
- **As a separate channel**: remove it from the pH loop on the display (`2.7.28` becomes −1).
  The controller has **no timer rule for a single pump**: timed or rule-based feeding exists
  only as a *Flow* loop (feed profile, `3.54`–`3.70`), and Flow is offered only on analog
  inputs and balances (sensors 5–12, 18–21, 37–44), i.e. it needs a balance or an analog
  signal, which we do not have; "Discontinuous Flow" is also not licensed (`4.4.1`/`4.5.1`).
  Without them, additions would be manual from the display, and FM2 would record them from the
  counter. Automating them from FM2 means FM2 driving an actuator: a deliberate decision
  outside the current rules (see "Still open").

### Availability, redox, dO2 and gas valves
- `1.11.<s>` / `2.21.<a>` (available): 0 absent · 1 present in firmware, **not installed**
  (Service → Install sensors/actuators) · 3 installed · 4 installed **and** selected as a control
  loop (sensors, System configuration → Control loop selection) or in use (actuators).
  R2: pH, dO2, T, Level, stirrer = 4; Analog/Digital In = 1; N2 and Air valve, Antifoam = 3;
  O2/CO2 valves and all MFCs = 1.
- **Redox = sensor 32**: `1.11.32` = 1 (not installed; licensed `1.39.32` = 1), unit empty,
  value 0.000, factory slope/offset 1.034/0.756. Installing it needs the Service level. As a
  non-digital sensor the controller would offer PID/Adaptive loops on it, but FM2 treats it as
  a read-only mV channel.
- **dO2 (sensor 1)**: loop PID (P 7, I 1500), off; setpoint `1.13.1` = **200 %** (odd: check
  before use); value 0.0 % (raw −0.1) with the probe connected: not polarised/calibrated.
  Its only actuator is the **stirrer in cascade**: `1.52.13` = 1, `1.53.13` = +1 (stirrer is
  sensor 13 used as the dO2 loop's actuator on the below-setpoint side).
- **Gas**: actuators 0 N2 Valve and 1 Air Valve (on/off, % = duty over `2.51` = 5 s), installed
  but in no loop; `2.10.1` (Air low limit) = 100 %: check before assigning. No MFCs.
- One actuator belongs to at most one loop (`2.7` is a single sensor index); a sensor
  (stirrer) can additionally drive another loop in cascade.

### Live confirmation (10 Nov 2026, 16:00, R2, watch mode at 1 s, Engineer on the display)
- **pH loop** start/stop → `1.14.0` 0↔1 within 1 s. With pH 6.87 above SP 6.20 the acid
  pump turned on at once: `2.49.28` (Addition 2 *active*) 0→1 together with the loop, back
  to 0 when the loop stopped. `2.49.<a>` = actuator currently on.
- **Addition 2 manual, ~1 s**: `2.49.28` 0→1→0; `2.12.28` and `2.13.28` stay 0 (not
  calibrated, dose monitoring off): its additions are invisible in mL until calibrated.
- **Alkali manual, ~1 s**: `2.12.29` 0→4.80→0, `2.49.29` 0→1→0, `2.13.29` 0.00→0.05→0.08→0.10
  over 3 s (the counter keeps catching up after the pump stops). **The counter read 0.00
  today, after 1.61 on 9 Nov**: it was reset between the two days (power cycle or reset on
  the display): FM2 must treat a counter drop as a reset, not as negative dosing.
- **Stirrer loop** start/stop → `1.14.13`; speed `1.15.13` and drive output `2.12.18` ramp
  0→35→65 rpm in 3 s and back to 0 after stop (the drive briefly reads −10).
- **Level**: a light touch of the two wires gave no change; pressing them firmly (16:11)
  → `1.15.3` `NO CONT` ↔ `CONTACT` within 1 s, twice. Confirmed.
- **dO2 loop** start (16:11:49) → `1.14.1` 0→1 **and `1.14.13` (stirrer loop) 0→1 in the same
  second**: starting a loop also starts the loop it drives in cascade; stopping dO2 stopped
  both. Stirrer ramped to 147 rpm in 6 s.
- **Temperature loop**, 28 s on (16:12:05–33) → heating `2.12.19` 0→100 % and `2.49.19` 0→1
  at once (22.6 °C vs SP 37), back to 0 at stop.
- No reassignment was done: `2.7`/`2.8` unchanged.

### R1-sx vs R2-dx (dump 10 Nov 2026, 16:07; same firmware and addresses)
Configuration differs; FM2 must read it per reactor, never assume:
| | R2-dx | R1-sx |
|---|---|---|
| dO2 control type `1.19.1` | PID | **Level** (on/off, pulse 30 s / dead time 30 s) |
| dO2 setpoint / alarm limits | 200 % / 40–200 | 70 % / 20–120 |
| dO2 calibration slope/offset | 13.998 / 0.0 | 11.315 / 1.2 |
| Level probe `5.3.0`/`5.3.1` | Foam / High | Level / Low |
| T loop | Adaptive | PID (P 25.2, I 3053.5, D 28.3) |
| Stirrer setpoint / alarm limits | 200 / 20–500 rpm | 400 / 200–900 rpm |
| Alkali factor | 4.80 mL/min | 4.86 mL/min |
| Dose monitoring stirrer / antifoam `2.3` | on / on | off / off |
| N2 valve factor / time factor / on time | 100 / 60 / 1 | 1 / 1 / 500 |
| Air valve factor / low limit | 100 / 100 | 0 / 0 |
| pH alarm high | 6.40 | 6.50 |
Same on both: actuator numbering (28 Addition 2 → pH acid side, not calibrated; 29 Alkali;
30 Antifoam not assigned), stirrer in cascade on dO2 (`1.52.13` = 1), redox not installed.

### Proposal for FM2
1. **Loop state on the live page and in alerts** (read only): `1.14` for pH, T, stirrer,
   dO2 and Level; an alert if a loop goes off during a run; `2.47` interlock per actuator.
2. **Channel "Level / antifoam"**: foam contact (`1.15.3`), level loop on/off, antifoam pump
   output/active (`2.12.30`, `2.49.30`) and counter (`2.13.30`, mL only once calibrated).
   Contacts shorter than the polling interval can be missed: the counter is the reliable
   quantity.
3. **Addition 2, mode read from the controller**: if `2.7.28 == 0` and `2.8.28 < 0` → acid
   inside the pH channel (base and acid mL side by side); if `2.7.28 == -1` → separate
   channel "Addition 2" (Feed role) with cumulative mL. In both cases FM2 warns when dose
   monitoring is off or the factor is 0 (the counter would not be in mL).

## Still open
- [ ] Step size of the base counter under automatic pH control (pulses every 10 s):
      visible at the first real fermentation.
- [x] Setpoint writes: confirmed live on R2-dx (pH SP).
- [x] pH control type / loop parameters from a profile: confirmed live on R2-dx (cycle time 10 → 15).
- [ ] Same checks on R1-sx (same firmware and addresses; not yet exercised).
- [ ] Never automate actuator start/stop, dose counter reset or calibration.
- [x] Loop on/off (`1.14`), actuator active (`2.49`), alkali output/counter: confirmed live on R2.
- [x] Level contact (`1.15.3`), dO2 loop (with stirrer cascade), heating output: confirmed live on R2.
- [ ] Antifoam pulse (`2.12.30`, `2.49.30`,
      `2.13.30`) once assigned; Addition 2 reassignment (`2.7.28`, `2.8.28`); cooling output.
- [ ] Dose counters reset between days (1.61 → 0.00): find out when (power cycle?).
- [x] Same dump on R1-sx (differences listed above).
- [x] Decision (Michele, 10 Nov): FM2 may start/stop loops (`1.14`), only those enabled in Settings
      (implemented in PR #23).
- [ ] Decision (user): whether timed Addition 2 doses driven by FM2 are wanted (outside the driver's rules).

### Manual output and prime (23 Nov 2026, R1 192.0.2.41, scripts/ez2_manual_test.py)
- Web UI (`Scripts/windows/manualcontrol.js`): on/off actuators (`2.6` = 1) get a "Start/Stop
  Actuator" toggle writing `2.20.<a>` 1/0; analogue ones (`2.6` = 0) a number 0…factor. "Click &
  Hold To Prime" writes `2.46.<a>` = 1 on press and 0 on release. The UI requires Operator and
  disables both while the assigned loop is on (`1.14.<2.7>`), during feed calibration or interlock.
- Alkali (29): `2.6` = 1, 4.80 mL/min. `2.46.29` 1/0 and `2.20.29` 1/0 both work with an
  **Operator (level 1)** login; `2.20` written as 1 reads back 4.80 (full output).
  Pump on within ~0.4 s, off within ~0.25 s. **The dose counter counts prime and manual**
  (+0.17 mL in 2.1 s), still catching up ~1 s after the stop.
- **No controller-side stop**: prime left at 1 ran ~41 s, after FM2's logout and with the
  WebSocket closed, until `2.46` = 0 was written. Same for `2.20`.
- **The controller does NOT enforce the loop interlock**: with the pH loop on (`1.14.0` = 1,
  `2.19.29` = 1) `2.46.29` = 1 was accepted and the alkali pump ran 2 s (acid probably running at
  the same time). Only the web UI blocks it: FM2 must check `1.14.<assigned sensor>`, `2.47` and
  `2.19` itself before any manual/prime write.
- Login refused while someone is logged in on the display: the login write answers `0.11` =
  `-5|0|`. Reading `0.11` before trying shows only this connection's state (`0|-1|`), not the
  display's: the refusal code is the only signal.

### Loop configuration, both controllers (23 Nov 2026, `docs/inspect-2026-11-23/R*-values.tsv`)
| Loop (sensor) | type `1.19` | P / I / D | deadband | cycle s | settling s | SP `1.13` R1 / R2 | alarm lo–hi |
|---|---|---|---|---|---|---|---|
| pH (0) | 0 PID | 100 / 2 / 10 | 0.02 | 10 | 300 | 6.20 / 6.20 | 6.00–6.40 |
| dO2 (1) | R1 0, **R2 3** | 7 / 1500 / 0 | 0 | 5 | 150 | 200 / 70 % | R1 40–200, R2 20–120 |
| Temperature (2) | 1 Adaptive | 30 / R1 3279.8, R2 3059.8 / R1 100, R2 14.7 | 0 | 5 | 1500 | 12 / 12 °C | 10–38 |
| Level (3) | 3 | 10 / 0 / 0 | 0 | 5 | 600 | 0 | — |
| Stirrer (13) | 2 | 10 / 0 / 0 | 0 | 5 | 600 | 80 / 80 rpm | 20–500 |
- R2 dO2 also has pulse time `3.22` = 30 and dead time `3.25` = 30 (R1: 0).
- Assigned actuators (both): Stirrer 18 → stirrer loop, Heating 19 → temperature, Alkali 29 → pH
  (below SP), Addition 2 28 → pH (above SP). **Antifoam 30, N2 0 and Air 1 are in no loop**
  (`1.16.3` = 0: the Level loop has no actuator); dO2 drives the stirrer in cascade.
- Display "Control Settings" (photos, R2 pH, 23 Nov): the four controller types and their fields.
  **PID** (`1.19` = 0): P, I, D, Dead Band, Cycle Time. **Adaptive** (1): the same plus Settling
  Time. **External** (2): asks for confirmation because it turns the positive actuators into
  "Setpoint Out" and the negative ones into "Process Value Out" (the loop no longer doses):
  **FM2 must never select it** (already excluded: only PID/Adaptive are writable). **Level** (3):
  Pulse Time, Dead Time, First Action (Pulse Time / Dead Time). Hence R2 dO2 (`1.19.1` = 3) is in
  Level mode with 30 s pulse/dead time; the stirrer's `1.19.13` = 2 needs checking on the
  display (the type list may differ for the stirrer).
- Temperature also offers **TCUSP** (`1.19` = 6): confirmation says it "will clear all above
  setpoint actuators from the loop". Most likely "Temperature Control Unit SetPoint" (the setpoint
  is sent to an external thermostat/circulator): inference, not documented. **Never selected by FM2.**
- Foam/Level sensor (display): only "Sensor Settings": probe type Level/Foam (`5.3.0`) and High/Low
  sensitivity (`5.3.1`); no control settings are shown while no actuator is assigned to it.
- Stirrer: the display shows **no Control Settings** (the stirrer's `1.19.13` = 2 is its own speed
  control, not selectable): FM2 treats the stirrer as setpoint + alarm limits only.
- "Limits" page of a loop (photo, R2 pH): **Process Limits** high/low (`1.4`/`1.5`, alarm only;
  Alarm State "Loop") and **Interlock Limits** high/low (`1.20`/`1.21`: pH 14.00 / 0.00, i.e.
  never reached), Dead Band 0.50, Interlock State Off, "Affected Actuators High/Low" (`1.25`/`1.26`,
  bit masks). An interlock switches off the affected actuators while the value is beyond the limit
  and releases them after the dead band. Not writable by FM2. **To verify**: whether an interlock
  also stops a prime/manual output (`manualcontrol.js` disables the UI when `2.47` = 1): if so, a pH
  interlock high on the alkali pump would be a controller-side safety net for a stuck manual command.
- Actuators are assigned to loops on another page of the display (actuator settings), not here.

### Loop configuration (actuator assignment) — `Scripts/windows/loopconfiguration.js` (read 23 Nov)
- Display: Menu › Configuration › Loop Configuration (photo R2: pH ↑Alkali ↓Addition 2, dO2 ↑Stirrer,
  Temperature ↑Heating ↓Cooling, Foam none). Web UI collects the edits and sends them on **Accept**:
  - add on the "above"/positive side: `J3.51.<sensor>s<actuator>`; negative side: `J3.52.<sensor>s<actuator>`
    (appended at the next free position, max 4 per side);
  - remove: `J3.53.<sensor>s<actuator>`;
  - a sensor used as actuator (cascade, e.g. stirrer for dO2) is sent as `actuator + 0x40000000`;
  - double-sided (polarity) actuators fill both sides at once.
- Allowed only with an **Engineer** login, loop off (`1.14` = 0) and no feed calibration (`3.58` = 0).
  Free actuators listed by the UI: `2.21` (available) = 3 and not a TGF carrier.
- Not written by FM2 yet: planned for 2.3.x (edit assignments / free an actuator such as Addition 2 for
  an FM2 rule) after a live test, with read-back of `2.7`/`2.8` and an event in the log.
- Actuator settings (photo R1 Alkali, "HMI Settings DigitalPump 2"): name, unit mL/min, type Pump, screen
  priority 28 (`2.2`), decimals; Advanced: Low/High Limit 0.00/4.86 (`2.10`/`2.9`: output range, high =
  calibration), Inhibit None (`2.29`), Interval 5 (`2.51`, period of the on/off duty cycle, s); Timer
  function Delay/On/Off time 0/1/0 s (`2.23`/`2.24`/`2.25`). Read-only for FM2. The high limit caps the
  loop's dosing rate; the timer probably makes pulse dosing (on/off cycles) — to verify before use.


### Written by FM2 from 2.3.0 (23 Nov 2026)
- Prime `2.46.<pump>` 0/1 (pumps 22–30, Operator login) for "hold to run", with FM2-side checks before
  switching on (`1.14.<2.7>`, `2.19`, `2.47`, `2.20`), keep-alive watchdog (1.5 s), 60 s limit, switch-off at
  start-up of what FM2 left on (`data/manual-outputs.json`). `2.20` (manual toggle) is still never written.
- Loop configuration `3.51/3.52/3.53.<sensor>` = actuator id (Engineer login, loop off, not while recording),
  read back from `2.7/2.8` and logged. **First live use to be checked on the display.**
- dO2 setpoint `1.13.1` and limits `1.4/1.5.1`, stirrer limits `1.4/1.5.13`, from the plan ("Check and load").
- Login answer `-5`: reported as "controller locked by a login on the display or web UI".
