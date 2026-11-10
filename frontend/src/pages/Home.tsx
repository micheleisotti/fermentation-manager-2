import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EventItem, type Experiment, type LiveReactor, type Status } from "../api";
import { ExperimentTable } from "./ExperimentsList";
import { usePolling, fmt, fmtDuration } from "../hooks";
import { phAndRateOption, trendsOption, TREND_VARS } from "../components/charts";
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
        <Link className="btn tonal" to={`/experiments/new?reactor=${r.reactor_id}`}>Plan on {label(r.reactor_id)}</Link>
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
  const option = useMemo(() => {
    if (ch.includes("ph")) return phAndRateOption(readings.data, analysis.data?.rate_bins);
    const first = TREND_VARS.find((v) => v.channel && ch.includes(v.channel) && ["temperature", "stirrer", "do2"].includes(v.key));
    return first ? trendsOption(readings.data, first, null, { setpoints: true, limits: true, compact: true }) : {};
  }, [readings.data, analysis.data, ch.join()]);
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
          <span style={{ fontSize: 18, fontWeight: 500 }}><span className="mono">{exp.code}</span> · {exp.strain || "—"}</span>
        </div>
        <StatusChip status={exp.status ?? "fermenting"} />
      </div>
      <div className="dv-grid">
        {[
          ch.includes("ph") && <DataValue key="ph" value={fmt(s?.ph)} label={`pH · set ${fmt(s?.ph_setpoint)}`} color="#1d3786" />,
          ch.includes("temperature") && <DataValue key="t" value={fmt(s?.temperature, 1)} unit="°C" label={`T · set ${fmt(s?.temperature_setpoint, 1)}`} color={tOut ? "#b30020" : undefined} />,
          ch.includes("ph") && <DataValue key="cum" value={fmt(s?.base_cumulative_ml, 1)} unit="mL" label="Base, cumulative" color="#1f7a63" />,
          ch.includes("ph") && <DataValue key="rate" value={fmt(lastBin?.rate_ml_min, 3)} unit="mL/min" label="Base rate, last 15 min" color="#1f7a63" />,
          ch.includes("do2") && <DataValue key="do2" value={fmt(s?.extra?.do2, 1)} unit="%" label={`dO2 · set ${fmt(s?.extra?.do2_setpoint, 1)}`} />,
          ch.includes("stirrer") && <DataValue key="st" value={fmt(s?.stirrer, 0)} unit="rpm" label={`Stirrer · set ${fmt(s?.stirrer_setpoint, 0)}`} />,
        ].filter(Boolean).slice(0, 4)}
      </div>
      <EChart option={option} className="chart mini" ariaLabel={`${r.reactor_id}: main channel since start`} />
      {alert && (
        <div className="banner alert" role="alert"><Icon name="bell" color="#b30020" size={20} /><span>{alert.message}</span></div>
      )}
      <div className="row between small muted">
        <span>{exp.status === "preparing" ? <>Not recording · t = —</> : <>t = <span className="mono" style={{ color: "var(--ink)" }}>{fmtDuration(exp.t_h)}</span></>} · last reading{" "}
          <span className="mono">{r.last_reading_age_s === null ? "—" : `${Math.round(r.last_reading_age_s)} s`}</span> ago</span>
        <Link className="btn tonal" to={`/experiments/${exp.id}`}>Open</Link>
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
