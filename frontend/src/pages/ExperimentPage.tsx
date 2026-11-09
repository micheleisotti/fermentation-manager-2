import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Experiment, type Readings } from "../api";
import { fmt, fmtDuration, usePolling } from "../hooks";
import { baseOption, trendsOption, TREND_VARS } from "../components/charts";
import { DataValue, EChart, Icon, Segmented, StatusChip } from "../components/ui";
import { SetpointDialog } from "../components/SetpointDialog";

export function ExperimentPage() {
  const id = Number(useParams().id);
  const exp = usePolling(() => api.experiment(id), 15000, [id]);
  const all = usePolling(() => api.experiments(), 60000);
  if (exp.error) return <div className="card">Experiment not found: {exp.error}</div>;
  if (!exp.data) return <div className="muted">Loading…</div>;
  const e = exp.data;
  const partner = (all.data ?? []).find((x) => e.parallel_partner_codes.includes(x.code));
  return e.status === "planned"
    ? <PlannedView e={e} onChange={exp.refresh} />
    : <FermentationView e={e} partner={partner} onChange={exp.refresh} />;
}

function Header({ e, partner }: { e: Experiment; partner?: Experiment }) {
  const nav = useNavigate();
  return (
    <>
      <div className="row" style={{ gap: 16 }}>
        <Link to="/" className="btn icon text" aria-label="Back to home"><Icon name="back" /></Link>
        {e.parallel_run_code && partner && (
          <div className="row" role="tablist" aria-label="Reactor" style={{ gap: 8 }}>
            <span className="small muted">Parallel run <b className="mono" style={{ color: "#22294d" }}>{e.parallel_run_code}</b></span>
            {[e, partner].sort((a, b) => a.reactor_id.localeCompare(b.reactor_id)).map((x) => (
              <button key={x.id} role="tab" aria-selected={x.id === e.id} className={`btn ${x.id === e.id ? "filled" : "outlined"}`}
                style={{ height: 44 }} onClick={() => nav(`/experiments/${x.id}`)}>
                <span className="mono">{x.reactor_id}</span><span style={{ fontWeight: 400 }}>{x.code} · {x.strain || "—"}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <header className="row between">
        <div className="row" style={{ gap: 12 }}>
          <div className="stack" style={{ gap: 2 }}>
            <h1 style={{ fontSize: 22 }}><span className="mono">{e.code}</span> · {e.name}{e.strain && <> — <i>L. plantarum</i> {e.strain}</>}</h1>
            <span className="small muted">Bioreactor {e.reactor_id.slice(1)}{e.started_at && ` · started ${new Date(e.started_at).toLocaleString()}`}
              {e.parallel_run_code && ` · parallel run ${e.parallel_run_code}`}</span>
          </div>
          <StatusChip status={e.status} />
        </div>
      </header>
    </>
  );
}

function PlannedView({ e, onChange }: { e: Experiment; onChange: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  return (
    <>
      <Header e={e} />
      <div className="card" style={{ maxWidth: 560 }}>
        <h2>Ready to start?</h2>
        <span className="muted small">The full planning screen (profile check, media, parallel run) comes in the next step.
          Recording starts with Start: t = 0 is that moment.</span>
        {err && <div className="banner alert">{err}</div>}
        <div className="row"><button className="btn filled" onClick={async () => {
          try { await api.start(e.id); onChange(); } catch (x) { setErr((x as Error).message); }
        }}><Icon name="play" size={18} />Start recording</button></div>
      </div>
    </>
  );
}

function FermentationView({ e, partner, onChange }: { e: Experiment; partner?: Experiment; onChange: () => void }) {
  const running = e.status === "fermenting";
  const readings = usePolling(() => api.readings(e.id), running ? 30000 : 600000, [e.id]);
  const partnerReadings = usePolling<Readings | undefined>(() => (partner ? api.readings(partner.id) : Promise.resolve(undefined)), 60000, [partner?.id]);
  const [binH, setBinH] = useState<"0.25" | "1">("0.25");
  const analysis = usePolling(() => api.analysis(e.id, Number(binH)), running ? 60000 : 600000, [e.id, binH]);
  const live = usePolling(api.live, running ? 10000 : 600000);
  const events = usePolling(() => api.events(e.id), 20000, [e.id]);
  const log = usePolling(() => api.setpointLog(e.id), 30000, [e.id]);

  const [left, setLeft] = useState("ph");
  const [right, setRight] = useState("temperature");
  const [showSp, setShowSp] = useState(true);
  const [showLim, setShowLim] = useState(true);
  const [showPartner, setShowPartner] = useState(true);
  const [showPartnerBase, setShowPartnerBase] = useState(false);
  const [dialog, setDialog] = useState<string | null>(null);

  const lv = TREND_VARS.find((v) => v.key === left)!;
  const rv = TREND_VARS.find((v) => v.key === right) ?? null;
  const trend = useMemo(() => trendsOption(readings.data, lv, rv, {
    setpoints: showSp, limits: showLim,
    partner: showPartner && partner ? partnerReadings.data : undefined, partnerLabel: partner ? `${lv.label} ${partner.reactor_id} (paired)` : undefined,
  }), [readings.data, partnerReadings.data, lv, rv, showSp, showLim, showPartner, partner]);
  const base = useMemo(() => baseOption(readings.data, analysis.data?.rate_bins, showPartnerBase ? partnerReadings.data : undefined),
    [readings.data, analysis.data, partnerReadings.data, showPartnerBase]);

  const r = live.data?.find((x) => x.reactor_id === e.reactor_id);
  const s = running ? r?.snapshot : null;
  const bd = analysis.data?.base_dosing;
  const lastBin = analysis.data?.rate_bins.filter((b) => b.complete).at(-1);

  return (
    <>
      <Header e={e} partner={partner} />
      {running && (
        <div className="card" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", borderRadius: 16 }}>
          <div className="row" style={{ gap: 24 }}>
            <span className="row" style={{ gap: 8, color: "#1f7a63", fontWeight: 600 }}><span className="dot ok" style={{ width: 10, height: 10 }} />Recording</span>
            <span>t = <span className="mono" style={{ fontSize: 20, fontWeight: 600 }}>{fmtDuration(r?.experiment?.t_h)}</span></span>
            <span className="small muted">Last controller reading <span className="mono">{r?.last_reading_age_s == null ? "—" : `${Math.round(r.last_reading_age_s)} s`}</span> ago
              {r && !r.online && <b style={{ color: "#b30020" }}> · controller not responding</b>}</span>
          </div>
          <button className="btn outlined" style={{ color: "#22294d" }} onClick={async () => {
            if (window.confirm(`End production of ${e.code}? Recording stops and the experiment becomes Completed.`)) { await api.end(e.id); onChange(); }
          }}><Icon name="stop" size={16} />End of production</button>
        </div>
      )}

      <div className="split">
        <div className="wide">
          <section className="card" aria-labelledby="trends">
            <div className="row between">
              <h2 id="trends" style={{ fontSize: 18 }}>Trends</h2>
              <div className="row">
                <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 14, height: 3, background: "#1d3786" }} />Left
                  <select value={left} onChange={(x) => setLeft(x.target.value)} style={{ height: 40, width: "auto" }}>
                    {TREND_VARS.map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}
                  </select>
                </label>
                <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 14, height: 3, background: "#f9b112" }} />Right
                  <select value={right} onChange={(x) => setRight(x.target.value)} style={{ height: 40, width: "auto" }}>
                    {TREND_VARS.filter((v) => v.key !== left).map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}
                    <option value="none">None</option>
                  </select>
                </label>
              </div>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button className={`chip ${showSp ? "on" : ""}`} aria-pressed={showSp} onClick={() => setShowSp(!showSp)}>Setpoints</button>
              <button className={`chip ${showLim ? "on" : ""}`} aria-pressed={showLim} onClick={() => setShowLim(!showLim)}>Process limits</button>
              {partner && <button className={`chip ${showPartner ? "on" : ""}`} aria-pressed={showPartner} onClick={() => setShowPartner(!showPartner)}>
                Paired: {partner.reactor_id} · {partner.code}</button>}
            </div>
            <EChart option={trend} ariaLabel="Trends over time since start" />
          </section>

          <section className="card" aria-labelledby="base">
            <div className="row between">
              <div className="stack" style={{ gap: 2 }}>
                <h2 id="base" style={{ fontSize: 18 }}>Base dosing</h2>
                <span className="small muted">Cumulative volume as reported by the controller; rate computed per bin</span>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <span className="small muted">Rate averaged over</span>
                <Segmented label="Averaging window" value={binH} onChange={setBinH} options={[{ value: "0.25", label: "15 min" }, { value: "1", label: "1 h" }]} />
                {partner && <button className={`chip ${showPartnerBase ? "on" : ""}`} onClick={() => setShowPartnerBase(!showPartnerBase)}>Overlay {partner.reactor_id}</button>}
              </div>
            </div>
            <div className="dv-grid">
              <DataValue value={fmt(bd?.total_ml as number, 1)} unit="mL" label="Cumulative (controller)" color="#1f7a63" />
              <DataValue value={fmt(lastBin?.rate_ml_min, 3)} unit="mL/min" label={`Last complete ${binH === "1" ? "hour" : "15 min"}`} color="#1f7a63" />
              <DataValue value={fmt(bd?.peak_rate_ml_min as number, 3)} unit="mL/min" label={`Peak rate · t = ${fmt(bd?.t_peak_h as number, 1)} h`} />
              <DataValue value={fmt(s?.base_pump_pct, 0)} unit="%" label="Pump activation · now" />
            </div>
            <EChart option={base} className="chart short" ariaLabel="Base rate bins and cumulative base over time" />
          </section>
        </div>

        <aside className="side">
          {running && s && (
            <section className="card tight" aria-labelledby="ctrl">
              <div className="row between" style={{ padding: "0 8px" }}>
                <h2 id="ctrl" style={{ fontSize: 18 }}>Controller · <span className="mono">{e.reactor_id}</span></h2>
                <span className="xs muted">measured · setpoint</span>
              </div>
              {[
                { p: "ph", v: fmt(s.ph), u: "", l: `pH · set ${fmt(s.ph_setpoint)} · limits ${fmt(s.ph_low_limit)}–${fmt(s.ph_high_limit)}`, c: "#1d3786" },
                { p: "temperature", v: fmt(s.temperature, 1), u: "°C", l: `T · set ${fmt(s.temperature_setpoint, 1)} · limits ${fmt(s.temperature_low_limit, 1)}–${fmt(s.temperature_high_limit, 1)}` },
                { p: "stirrer", v: fmt(s.stirrer, 0), u: "rpm", l: `Stirrer · set ${fmt(s.stirrer_setpoint, 0)}` },
              ].map((row) => (
                <div key={row.p} className="row between" style={{ padding: "8px", borderBottom: "1px solid #eef0f4" }}>
                  <DataValue value={row.v} unit={row.u} label={row.l} color={row.c} />
                  <button className="btn tonal" onClick={() => setDialog(row.p)}>Edit</button>
                </div>
              ))}
              <span className="xs muted" style={{ padding: "4px 8px" }}>
                pH control: {r?.ph_control?.mode ?? "—"}. Control mode and PID come from the profile at planning; limits, dose calibration and actuators stay on the controller.
              </span>
            </section>
          )}

          <section className="card tight" aria-labelledby="ev">
            <h3 id="ev">Events</h3>
            {(events.data ?? []).slice(0, 8).map((x) => (
              <div key={x.id} className="row" style={{ gap: 10, alignItems: "flex-start", flexWrap: "nowrap" }}>
                <span className={`dot ${x.severity === "alert" ? "bad" : x.severity === "good" ? "ok" : "idle"}`} style={{ marginTop: 6, flex: "0 0 auto" }} />
                <span className="small" style={{ flexGrow: 1 }}>{x.message}</span>
                <span className="xs muted mono">{x.t_h == null ? "" : `${x.t_h.toFixed(1)} h`}</span>
              </div>
            ))}
            {(events.data ?? []).length === 0 && <span className="small muted">No events yet.</span>}
          </section>

          <section className="card tight" aria-labelledby="log">
            <h3 id="log">Change log</h3>
            {(log.data ?? []).map((c, i) => (
              <div key={i} className="small row" style={{ gap: 10, flexWrap: "nowrap" }}>
                <span className="mono muted">{new Date(c.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                <span>{c.parameter} {c.old ?? "—"} → {c.new} · {c.origin} · {c.status}</span>
              </div>
            ))}
            {(log.data ?? []).length === 0 && <span className="small muted">No setpoint changes from this software.</span>}
          </section>
        </aside>
      </div>

      <ManualVariables e={e} />

      {dialog && s && (
        <SetpointDialog reactor={e.reactor_id} parameter={dialog} snapshot={s} remote={!["localhost", "127.0.0.1"].includes(location.hostname)}
          onClose={(changed) => { setDialog(null); if (changed) { live.refresh(); log.refresh(); } }} />
      )}
    </>
  );
}

function ManualVariables({ e }: { e: Experiment }) {
  const vars = usePolling(api.variables, 600000);
  const meas = usePolling(() => api.measurements(e.id), 30000, [e.id]);
  const [variable, setVariable] = useState<number | "">("");
  const [value, setValue] = useState("");
  const [time, setTime] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const varId = variable === "" ? (vars.data?.find((v) => v.name === "OD600") ?? vars.data?.[0])?.id : variable;
  const unit = vars.data?.find((v) => v.id === varId)?.unit ?? "";

  async function add() {
    setErr(null);
    const v = Number(value.replace(",", "."));
    if (!varId || !Number.isFinite(v) || value === "") { setErr("Enter a numeric value"); return; }
    let sampled_at: string | undefined;
    if (time) {
      const [hh, mm] = time.split(":").map(Number);
      const d = new Date(); d.setHours(hh, mm, 0, 0);
      if (d.getTime() > Date.now()) d.setDate(d.getDate() - 1); // sample taken before midnight
      sampled_at = d.toISOString();
    }
    try {
      await api.addMeasurement(e.id, { variable_id: varId, value: v, sampled_at, note });
      setValue(""); setNote(""); setTime(""); meas.refresh();
    } catch (x) { setErr((x as Error).message); }
  }

  return (
    <section className="card" aria-labelledby="man">
      <h2 id="man" style={{ fontSize: 18 }}>Manual variables</h2>
      {e.status === "fermenting" && (
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field" style={{ flex: "1 1 140px" }}>Sampling time (empty = now)
            <input type="time" value={time} onChange={(x) => setTime(x.target.value)} />
          </label>
          <label className="field" style={{ flex: "1 1 160px" }}>Variable
            <select value={varId ?? ""} onChange={(x) => setVariable(Number(x.target.value))}>
              {(vars.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          </label>
          <label className="field" style={{ flex: "0 1 120px" }}>Value{unit ? ` (${unit})` : ""}
            <input inputMode="decimal" value={value} onChange={(x) => setValue(x.target.value)} placeholder="0.000" style={{ fontFamily: "var(--font-mono)" }} />
          </label>
          <label className="field" style={{ flex: "3 1 220px" }}>Notes
            <input value={note} onChange={(x) => setNote(x.target.value)} placeholder="e.g. dilution 1:10" />
          </label>
          <button className="btn filled" style={{ height: 48, borderRadius: 16 }} onClick={add}>Add</button>
        </div>
      )}
      {err && <div className="banner alert">{err}</div>}
      <div className="table-wrap">
        <table className="data" style={{ minWidth: 640 }}>
          <thead><tr><th>t (h)</th><th>Time</th><th>Variable</th><th style={{ textAlign: "right" }}>Value</th><th>Unit</th><th>Notes</th></tr></thead>
          <tbody>
            {(meas.data ?? []).map((m) => (
              <tr key={m.id}>
                <td className="mono">{m.t_h.toFixed(2)}</td>
                <td className="mono muted">{new Date(m.sampled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</td>
                <td>{m.variable_name}</td>
                <td className="mono" style={{ textAlign: "right" }}>{m.value}</td>
                <td className="mono muted">{m.unit}</td>
                <td className="muted">{m.note}</td>
              </tr>
            ))}
            {(meas.data ?? []).length === 0 && <tr><td colSpan={6} className="muted">No manual entries yet.</td></tr>}
          </tbody>
        </table>
      </div>
      <span className="xs muted">Manual entries are editable and every edit is logged; controller data is read-only.</span>
    </section>
  );
}
