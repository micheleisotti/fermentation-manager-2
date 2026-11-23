import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ReactorHistory, type Readings } from "../api";
import { fmtDuration, usePolling, fmtAge } from "../hooks";
import { EChart, Icon, StatusChip } from "../components/ui";
import { LoopsButton, ResetCountersButton } from "../components/ReactorControls";
import { LoopCards } from "../components/LoopCards";
import { loopDots, ManualPumpsButton } from "../components/SidePanels";
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

/**
 * Controller page of one bioreactor (2.3, desktop prototype): loops with their actuators and outputs, idle
 * trends (last 3 h, memory only) and the controller's events. Commands as on the fermentation page.
 * Nothing is recorded here: readings are stored only while an experiment is recording.
 */
export function ReactorPage() {
  const reactor = useParams().id ?? "";
  const live = usePolling(api.live, 10000);
  const bio = usePolling(api.bioreactors, 60000);
  const activity = usePolling(() => api.reactorActivity(reactor, 72), 20000, [reactor]);
  const cfg = usePolling(() => api.loopConfig(reactor), 30000, [reactor]);
  const [tab, setTab] = useState<"controller" | "trends" | "events">("controller");
  const r = live.data?.find((x) => x.reactor_id === reactor);
  const b = bio.data?.find((x) => x.id === reactor);
  if (live.data && !r) return <div className="card">Unknown bioreactor {reactor}. <Link to="/">Home</Link></div>;
  const exp = r?.experiment;
  const name = r ? reactorName(r) : reactor;
  const refresh = () => { live.refresh(); activity.refresh(); cfg.refresh(); };
  const items = [
    ...(activity.data?.events ?? []).map((x) => ({ at: x.at, title: x.message, sub: x.severity, bad: x.severity === "alert" })),
    ...(activity.data?.changes ?? []).map((c) => ({ at: c.at, title: `${c.parameter} ${c.old ?? "—"} → ${c.new}`, sub: `change · ${c.status} · ${c.origin}${c.author ? ` · ${c.author}` : ""}`, bad: false })),
  ].sort((a, b2) => b2.at.localeCompare(a.at));

  return (
    <>
      <div className="sticky-head exp-sticky">
        <header className="exp-head">
          <div className="stack" style={{ gap: 0, flex: "1 1 auto", minWidth: 0 }}>
            <span className="xs muted">Controller{b ? ` · ${b.driver_label}${b.address ? ` · ${b.address}` : ""} · reading every ${b.interval_s} s` : ""}</span>
            <h1 className="exp-h1">{name}</h1>
          </div>
          {exp ? (
            <>
              <span className={`run-chip ${exp.status === "fermenting" ? "ok" : "prep"}`}><span className={`dot ${exp.status === "fermenting" ? "ok" : "prep"}`} />
                {exp.code} {exp.status === "fermenting" ? <>running · <span className="mono">{fmtDuration(exp.t_h)}</span></> : "preparing"}</span>
              <Link className="btn tonal" to={`/experiments/${exp.id}`}>Open fermentation →</Link>
            </>
          ) : <Link className="btn tonal" to={`/experiments/new?reactor=${reactor}`}>Plan experiment</Link>}
        </header>
        <div className="action-bar">
          {r && <span className="run-chip"><span className={`dot ${r.online ? "ok" : "bad"}`} />{r.online ? `last reading ${fmtAge(r.last_reading_age_s)} ago` : "not reachable"}</span>}
          <span className="ab-sep" />
          <LoopsButton reactor={reactor} channels={exp?.channels} dots={loopDots(r?.snapshot, exp?.channels)} />
          <ResetCountersButton reactor={reactor} recording={exp?.status === "fermenting"} onDone={refresh} />
          <ManualPumpsButton reactor={reactor} name={name} />
        </div>
      </div>
      <div className="row" role="tablist" aria-label="Sections" style={{ gap: 8 }}>
        {([["controller", "Controller"], ["trends", "Trends"], ["events", "Events"]] as const).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`chip tab-chip ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{l}</button>))}
      </div>
      {r && !r.online && <div className="banner alert small"><Icon name="bell" color="#b30020" size={18} />Controller not reachable{r.error ? `: ${r.error}` : ""}</div>}
      {tab === "controller" && <LoopCards reactor={reactor} cfg={cfg.data} s={r?.snapshot ?? null} onChanged={refresh} />}
      {tab === "trends" && (exp?.status === "fermenting"
        ? <div className="card"><span className="small">An experiment is recording: its trends are on the <Link to={`/experiments/${exp.id}`}>fermentation page</Link>.</span></div>
        : <IdleTrends reactor={reactor} />)}
      {tab === "events" && (
        <section className="card tight">
          <h3>Events and changes · last 3 days</h3>
          {items.slice(0, 60).map((x, i) => (
            <div key={i} className="row" style={{ gap: 10, alignItems: "flex-start", flexWrap: "nowrap" }}>
              <span className={`dot ${x.bad ? "bad" : "idle"}`} style={{ marginTop: 6, flex: "0 0 auto" }} />
              <span className="stack" style={{ gap: 1, flexGrow: 1 }}><span className="small">{x.title}</span><span className="xs muted">{x.sub}</span></span>
              <span className="xs muted mono">{new Date(x.at).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
            </div>
          ))}
          {items.length === 0 && <span className="small muted">No events.</span>}
        </section>
      )}
    </>
  );
}
