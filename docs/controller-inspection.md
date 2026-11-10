# ez2-Control — inspection results (9–10 Nov 2026)

Inspected read-only from the bioreactor PC through the controllers' own web UI
(page scripts and live reads). Both units run firmware **mE.2.9.2.4** and expose the
**same addresses**, so one driver serves both.

| Name in FM2 | Controller name | IP |
|---|---|---|
| R1-sx | Bioreactor 1 sx | 192.0.2.42 |
| R2-dx | Bioreactor 2 dx | 192.0.2.41 |

Network: the controllers sit on a separate switch, subnet 192.0.2.0/24. The PC's
Ethernet adapter has the static address 192.0.2.10/24 **without a gateway**; Wi-Fi
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
| Probe type / sensitivity | `5.3.0` (0 Level, 1 Foam) / `5.3.1` (0 Low, 1 High) | Foam / Low |
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
- [ ] Confirm the addresses above live (watch mode while toggling on the display): loop on/off
      (`1.14`), Level contact (`1.15.3`), antifoam pulse (`2.12.30`, `2.49.30`, `2.13.30`),
      Addition 2 reassignment (`2.7.28`, `2.8.28`).
- [ ] Same dump on R1-sx.
- [ ] Decision (user): whether FM2 may ever start/stop loops (`1.14`) and whether timed
      Addition 2 doses driven by FM2 are wanted; today both are outside the driver's rules.
