import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type LoopConfigView, type ReactorHistory, type Readings } from "../api";
import { fmtDuration, usePolling, fmtAge } from "../hooks";
import { EChart, Icon, StatusChip } from "../components/ui";
import { ControllerPanel, LoopsButton, ResetCountersButton } from "../components/ReactorControls";
import { TREND_VARS, trendsOption } from "../components/charts";
import { reactorName } from "../components/Shell";

/** In-memory history (last 3 h) as columnar readings, t in hours before now (negative). */
function historyReadings(h: ReactorHistory | undefined): Readings | undefined {
  if (!h || h.points.length === 0) return undefined;
  const keys = Array.from(new Set(h.points.flatMap((p) => Object.keys(p)))).filter((k) => k !== "ts");
  const now = Date.now();
  return { columns: ["t_h", ...keys],
    rows: h.points.map((p) => [(new Date(p.ts).getTime() - now) / 3.6e6, ...keys.map((k) => (typeof p[k] === "number" ? p[k] as number : null))]) };
}

/** Trends of a bioreactor without a recording experiment: last 3 hours, kept only in the server's memory. */
function IdleTrends({ reactor }: { reactor: string }) {
  const hist = usePolling(() => api.reactorHistory(reactor), 15000, [reactor]);
  const rd = historyReadings(hist.data);
  const avail = TREND_VARS.filter((v) => rd?.rows.some((row) => row[rd.columns.indexOf(v.key)] != null));
  const [key, setKey] = useState("ph");
  const v = avail.find((x) => x.key === key) ?? avail[0];
  const option = useMemo(() => (v ? trendsOption(rd, v, null, { setpoints: true, limits: true, xRange: { min: -3, max: 0 } }) : {}), [hist.data, v?.key]);
  return (
    <section className="card tight" aria-labelledby="idle-trends">
      <div className="row between" style={{ padding: "0 8px" }}>
        <h2 id="idle-trends" style={{ fontSize: 18 }}>Trends · last 3 h</h2>
        {avail.length > 0 && (
          <select aria-label="Variable" value={v?.key} onChange={(e) => setKey(e.target.value)} style={{ width: "auto", height: 32, fontSize: 13 }}>
            {avail.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
          </select>
        )}
      </div>
      {rd ? <EChart option={option} className="chart" ariaLabel={`${reactor}: ${v?.label} in the last 3 hours`} />
        : <span className="small muted" style={{ padding: 8 }}>No readings yet since the server started.</span>}
      <span className="xs muted" style={{ padding: "0 8px" }}>Kept in memory only (every {hist.data?.interval_s ?? "…"} s, last 3 h):
        nothing is saved until an experiment starts recording. Time axis: hours before now.</span>
    </section>
  );
}

/** Loops on the controller with their actuators (read from the controller's configuration). */
function LoopsCard({ cfg }: { cfg: LoopConfigView | undefined }) {
  if (!cfg) return null;
  if (!cfg.reachable) return <section className="card tight"><h3>Loops and actuators</h3><span className="small muted">{cfg.message ?? "Controller not reachable."}</span></section>;
  return (
    <section className="card tight" aria-labelledby="loops-h">
      <h3 id="loops-h">Loops and actuators</h3>
      {cfg.loops.filter((l) => l.installed).map((l) => (
        <div key={l.key} className="loop-row">
          <span className={`dot ${l.active ? "ok" : "idle"}`} title={l.active ? "Loop on" : "Loop off"} />
          <span className="stack" style={{ gap: 2, minWidth: 0 }}>
            <span className="small"><b>{l.label}</b> · loop · controller{l.control_type ? ` · ${l.control_type}` : ""}{l.active ? " · on" : " · off"}</span>
            <span className="xs muted">{l.actuators.length ? l.actuators.map((a) => `${a.name}${a.side_label ? ` (${a.side_label})` : ""}`).join(", ") : "no actuator"}
              {l.drives ? ` · drives ${l.drives}` : ""}</span>
          </span>
        </div>
      ))}
      {cfg.unassigned_pumps.length > 0 && (
        <span className="xs muted">Free actuators (in no loop): {cfg.unassigned_pumps.map((a) => a.name).join(", ")}. They can be used by FM2 rules (2.4).</span>
      )}
    </section>
  );
}

/**
 * Monitoring page of one bioreactor, with or without an experiment: live values from the controller and
 * the same commands as during a fermentation (setpoints, process limits, loops, dose counters).
 * Nothing is recorded here: readings are stored only while an experiment is recording.
 */
export function ReactorPage() {
  const reactor = useParams().id ?? "";
  const live = usePolling(api.live, 10000);
  const activity = usePolling(() => api.reactorActivity(reactor, 72), 20000, [reactor]);
  const cfg = usePolling(() => api.loopConfig(reactor), 60000, [reactor]);
  const [allEv, setAllEv] = useState(false);
  const [allCh, setAllCh] = useState(false);
  const r = live.data?.find((x) => x.reactor_id === reactor);
  if (live.data && !r) return <div className="card">Unknown bioreactor {reactor}. <Link to="/">Home</Link></div>;
  const exp = r?.experiment;
  const s = r?.snapshot;
  const refresh = () => { live.refresh(); activity.refresh(); };
  const ev = activity.data?.events ?? [], ch = activity.data?.changes ?? [];

  return (
    <>
      <div className="sticky-head mon-head">
        <header className="mon-bar">
          <Link to="/" className="btn icon text" aria-label="Back to home"><Icon name="back" /></Link>
          <div className="stack mon-title" style={{ gap: 2 }}>
            <h1>{r ? reactorName(r) : reactor} · Controller</h1>
            <span className="small muted mon-sub">Live values and commands on the controller{r?.interval_s ? ` · reading every ${r.interval_s} s` : ""}</span>
          </div>
          {exp && (
            <Link className="btn tonal mon-exp" to={`/experiments/${exp.id}`} title={`${exp.name} ${exp.code}`}>
              <span className="mon-exp-name">{exp.name}</span> <StatusChip status={exp.status} />
            </Link>
          )}
          <div className="row mon-actions-wide" style={{ gap: 8 }}>
            <LoopsButton reactor={reactor} channels={exp?.channels} />
            <ResetCountersButton reactor={reactor} recording={exp?.status === "fermenting"} onDone={refresh} />
          </div>
        </header>
        <div className="mon-status small">
          {exp && exp.status === "fermenting"
            ? <span className="row" style={{ gap: 6, color: "var(--data-green-dark)", fontWeight: 600, flexWrap: "nowrap" }}><span className="dot ok" />Recording · t = <span className="mono">{fmtDuration(exp.t_h)}</span></span>
            : <span className="row" style={{ gap: 6, fontWeight: 600, flexWrap: "nowrap" }}><span className="dot idle" />Not recording</span>}
          <span className="muted row" style={{ gap: 4, flexWrap: "nowrap" }} title="Time since the last reading from the controller">
            <Icon name="refresh" size={16} /><span className="mono">{fmtAge(r?.last_reading_age_s)}</span> ago</span>
          {r && !r.online && <b style={{ color: "var(--alert-text)" }}>controller not responding</b>}
        </div>
      </div>
      <div className="row mon-actions-narrow" style={{ gap: 8 }}>
        <LoopsButton reactor={reactor} channels={exp?.channels} />
        <ResetCountersButton reactor={reactor} recording={exp?.status === "fermenting"} onDone={refresh} />
      </div>

      <div className="split">
        <div className="wide">
          <section className="card tight" aria-labelledby="ctrl">
            <div className="row between" style={{ padding: "0 8px" }}>
              <h2 id="ctrl" style={{ fontSize: 18 }}>Controller · <span className="mono">{reactor}</span></h2>
              <span className="xs muted">{exp ? "channels of the experiment" : "every channel the controller reports"}</span>
            </div>
            {!r && <span className="muted small" style={{ padding: 8 }}>Waiting for the server…</span>}
            {r && !r.online && <div className="banner alert small"><Icon name="bell" color="#b30020" size={18} />Controller not reachable{r.error ? `: ${r.error}` : ""}</div>}
            {r && s && <ControllerPanel reactor={reactor} s={s} channels={exp?.channels} phMode={r.ph_control?.mode} onChanged={refresh} />}
          </section>
          {exp?.status !== "fermenting" && <IdleTrends reactor={reactor} />}
        </div>
        <aside className="side">
          <LoopsCard cfg={cfg.data} />
          <section className="card tight" aria-labelledby="ev">
            <div className="row between"><h3 id="ev">Events · last 3 days</h3>
              {ev.length > 8 && <button className="btn text" onClick={() => setAllEv(!allEv)}>{allEv ? "Show fewer" : `Show all (${ev.length})`}</button>}</div>
            {ev.slice(0, allEv ? undefined : 8).map((x) => (
              <div key={x.id} className="row" style={{ gap: 10, alignItems: "flex-start", flexWrap: "nowrap" }}>
                <span className={`dot ${x.severity === "alert" ? "bad" : x.severity === "good" ? "ok" : "idle"}`} style={{ marginTop: 6, flex: "0 0 auto" }} />
                <span className="small" style={{ flexGrow: 1 }}>{x.message}</span>
                <span className="xs muted mono" title={new Date(x.at).toLocaleString()}>{new Date(x.at).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
              </div>
            ))}
            {ev.length === 0 && <span className="small muted">No events.</span>}
          </section>
          <section className="card tight" aria-labelledby="log">
            <div className="row between"><h3 id="log">Change log · last 3 days</h3>
              {ch.length > 5 && <button className="btn text" onClick={() => setAllCh(!allCh)}>{allCh ? "Show fewer" : `Show all (${ch.length})`}</button>}</div>
            {ch.slice(0, allCh ? undefined : 5).map((c, i) => (
              <div key={i} className="small row" style={{ gap: 10, flexWrap: "nowrap" }}>
                <span className="mono muted" title={new Date(c.at).toLocaleString()}>{new Date(c.at).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
                <span>{c.parameter} {c.old ?? "—"} → {c.new} · {c.origin} · {c.status}</span>
              </div>
            ))}
            {ch.length === 0 && <span className="small muted">No changes from this software.</span>}
          </section>
        </aside>
      </div>
    </>
  );
}
