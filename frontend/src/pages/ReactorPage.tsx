import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtDuration, usePolling } from "../hooks";
import { Icon, StatusChip } from "../components/ui";
import { ControllerPanel, LoopsButton, ResetCountersButton } from "../components/ReactorControls";
import { REACTOR_LABEL } from "./Home";

/**
 * Monitoring page of one bioreactor, with or without an experiment: live values from the controller and
 * the same commands as during a fermentation (setpoints, process limits, loops, dose counters).
 * Nothing is recorded here: readings are stored only while an experiment is recording.
 */
export function ReactorPage() {
  const reactor = useParams().id ?? "";
  const live = usePolling(api.live, 10000);
  const activity = usePolling(() => api.reactorActivity(reactor, 72), 20000, [reactor]);
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
      <div className="sticky-head">
        <header className="row between">
          <div className="row" style={{ gap: 12 }}>
            <Link to="/" className="btn icon text" aria-label="Back to home"><Icon name="back" /></Link>
            <div className="stack" style={{ gap: 2 }}>
              <h1 style={{ fontSize: 22 }}>Bioreactor {REACTOR_LABEL[reactor] ?? reactor} · monitoring</h1>
              <span className="small muted">Live values and commands on the controller. Nothing is recorded unless an experiment is recording.</span>
            </div>
          </div>
          {exp ? (
            <Link className="btn tonal" to={`/experiments/${exp.id}`}>
              <span className="mono">{exp.code}</span>&nbsp;· {exp.strain || "—"} <StatusChip status={exp.status} />
            </Link>
          ) : <Link className="btn tonal" to={`/experiments/new?reactor=${reactor}`}>Plan on {REACTOR_LABEL[reactor] ?? reactor}</Link>}
        </header>
        <div className="card" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", borderRadius: 16 }}>
          <div className="row" style={{ gap: 24 }}>
            {exp && exp.status === "fermenting"
              ? <span className="row" style={{ gap: 8, color: "#1f7a63", fontWeight: 600 }}><span className="dot ok" style={{ width: 10, height: 10 }} />Recording {exp.code} · t = {fmtDuration(exp.t_h)}</span>
              : <span className="row" style={{ gap: 8, fontWeight: 600 }}><span className="dot idle" style={{ width: 10, height: 10 }} />Not recording</span>}
            <span className="small muted">Last controller reading <span className="mono">{r?.last_reading_age_s == null ? "—" : `${Math.round(r.last_reading_age_s)} s`}</span> ago
              {r && !r.online && <b style={{ color: "#b30020" }}> · controller not responding</b>}</span>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <LoopsButton reactor={reactor} channels={exp?.channels} />
            <ResetCountersButton reactor={reactor} recording={exp?.status === "fermenting"} onDone={refresh} />
          </div>
        </div>
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
        </div>
        <aside className="side">
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
