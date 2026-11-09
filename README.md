# Fermentation Manager 2

Monitoring, control and analysis of lab-scale batch fermentations run on two
Applikon **ez2-Control** bioreactor controllers.

It records controller data automatically, collects manual measurements (OD600,
AFU, riboflavin, …) from the PC or a phone, sends alerts, lets you change
setpoints remotely with confirmation and read-back, and analyses single
experiments and groups of replicates/conditions.

> Status: **backend under development**. The controller driver currently runs on a
> **simulator**; the real ez2-Control driver will be written on the bioreactor PC
> once we have inspected what the controllers expose. The web interface (React)
> follows the approved mockups and comes next.

## Architecture

```
          phone / laptop (via private VPN, e.g. Tailscale)
                          │  HTTP
┌─────────────────────────▼──────────────────────────┐
│  fm2 service (one process, starts with Windows)     │
│  ├── FastAPI REST API  (/api/...)                   │
│  ├── acquisition thread: polls controllers every 30 s│
│  ├── alert engine → events / notifications           │
│  └── analysis: Gompertz, base dosing, statistics     │
│            │                       │                 │
│      SQLite (WAL)          ControllerDriver          │
│   data/fermentation.db     ├── simulator (now)       │
│                            └── ez2-Control (to do)   │
└─────────────────────────────────────────────────────┘
```

Design rules (agreed during the mockup phase):

- **Time is always hours since Start** (when recording begins).
- An **experiment** is a standalone unit. A **parallel run** only links two
  experiments for live monitoring; it never defines conditions or replicates.
- **Conditions and replicates** are assigned per **analysis session**.
- **Controller data is immutable**. Manual entries are editable and every edit is logged.
- **Setpoints** (pH, T, stirrer) can be changed anytime, including remotely, within
  allowed ranges and a maximum step, always with read-back. **pH control mode and PID**
  are loaded from a profile **only at planning**. Actuators stay controller-only.
- The base cumulative volume is used as reported by the controller (dose monitoring
  calibration); rates are computed from it per 15-min / 1-h bin, without smoothing.

## Alerts

| Rule | Behaviour |
|---|---|
| Out of process limits | Immediately if already outside at Start; otherwise after 3 min outside. One per excursion, re-armed after 10 min back inside. |
| Setpoint reached (pH, T) | Within ±0.05 pH / ±0.5 °C for 2 min. Once per setpoint (dosing peaks don't re-trigger); re-armed when the setpoint changes. "Already at setpoint" if so at Start. |
| Controller silent | No reading for 5 min; "restored" when readings resume. |
| Setpoint changed on the controller | A change not made from this software. |
| Fermentation probably finished | Base rate below 10 % of its peak for 30 min. |

## Development

Requires Python ≥ 3.11.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                              # run the test suite
fm2-server                          # API on http://localhost:8000, docs at /docs
```

Configuration is via environment variables (see `backend/fm2/config.py`):
`FM2_DATA_DIR`, `FM2_SAMPLING_INTERVAL_S`, `FM2_R1_DRIVER` / `FM2_R2_DRIVER`
(`simulator` | `ez2control`), `FM2_R1_ADDRESS` / `FM2_R2_ADDRESS`, `FM2_PORT`.

### Web interface

```bash
cd frontend && npm install && npm run build   # build once; fm2-server then serves it at http://localhost:8000
npm run dev                                   # or: live-reload dev server on :5173 (proxies /api to :8000)
```

### Demo with simulated reactors

```bash
FM2_DATA_DIR=/tmp/fm2demo FM2_SIM_SPEED=300 FM2_SAMPLING_INTERVAL_S=2 fm2-server
python scripts/demo_seed.py      # two experiments on R1/R2 as a parallel run, started
```

`FM2_SIM_SPEED=300` runs simulated time 300× faster (one real minute ≈ 5 h of culture);
the API docs are at `/docs`.

## Repository layout

```
backend/fm2/
  config.py          settings (env vars), allowed setpoint ranges, alert parameters
  models.py          database schema
  drivers/           controller interface, simulator, ez2-Control (stub)
  acquisition.py     polling loop, readings, alert dispatch
  alerts.py          alert rules (pure logic, unit-tested)
  services/commands.py  safe setpoint writes with read-back
  analysis/          kinetics (from v1), base dosing, statistics
  api/               FastAPI app and schemas
backend/tests/       pytest suite (simulator-driven end-to-end test included)
frontend/            React + TypeScript interface (next step)
docs/                notes, including the controller inspection checklist
```

## Roadmap

1. ✅ Backend core: data model, simulator, acquisition, alerts, setpoint commands, analysis, XLSX export
2. Web interface (React + TypeScript, Material 3 styling with the project palette), installable on phones
   — in progress: Home and Fermentation screens done; Planning, Analysis, Settings next
3. Push notifications (app push / ntfy / Telegram)
4. ez2-Control driver on the bioreactor PC (read first, then writes)
5. Deployment: Windows service at boot, Tailscale remote access, automatic backups

## Credits

Developed by Michele Isotti — DiSAFA, University of Turin · DeFENS, University of Milan.
Kinetic analysis ported from Fermentation Manager v1.

## License

To be decided before the first public release (see discussion: CC licences are not
designed for software; options include PolyForm Noncommercial, GPL/AGPL or MIT).
