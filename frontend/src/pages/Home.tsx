import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EventItem, type Experiment, type LiveReactor, type Status } from "../api";
import { usePolling, fmt, fmtDuration } from "../hooks";
import { phAndRateOption } from "../components/charts";
import { DataValue, EChart, Icon, StatusChip } from "../components/ui";
import logo from "../assets/logo.png";

/** Display names agreed for the lab: R1 is the left unit, R2 the right one. */
export const REACTOR_LABEL: Record<string, string> = { R1: "R1-sx", R2: "R2-dx" };
const label = (id: string) => REACTOR_LABEL[id] ?? id;

const outOf = (v: number | null | undefined, lo: number | null | undefined, hi: number | null | undefined) =>
  v != null && lo != null && hi != null && (v < lo || v > hi);

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
        <div className="dv-grid">
          <DataValue value={fmt(s?.ph)} label={`pH · set ${fmt(s?.ph_setpoint)}`}
            color={outOf(s?.ph, s?.ph_low_limit, s?.ph_high_limit) ? "#b30020" : "#1d3786"} />
          <DataValue value={fmt(s?.temperature, 1)} unit="°C" label={`T · set ${fmt(s?.temperature_setpoint, 1)}`}
            color={outOf(s?.temperature, s?.temperature_low_limit, s?.temperature_high_limit) ? "#b30020" : undefined} />
          <DataValue value={fmt(s?.stirrer, 0)} unit="rpm" label={`Stirrer · set ${fmt(s?.stirrer_setpoint, 0)}`} />
          <DataValue value={fmt(s?.base_cumulative_ml, 2)} unit="mL" label="Base counter (reset before Start)" color="#1f7a63" />
        </div>
      )}
      <div className="row between small muted">
        <span>last reading{" "}
          <span className="mono">{r.last_reading_age_s === null ? "—" : `${Math.round(r.last_reading_age_s)} s`}</span> ago</span>
        <Link className="btn tonal" to="/experiments/new">Plan experiment</Link>
      </div>
    </div>
  );
}

function RunningCard({ r, events }: { r: LiveReactor; events: EventItem[] }) {
  const exp = r.experiment!;
  const s = r.snapshot;
  const readings = usePolling(() => api.readings(exp.id), 30000, [exp.id]);
  const analysis = usePolling(() => api.analysis(exp.id, 0.25), 60000, [exp.id]);
  const option = useMemo(() => phAndRateOption(readings.data, analysis.data?.rate_bins), [readings.data, analysis.data]);
  const lastBin = analysis.data?.rate_bins.filter((b) => b.complete).at(-1);
  const alert = events.find((e) => e.experiment_id === exp.id && e.severity === "alert" &&
    Date.now() - new Date(e.at).getTime() < 30 * 60 * 1000);
  const tOut = s && s.temperature !== null && s.temperature_low_limit !== null && s.temperature_high_limit !== null &&
    (s.temperature < s.temperature_low_limit || s.temperature > s.temperature_high_limit);
  return (
    <div className="card">
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4 }}>
          <span className="kicker">Bioreactor {label(r.reactor_id)}{exp.parallel_run ? ` · parallel run ${exp.parallel_run}` : ""}</span>
          <span style={{ fontSize: 18, fontWeight: 500 }}><span className="mono">{exp.code}</span> · <i>L. plantarum</i> {exp.strain || "—"}</span>
        </div>
        <StatusChip status="fermenting" />
      </div>
      <div className="dv-grid">
        <DataValue value={fmt(s?.ph)} label={`pH · set ${fmt(s?.ph_setpoint)}`} color="#1d3786" />
        <DataValue value={fmt(s?.temperature, 1)} unit="°C" label={`T · set ${fmt(s?.temperature_setpoint, 1)}`} color={tOut ? "#b30020" : undefined} />
        <DataValue value={fmt(s?.base_cumulative_ml, 1)} unit="mL" label="Base, cumulative" color="#1f7a63" />
        <DataValue value={fmt(lastBin?.rate_ml_min, 3)} unit="mL/min" label="Base rate, last 15 min" color="#1f7a63" />
      </div>
      <EChart option={option} className="chart mini" ariaLabel={`${r.reactor_id}: pH with setpoint and process limits, base rate bars`} />
      {alert && (
        <div className="banner alert" role="alert"><Icon name="bell" color="#b30020" size={20} /><span>{alert.message}</span></div>
      )}
      <div className="row between small muted">
        <span>t = <span className="mono" style={{ color: "#22294d" }}>{fmtDuration(exp.t_h)}</span> · last reading{" "}
          <span className="mono">{r.last_reading_age_s === null ? "—" : `${Math.round(r.last_reading_age_s)} s`}</span> ago</span>
        <Link className="btn tonal" to={`/experiments/${exp.id}`}>Open</Link>
      </div>
    </div>
  );
}

const FILTERS: { key: Status | "all"; label: string }[] = [
  { key: "all", label: "All" }, { key: "planned", label: "Planned" }, { key: "fermenting", label: "Fermenting" },
  { key: "completed", label: "Completed" }, { key: "aborted", label: "Aborted" },
];

export function Home() {
  const live = usePolling(api.live, 10000);
  const exps = usePolling(() => api.experiments(), 30000);
  const events = usePolling(api.recentEvents, 20000);
  const [filter, setFilter] = useState<Status | "all">("all");
  const [q, setQ] = useState("");
  const nav = useNavigate();

  const reactors = live.data ?? [];
  const running = reactors.filter((r) => r.experiment);
  const list = (exps.data ?? []).filter((e) => (filter === "all" || e.status === filter) &&
    (!q || `${e.code} ${e.name} ${e.strain}`.toLowerCase().includes(q.toLowerCase())));
  const count = (k: Status | "all") => (exps.data ?? []).filter((e) => k === "all" || e.status === k).length;

  return (
    <>
      <header className="row between">
        <h1 style={{ lineHeight: 0 }}><img src={logo} alt="Fermentation Manager 2.0" style={{ height: 64, width: "auto" }} /></h1>
        <div className="row" style={{ gap: 8 }}>
          {(live.data ?? []).map((r) => (
            <span key={r.reactor_id} className="pill" title={r.error ?? ""}>
              <span className={`dot ${r.online ? "ok" : "bad"}`} />Controller {label(r.reactor_id)}
            </span>
          ))}
          {live.error && <span className="pill"><span className="dot bad" />Server not reachable</span>}
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
          <h2 id="exp-title">Experiments</h2>
          <label className="field" style={{ minWidth: 280 }}>
            <span className="sr-only">Search experiments</span>
            <input type="search" placeholder="Search by code, name or strain" value={q} onChange={(e) => setQ(e.target.value)}
              style={{ borderRadius: 24, background: "#eef0f4", border: 0 }} />
          </label>
        </div>
        <div className="row" role="group" aria-label="Filter by status" style={{ gap: 8 }}>
          {FILTERS.map((f) => (
            <button key={f.key} className={`chip ${filter === f.key ? "on" : ""}`} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
              {filter === f.key && <Icon name="check" size={16} width={2.2} />}{f.label} · {count(f.key)}
            </button>
          ))}
        </div>
        <div className="card" style={{ padding: 0 }}>
          <div className="table-wrap">
            <table className="data" style={{ minWidth: 900 }}>
              <thead><tr><th>Date</th><th>Experiment</th><th>Strain</th><th>Reactor</th><th>Parallel run</th><th>Status</th><th>Last modified</th></tr></thead>
              <tbody>
                {list.map((e: Experiment) => (
                  <tr key={e.id} className="click" onClick={() => nav(`/experiments/${e.id}`)}>
                    <td className="mono">{e.planned_date ?? "—"}</td>
                    <td><span className="mono" style={{ fontWeight: 600 }}>{e.code}</span> · {e.name}</td>
                    <td className="mono">{e.strain || "—"}</td>
                    <td className="mono">{e.reactor_id}</td>
                    <td className="mono muted">{e.parallel_run_code ?? "—"}</td>
                    <td><StatusChip status={e.status} /></td>
                    <td className="mono muted">{new Date(e.updated_at).toLocaleString()}</td>
                  </tr>
                ))}
                {list.length === 0 && <tr><td colSpan={7} className="muted">No experiments.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </>
  );
}
