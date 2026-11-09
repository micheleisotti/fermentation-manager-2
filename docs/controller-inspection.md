# ez2-Control — inspection results (9 Nov 2026)

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
  - write: `J<address>s<value>\r` (lowercase `s`) — **not used yet**.
  - The controller **drops idle connections after a few seconds without a close frame**.
    The driver reconnects and retries once (cost ≈ 50–200 ms).
- **HTTP history** `/Data/history.csv?<sensor>|<divider>`: ~500 lines
  `value|alarm high|alarm low|setpoint|output`, no timestamps. Not used (could backfill).

## Address map (sensor index: pH 0, dO2 1, T 2, stirrer 13; base pump = actuator 29)

| Quantity | Address | Notes |
|---|---|---|
| Process value | `1.15.<s>` | `NO CONT` etc. → none |
| Setpoint | `1.13.<s>` | |
| Process (alarm) limit high / low | `1.4.<s>` / `1.5.<s>` | shown on charts, used by alerts |
| Loop active | `1.14.<s>` | |
| Control type | `1.19.<s>` | 0 PID, 1 Adaptive, 2 External, 3 Level, 4 Flow, 5 P-Adaptive, 6 TCUSP |
| PID P / I / D | `3.0.<s>` / `3.3.<s>` / `3.6.<s>` | |
| Loop interval / deadband | `3.12.<s>` / `3.9.<s>` | pH: 10 s / 0.10 |
| Actuator name | `2.1.<a>` | 29 = "Alkali" |
| Actuator assignment / side | `2.7.<a>` / `2.8.<a>` | Alkali: sensor 0, +1 (below SP); Addition 2: sensor 0, −1 (acid, not calibrated) |
| Actuator output (flow) | `2.12.<a>` | mL/min for pumps |
| **Dose monitoring, cumulative** | `2.13.<a>` | **mL** |
| Dose factor (calibration) | `2.4.<a>` | Alkali: 4.86 mL/min (R1), 4.80 (R2) |

Verified live on R2: Alkali on manually for ~20 s → `2.13.29` 0.00 → 1.61 mL
(0.08 mL/s = 4.80 mL/min), `2.12.29` = 4.80 while on, 0 after. The controller display
briefly showed 1.56 and then 1.61: display refresh lag, the network value is the true one.

Pump activation % = `2.12.29 / 2.4.29 × 100`.

## Differences between the units (read, not configured by FM2)
- Temperature loop: R2 Adaptive, R1 PID (P 25.2 / I 3053.5 / D 28.3).
- Stirrer alarm limits: R2 20–500 rpm, R1 200–900 rpm.
- Controller clocks differ by ~1 min → FM2 timestamps use the PC clock.

## Still open
- [ ] Step size of the base counter under automatic pH control (pulses every 10 s):
      visible at the first real fermentation.
- [ ] Writes (setpoints, then pH control mode/PID from planning): capture the web UI's
      `s` requests, confirm on the display and by read-back, one at a time with Michele.
- [ ] Never automate actuator start/stop, dose counter reset or calibration.
