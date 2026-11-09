# ez2-Control inspection checklist (to do on the bioreactor PC)

Goal: find out how to read (and later write) the data the software needs, before
writing `backend/fm2/drivers/ez2control.py`.

## 0. Before touching anything
- [ ] Check the ez2-Control manual for a documented interface (OPC UA, Modbus TCP, REST,
      data export). A documented interface beats reverse-engineering the web UI.
- [ ] Note firmware version and controller IPs (R1, R2).
- [ ] Do the inspection with no culture running, or read-only.

## 1. Reading (web UI)
Open the controller web UI in Chrome → DevTools → Network (filter Fetch/XHR):
- [ ] Is the page refreshed by periodic requests? URL, method, response format (JSON/XML/HTML)?
- [ ] Is authentication needed (cookie, token, basic auth)?
- [ ] Map each field we need to a request/field:

| Loop | Field | Found? | Where / name | Units |
|---|---|---|---|---|
| pH | value, setpoint | | | |
| pH | process limits low/high | | | |
| pH | control mode (Adaptive/PID) | | | |
| pH | PID parameters (names!) | | | |
| pH | base cumulative (dose monitoring) | | | mL |
| pH | base pump activation | | | % |
| Temperature | value, setpoint, limits | | | °C |
| Stirrer | value, setpoint | | | rpm |

- [ ] Volume per dose (from the cumulative steps): needed to judge 15-min rate resolution.
- [ ] Does opening one controller's IP really show both? Which one serves the data?

## 2. Writing (only after reading works)
- [ ] Change a setpoint from the web UI and capture the request (method, payload).
- [ ] Same for pH control mode and PID parameters.
- [ ] Confirm the change is visible on the controller display and by reading back.
- [ ] Never automate actuator start/stop, dose counter reset or calibration.
