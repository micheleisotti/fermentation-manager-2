import { useMemo, useState, type ReactElement } from "react";
import { Link } from "react-router-dom";
import { api, type Channel, type EventItem, type LiveReactor, type Snapshot } from "../api";
import { ExperimentTable } from "./ExperimentsList";
import { usePolling, fmt, fmtDuration } from "../hooks";
import { phAndRateOption, trendsOption, varsFor } from "../components/charts";
import { DataValue, EChart, Icon, StatusChip } from "../components/ui";
import logo from "../assets/logo.png";

/** Display names agreed for the lab: R1 is the left unit, R2 the right one. */
export const REACTOR_LABEL: Record<string, string> = { R1: "R1-sx", R2: "R2-dx" };
const label = (id: string) => REACTOR_LABEL[id] ?? id;

const outOf = (v: number | null | undefined, lo: number | null | undefined, hi: number | null | undefined) =>
  v != null && lo != null && hi != null && (v < lo || v > hi);

/** Live values of a reactor: those of the experiment's channels, or every channel the controller reports. */
function liveValues(s: Snapshot | null | undefined, ch: Channel[] | null, extra: { baseRate?: number | null; acidRate?: number | null } = {}) {
  const x = s?.extra ?? {};
  const on = (c: Channel, reported: boolean) => (ch ? ch.includes(c) : reported);
  const out: ReactElement[] = [];
  if (on("ph", s?.ph != null)) {
    out.push(<DataValue key="ph" value={fmt(s?.ph)} label={`pH · set ${fmt(s?.ph_setpoint)}`}
      color={outOf(s?.ph, s?.ph_low_limit, s?.ph_high_limit) ? "#b30020" : "#1d3786"} />);
  }
  if (on("temperature", s?.temperature != null))
    out.push(<DataValue key="t" value={fmt(s?.temperature, 1)} unit="°C" label={`T · set ${fmt(s?.temperature_setpoint, 1)}`}
      color={outOf(s?.temperature, s?.temperature_low_limit, s?.temperature_high_limit) ? "#b30020" : undefined} />);
  if (on("stirrer", s?.stirrer != null))
    out.push(<DataValue key="st" value={fmt(s?.stirrer, 0)} unit="rpm" label={`Stirrer · set ${fmt(s?.stirrer_setpoint, 0)}`} />);
  if (on("ph", s?.ph != null)) {
    out.push(<DataValue key="cum" value={fmt(s?.base_cumulative_ml, ch ? 1 : 2)} unit="mL" label={ch ? "Base, cumulative" : "Base counter"} color="#1f7a63" />);
    if (ch) out.push(<DataValue key="rate" value={fmt(extra.baseRate, 3)} unit="mL/min" label="Base rate, last 15 min" color="#1f7a63" />);
    if (x.acid_cumulative_ml != null) {
      out.push(<DataValue key="acum" value={fmt(x.acid_cumulative_ml, ch ? 1 : 2)} unit="mL" label={ch ? "Addition 2, cumulative" : "Addition 2 counter"} />);
      if (ch) out.push(<DataValue key="arate" value={fmt(extra.acidRate, 3)} unit="mL/min" label="Addition 2 rate, last 15 min" />);
    }
  }
  if (on("do2", x.do2 != null)) {
    out.push(<DataValue key="do2" value={fmt(x.do2, 1)} unit="%" label={`dO2 · set ${fmt(x.do2_setpoint, 1)}`} />);
    out.push(<DataValue key="gas" value={`${fmt(x.n2_pct, 0)} / ${fmt(x.air_pct, 0)}`} unit="%" label="N2 / air valves" />);
  }
  if (on("level", false) && x.level_contact != null)
    out.push(<DataValue key="lvl" value={x.level_contact ? "Contact" : "No contact"} label={`Level probe${x.antifoam_cumulative_ml != null ? ` · antifoam ${fmt(x.antifoam_cumulative_ml, 1)} mL` : ""}`} />);
  if (on("redox", false) && x.redox_mv != null)
    out.push(<DataValue key="rdx" value={fmt(x.redox_mv, 0)} unit="mV" label="Redox" />);
  return out;
}

/** Preview chart of a running card: chosen per reactor, remembered in this browser. */
const PREVIEW_KEY = "fm2.homePreview";
function loadPreview(reactor: string): string | undefined {
  try { return (JSON.parse(localStorage.getItem(PREVIEW_KEY) ?? "{}") as Record<string, string>)[reactor]; } catch { return undefined; }
}
function savePreview(reactor: string, key: string) {
  try {
    const all = JSON.parse(localStorage.getItem(PREVIEW_KEY) ?? "{}") as Record<string, string>;
    localStorage.setItem(PREVIEW_KEY, JSON.stringify({ ...all, [reactor]: key }));
  } catch { /* storage unavailable */ }
}

/** A reactor with no experiment recording: live values only, nothing is stored. */
function IdleCard({ r }: { r: LiveReactor }) {
  const s = r.snapshot;
  return (
    <div className="card">
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4 }}>
          <span className="kicker">Bioreactor {label(r.reactor_id)}</span>
          <span style={{ fontSize: 18, fontWeight: 500 }}>No experiment</span>
        </div>
        <span className="pill" title="Live values from the controller; nothing is recorded until Start">
          <span className="dot idle" />Idle · not recording
        </span>
      </div>
      {!r.online ? (
        <div className="banner alert" role="alert">
          <Icon name="bell" color="#b30020" size={20} />
          <span>Controller not reachable{r.error ? `: ${r.error}` : ""}</span>
        </div>
      ) : (
        <div className="dv-grid">{liveValues(s, null)}</div>
      )}
      <div className="row between small muted">
        <span>last reading{" "}
          <span className="mono">{r.last_reading_age_s === null ? "—" : `${Math.round(r.last_reading_age_s)} s`}</span> ago</span>
        <span className="row" style={{ gap: 8 }}>
          <Link className="btn text" to={`/experiments/new?reactor=${r.reactor_id}`}>Plan</Link>
          <Link className="btn tonal" to={`/reactors/${r.reactor_id}`}>Open</Link>
        </span>
      </div>
    </div>
  );
}

function RunningCard({ r, events }: { r: LiveReactor; events: EventItem[] }) {
  const exp = r.experiment!;
  const s = r.snapshot;
  const readings = usePolling(() => api.readings(exp.id), 30000, [exp.id]);
  const analysis = usePolling(() => api.analysis(exp.id, 0.25), 60000, [exp.id]);
  const ch = exp.channels ?? ["temperature", "ph", "stirrer"];
  const vars = varsFor(ch);
  const choices = [...(ch.includes("ph") ? [{ key: "ph_rate", label: "pH + base rate" }] : []), ...vars.map((v) => ({ key: v.key, label: v.label }))];
  const [preview, setPreview] = useState(() => {
    const k = loadPreview(r.reactor_id);
    return choices.some((c) => c.key === k) ? k! : choices[0]?.key ?? "";
  });
  const option = useMemo(() => {
    if (preview === "ph_rate") return phAndRateOption(readings.data, analysis.data?.rate_bins, undefined, analysis.data?.estimated_end?.t_end_h);
    const v = vars.find((x) => x.key === preview);
    return v ? trendsOption(readings.data, v, null, { setpoints: true, limits: true, compact: true, bins: analysis.data?.rate_bins, endH: analysis.data?.estimated_end?.t_end_h }) : {};
  }, [readings.data, analysis.data, preview, ch.join()]);
  const lastBin = analysis.data?.rate_bins.filter((b) => b.complete).at(-1);
  const lastAcid = analysis.data?.acid_rate_bins?.filter((b) => b.complete).at(-1);
  const alert = events.find((e) => e.experiment_id === exp.id && e.severity === "alert" &&
    Date.now() - new Date(e.at).getTime() < 30 * 60 * 1000);
  return (
    <div className="card">
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4 }}>
          <span className="kicker">Bioreactor {label(r.reactor_id)}{exp.parallel_run ? ` · parallel run ${exp.parallel_run}` : ""}</span>
          <span style={{ fontSize: 18, fontWeight: 500 }}><span className="mono">{exp.code}</span> · {exp.strain || "—"}</span>
        </div>
        <StatusChip status={exp.status ?? "fermenting"} />
      </div>
      <div className="dv-grid">{liveValues(s, ch, { baseRate: lastBin?.rate_ml_min, acidRate: lastAcid?.rate_ml_min })}</div>
      <div className="row between" style={{ gap: 8 }}>
        <span className="xs muted">Preview</span>
        <select aria-label="Preview chart" value={preview} style={{ height: 32, width: "auto", fontSize: 13 }}
          onChange={(x) => { setPreview(x.target.value); savePreview(r.reactor_id, x.target.value); }}>
          {choices.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
      </div>
      <EChart option={option} className="chart mini" ariaLabel={`${r.reactor_id}: ${choices.find((c) => c.key === preview)?.label ?? ""} since start`} />
      {alert && (
        <div className="banner alert" role="alert"><Icon name="bell" color="#b30020" size={20} /><span>{alert.message}</span></div>
      )}
      <div className="row between small muted">
        <span>{exp.status === "preparing" ? <>Not recording · t = —</> : <>t = <span className="mono" style={{ color: "var(--ink)" }}>{fmtDuration(exp.t_h)}</span></>} · last reading{" "}
          <span className="mono">{r.last_reading_age_s === null ? "—" : `${Math.round(r.last_reading_age_s)} s`}</span> ago</span>
        <span className="row" style={{ gap: 8 }}>
          <Link className="btn text" to={`/reactors/${r.reactor_id}`}>Controller</Link>
          <Link className="btn tonal" to={`/experiments/${exp.id}`}>Open</Link>
        </span>
      </div>
    </div>
  );
}


const fmtUptime = (s: number) => (s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${(s / 3600).toFixed(1)} h` : `${(s / 86400).toFixed(1)} d`);

export function Home() {
  const live = usePolling(api.live, 10000);
  const sys = usePolling(api.systemStatus, 30000);
  const exps = usePolling(() => api.experiments(), 30000);
  const events = usePolling(api.recentEvents, 20000);

  const reactors = live.data ?? [];
  const running = reactors.filter((r) => r.experiment);
  // Planned and recording first, then the most recent: 10 rows, archived hidden
  const rank = (e: { status: string }) => (e.status === "fermenting" || e.status === "preparing" ? 0 : e.status === "planned" ? 1 : 2);
  const last = (exps.data ?? []).filter((e) => !e.archived)
    .sort((x, y) => rank(x) - rank(y) || (y.started_at ?? y.created_at ?? "").localeCompare(x.started_at ?? x.created_at ?? ""))
    .slice(0, 10);

  return (
    <>
      <header className="row between">
        <h1 style={{ lineHeight: 0 }}><img className="logo-img" src={logo} alt="Fermentation Manager 2.0" style={{ height: 64, width: "auto" }} /></h1>
        <div className="row" style={{ gap: 8 }}>
          {(live.data ?? []).map((r) => (
            <span key={r.reactor_id} className="pill" title={live.error ? "Unknown: no connection to the lab PC" : r.error ?? ""}>
              <span className={`dot ${live.error ? "" : r.online ? "ok" : "bad"}`} />Controller {label(r.reactor_id)}
            </span>
          ))}
          {live.error || sys.error
            ? <span className="pill"><span className="dot bad" />Server not reachable</span>
            : sys.data && <span className="pill" title={`${sys.data.server.host} · v${sys.data.server.version} · up ${fmtUptime(sys.data.server.uptime_s)}`}>
                <span className="dot ok" />Server</span>}
          {sys.data && (
            <span className="pill" title={sys.data.remote.state === "available"
              ? `${sys.data.remote.name ?? ""}${sys.data.remote.devices_online != null ? ` · ${sys.data.remote.devices_online} device(s) online` : ""}`
              : sys.data.remote.detail ?? sys.data.remote.backend_state ?? ""}>
              <span className={`dot ${sys.data.remote.state === "available" ? "ok" : sys.data.remote.state === "unknown" ? "idle" : "bad"}`} />
              Remote access{sys.data.remote.state === "available" ? "" : sys.data.remote.state === "unknown" ? " · unknown" : " · off"}
            </span>
          )}
        </div>
      </header>

      <section className="stack" aria-labelledby="running">
        <h2 id="running">Bioreactors</h2>
        {reactors.length === 0 ? (
          <div className="card"><span className="muted">Waiting for the server…</span></div>
        ) : (
          <div className="grid-cards">
            {reactors.map((r) => r.experiment
              ? <RunningCard key={r.reactor_id} r={r} events={events.data ?? []} />
              : <IdleCard key={r.reactor_id} r={r} />)}
          </div>
        )}
        {running.length === 0 && reactors.length > 0 &&
          <span className="small muted">No experiment is recording. Plan one with <b>New → Experiment</b>.</span>}
      </section>

      <section className="stack" aria-labelledby="exp-title">
        <div className="row between">
          <h2 id="exp-title">Last experiments</h2>
          <Link className="btn text" to="/experiments">All experiments ({(exps.data ?? []).length}) →</Link>
        </div>
        <ExperimentTable list={last} empty="No experiments yet: plan one with New → Experiment." />
      </section>
    </>
  );
}
