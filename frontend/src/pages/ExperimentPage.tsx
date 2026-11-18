import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Experiment, type Readings } from "../api";
import { fmt, fmtDuration, PHONE_QUERY, useMediaQuery, usePolling, fmtAge } from "../hooks";
import { baseOption, MANUAL_PREFIX, trendsOption, varsFor, type ManualPoints, type TrendVar } from "../components/charts";
import { DataValue, EChart, Icon, Segmented, StatusChip } from "../components/ui";
import { DeleteExperiment } from "../components/DeleteExperiment";
import { ControllerPanel, LoopsButton, ResetCountersButton } from "../components/ReactorControls";
import { Planning } from "./Planning";
import { PhoneFermentation } from "./PhoneExperiment";
import { ModeTabs } from "./ExperimentAnalysis";
import { ArchiveButton, EndExperimentButton, ExpTags, StartExperimentButton } from "./ExperimentsList";

export function ExperimentPage() {
  const id = Number(useParams().id);
  const exp = usePolling(() => api.experiment(id), 15000, [id]);
  const all = usePolling(() => api.experiments(), 60000);
  const phone = useMediaQuery(PHONE_QUERY);
  if (exp.error) return <div className="card">Experiment not found: {exp.error}</div>;
  if (!exp.data) return <div className="muted">Loading…</div>;
  const e = exp.data;
  const partner = (all.data ?? []).find((x) => e.parallel_partner_codes.includes(x.code));
  if (e.status === "planned") return <Planning key={e.id} existing={e} onChange={exp.refresh} />;
  return phone
    ? <PhoneFermentation key={e.id} e={e} partner={partner} onChange={exp.refresh} />
    : <FermentationView e={e} partner={partner} onChange={exp.refresh} />;
}

function Header({ e, partner, onArchive }: { e: Experiment; partner?: Experiment; onArchive?: () => void }) {
  const nav = useNavigate();
  return (
    <>
      <div className="row" style={{ gap: 16 }}>
        <Link to="/" className="btn icon text" aria-label="Back to home"><Icon name="back" /></Link>
        {e.parallel_run_code && partner && (
          <div className="row" role="tablist" aria-label="Reactor" style={{ gap: 8 }}>
            <span className="small muted">Parallel run <b className="mono" style={{ color: "var(--ink)" }}>{e.parallel_run_code}</b></span>
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
            <h1 style={{ fontSize: 22, fontWeight: 600 }}>{e.name}{e.strain && <span style={{ fontWeight: 400 }}> — {e.strain}</span>}
              {" "}<span className="mono code-chip">{e.code}</span></h1>
            <span className="small muted">Bioreactor {e.reactor_id.slice(1)}{e.started_at && ` · started ${new Date(e.started_at).toLocaleString()}`}
              {e.parallel_run_code && ` · parallel run ${e.parallel_run_code}`}
              {e.profile_name && <> · profile “{e.profile_name}”</>}
              {e.profile_changed && <span className="profile-changed" title="The profile was edited after this experiment's plan was saved; the experiment keeps the values it was saved with.">
                {" "}· changed after the plan was saved</span>}</span>
          </div>
          <StatusChip status={e.status} />
          <ExpTags e={e} />
        </div>
        <div className="row" style={{ gap: 12 }}>
          <ModeTabs e={e} current="fermentation" />
          {e.status === "preparing" && <button className="btn text" onClick={async () => {
            if (window.confirm(`Back to planning? ${e.code} leaves the live page (nothing has been recorded yet).`)) { await api.unprepare(e.id); onArchive?.(); }
          }}>Back to planning</button>}
          <ArchiveButton e={e} onDone={onArchive ?? (() => undefined)} />
          <DeleteExperiment e={e} />
        </div>
      </header>
    </>
  );
}

function FermentationView({ e, partner, onChange }: { e: Experiment; partner?: Experiment; onChange: () => void }) {
  const preparing = e.status === "preparing";
  const running = e.status === "fermenting" || preparing;   // live: polling and current values
  const readings = usePolling(() => api.readings(e.id), running ? 30000 : 600000, [e.id, e.status]);
  const partnerReadings = usePolling<Readings | undefined>(() => (partner ? api.readings(partner.id) : Promise.resolve(undefined)), 60000, [partner?.id]);
  const [binH, setBinH] = useState<"0.25" | "1">("0.25");
  const analysis = usePolling(() => api.analysis(e.id, Number(binH)), running ? 60000 : 600000, [e.id, binH]);
  const live = usePolling(api.live, running ? 10000 : 600000);
  const events = usePolling(() => api.events(e.id), 20000, [e.id]);
  const log = usePolling(() => api.setpointLog(e.id), 30000, [e.id]);
  const meas = usePolling(() => api.measurements(e.id), 60000, [e.id]);

  const ch = e.channels;
  // manual variables (OD, AFU, …) can go on the chart too, as points
  const manual = useMemo(() => {
    const out: ManualPoints = {};
    for (const m of [...(meas.data ?? [])].sort((a, b) => a.t_h - b.t_h)) {
      (out[m.variable_name] ??= { unit: m.unit, t: [], y: [] });
      out[m.variable_name].t.push(m.t_h); out[m.variable_name].y.push(m.value);
    }
    return out;
  }, [meas.data]);
  const vars: TrendVar[] = [...varsFor(ch),
    ...Object.entries(manual).map(([n, m]) => ({ key: `${MANUAL_PREFIX}${n}`, label: `${n} (manual)`, unit: m.unit, digits: 2 }))];
  const main = vars.filter((v) => ["ph", "temperature", "stirrer", "do2"].includes(v.key));
  const saved = loadTrendPrefs();
  // the saved choice is kept even if a manual variable is not loaded yet: it applies once available
  const [left, setLeftRaw] = useState(saved.left ?? main[0]?.key ?? vars[0]?.key ?? "ph");
  const [right, setRightRaw] = useState(saved.right ?? main[1]?.key ?? "none");
  const [showSp, setShowSpRaw] = useState(saved.setpoints ?? true);
  const [showLim, setShowLimRaw] = useState(saved.limits ?? true);
  const setLeft = (k: string) => { setLeftRaw(k); saveTrendPrefs({ left: k }); };
  const setRight = (k: string) => { setRightRaw(k); saveTrendPrefs({ right: k }); };
  const setShowSp = (b: boolean) => { setShowSpRaw(b); saveTrendPrefs({ setpoints: b }); };
  const setShowLim = (b: boolean) => { setShowLimRaw(b); saveTrendPrefs({ limits: b }); };
  const [showPartner, setShowPartner] = useState(true);
  const [showPartnerBase, setShowPartnerBase] = useState(false);
  const [allEvents, setAllEvents] = useState(false);
  const [allLog, setAllLog] = useState(false);

  const lv = vars.find((v) => v.key === left) ?? main[0] ?? vars[0];
  const rv = vars.find((v) => v.key === right && v.key !== lv?.key) ?? null;
  const endH = analysis.data?.estimated_end?.t_end_h ?? null;
  const trend = useMemo(() => !lv ? {} : trendsOption(readings.data, lv, rv, {
    setpoints: showSp, limits: showLim, endH, manual,
    partner: showPartner && partner && !lv.key.startsWith(MANUAL_PREFIX) ? partnerReadings.data : undefined,
    partnerLabel: partner ? `${lv.label} ${partner.reactor_id} (paired)` : undefined,
  }), [readings.data, partnerReadings.data, lv, rv, showSp, showLim, showPartner, partner, endH, manual]);
  const base = useMemo(() => baseOption(readings.data, analysis.data?.rate_bins, showPartnerBase ? partnerReadings.data : undefined,
    endH, analysis.data?.acid_rate_bins, partner?.reactor_id),
    [readings.data, analysis.data, partnerReadings.data, showPartnerBase, endH, partner]);

  const r = live.data?.find((x) => x.reactor_id === e.reactor_id);
  const s = running ? r?.snapshot : null;
  const bd = analysis.data?.base_dosing;
  const lastBin = analysis.data?.rate_bins.filter((b) => b.complete).at(-1);

  return (
    <>
      <div className="sticky-head">
      <Header e={e} partner={partner} onArchive={onChange} />
      {e.end_note && <div className={`banner ${e.status === "aborted" ? "alert" : "warn"} small`} role="note">
        <b>{e.status === "aborted" ? "Aborted" : "Ended"}{e.ended_at ? ` ${new Date(e.ended_at).toLocaleString()}` : ""}:</b>&nbsp;{e.end_note}</div>}
      {preparing && (
        <div className="card" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", borderRadius: 16 }}>
          <div className="row" style={{ gap: 24 }}>
            <span className="row" style={{ gap: 8, color: "var(--data-orange-text)", fontWeight: 600 }}><span className="dot" style={{ width: 10, height: 10, background: "#f9b112" }} />Not recording</span>
            <span>t = <span className="mono" style={{ fontSize: 20, fontWeight: 600 }}>—</span></span>
            <span className="small muted">Last controller reading <span className="mono">{fmtAge(r?.last_reading_age_s)}</span> ago
              {r && !r.online && <b style={{ color: "#b30020" }}> · controller not responding</b>}</span>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <LoopsButton reactor={e.reactor_id} channels={ch} />
            <ResetCountersButton reactor={e.reactor_id} recording={false} />
            <StartExperimentButton e={e} onDone={onChange} />
          </div>
        </div>
      )}
      {running && !preparing && (
        <div className="card" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", borderRadius: 16 }}>
          <div className="row" style={{ gap: 24 }}>
            <span className="row" style={{ gap: 8, color: "#1f7a63", fontWeight: 600 }}><span className="dot ok" style={{ width: 10, height: 10 }} />Recording</span>
            <span>t = <span className="mono" style={{ fontSize: 20, fontWeight: 600 }}>{fmtDuration(r?.experiment?.t_h)}</span></span>
            <span className="small muted">Last controller reading <span className="mono">{fmtAge(r?.last_reading_age_s)}</span> ago
              {r && !r.online && <b style={{ color: "#b30020" }}> · controller not responding</b>}</span>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <LoopsButton reactor={e.reactor_id} channels={ch} />
            <ResetCountersButton reactor={e.reactor_id} recording onDone={() => { readings.refresh(); events.refresh(); }} />
            <EndExperimentButton e={e} onDone={onChange} />
          </div>
        </div>
      )}
      {!running && (
        <div className="card" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", borderRadius: 16 }}>
          <span className="small muted">Not recording. The controller can still be monitored and commanded (setpoints, limits, loops, counters).</span>
          <Link className="btn tonal" to={`/reactors/${e.reactor_id}`}>Monitor {e.reactor_id}</Link>
        </div>
      )}
      </div>

      <div className="split">
        <div className="wide">
          <section className="card" aria-labelledby="trends">
            <div className="row between">
              <h2 id="trends" style={{ fontSize: 18 }}>Trends</h2>
              <div className="row">
                <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 14, height: 3, background: "#1d3786" }} />Left
                  <select value={left} onChange={(x) => setLeft(x.target.value)} style={{ height: 40, width: "auto" }}>
                    {vars.map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}
                  </select>
                </label>
                <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 14, height: 3, background: "#f9b112" }} />Right
                  <select value={right} onChange={(x) => setRight(x.target.value)} style={{ height: 40, width: "auto" }}>
                    {vars.filter((v) => v.key !== left).map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}
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

          {ch.includes("ph") && <section className="card" aria-labelledby="base">
            <div className="row between">
              <div className="stack" style={{ gap: 2 }}>
                <h2 id="base" style={{ fontSize: 18 }}>Buffer dosing</h2>
                <span className="small muted">Base buffer (and acid buffer, Addition 2, when dosed): cumulative volume as reported by the controller; rate computed per bin</span>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <span className="small muted">Rate averaged over</span>
                <Segmented label="Averaging window" value={binH} onChange={setBinH} options={[{ value: "0.25", label: "15 min" }, { value: "1", label: "1 h" }]} />
                {partner && <button className={`chip ${showPartnerBase ? "on" : ""}`} onClick={() => setShowPartnerBase(!showPartnerBase)}>Overlay {partner.reactor_id}</button>}
              </div>
            </div>
            <div className="dv-grid">
              <DataValue value={fmt(bd?.total_ml as number, 1)} unit="mL" label="Base buffer, total since Start" color="#1f7a63" />
              <DataValue value={fmt(lastBin?.rate_ml_min, 3)} unit="mL/min" label={`Last complete ${binH === "1" ? "hour" : "15 min"}`} color="#1f7a63" />
              <DataValue value={fmt(bd?.peak_rate_ml_min as number, 3)} unit="mL/min" label={`Peak rate · t = ${fmt(bd?.t_peak_h as number, 1)} h`} />
              <DataValue value={fmt(s?.base_pump_pct, 0)} unit="%" label="Pump activation · now" />
              {endH != null && <DataValue value={fmt(endH, 1)} unit="h" label="Estimated end of fermentation" />}
            </div>
            <EChart option={base} className="chart short" ariaLabel="Base rate bins and cumulative base over time" />
          </section>}

          <ManualVariables e={e} />
        </div>

        <aside className="side">
          {running && s && (
            <section className="card tight" aria-labelledby="ctrl">
              <div className="row between" style={{ padding: "0 8px" }}>
                <h2 id="ctrl" style={{ fontSize: 18 }}>Controller · <span className="mono">{e.reactor_id}</span></h2>
                <span className="xs muted">measured · target and limits</span>
              </div>
              <ControllerPanel reactor={e.reactor_id} s={s} channels={ch} phMode={r?.ph_control?.mode}
                onChanged={() => { live.refresh(); log.refresh(); }} />
            </section>
          )}

          <section className="card tight" aria-labelledby="ev">
            <div className="row between"><h3 id="ev">Events</h3>
              <MoreToggle n={(events.data ?? []).length} shown={EVENTS_SHOWN} open={allEvents} onToggle={() => setAllEvents(!allEvents)} /></div>
            {(events.data ?? []).slice(0, allEvents ? undefined : EVENTS_SHOWN).map((x) => (
              <div key={x.id} className="row" style={{ gap: 10, alignItems: "flex-start", flexWrap: "nowrap" }}>
                <span className={`dot ${x.severity === "alert" ? "bad" : x.severity === "good" ? "ok" : "idle"}`} style={{ marginTop: 6, flex: "0 0 auto" }} />
                <span className="small" style={{ flexGrow: 1 }}>{x.message}</span>
                <span className="xs muted mono">{x.t_h == null ? "" : `${x.t_h.toFixed(1)} h`}</span>
              </div>
            ))}
            {(events.data ?? []).length === 0 && <span className="small muted">No events yet.</span>}
          </section>

          <section className="card tight" aria-labelledby="log">
            <div className="row between"><h3 id="log">Change log</h3>
              <MoreToggle n={(log.data ?? []).length} shown={LOG_SHOWN} open={allLog} onToggle={() => setAllLog(!allLog)} /></div>
            {[...(log.data ?? [])].reverse().slice(0, allLog ? undefined : LOG_SHOWN).map((c, i) => (
              <div key={i} className="small row" style={{ gap: 10, flexWrap: "nowrap" }}>
                <span className="mono muted" title={new Date(c.at).toLocaleString()}>{new Date(c.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                <span>{c.parameter} {c.old ?? "—"} → {c.new} · {c.origin} · {c.status}</span>
              </div>
            ))}
            {(log.data ?? []).length === 0 && <span className="small muted">No setpoint changes from this software.</span>}
          </section>
        </aside>
      </div>

    </>
  );
}

const EVENTS_SHOWN = 8, LOG_SHOWN = 5;

/** "Show all (n)" / "Show fewer" for the event and change lists (newest first). */
function MoreToggle({ n, shown, open, onToggle }: { n: number; shown: number; open: boolean; onToggle: () => void }) {
  if (n <= shown) return null;
  return <button className="btn text" onClick={onToggle} aria-expanded={open}>{open ? "Show fewer" : `Show all (${n})`}</button>;
}

/** Trend axes and toggles: the last choice is remembered in this browser (all experiments). */
type TrendPrefs = { left?: string; right?: string; setpoints?: boolean; limits?: boolean };
const TREND_KEY = "fm2.trendPrefs";
function loadTrendPrefs(): TrendPrefs {
  try { return JSON.parse(localStorage.getItem(TREND_KEY) ?? "{}") as TrendPrefs; } catch { return {}; }
}
function saveTrendPrefs(p: TrendPrefs) {
  try { localStorage.setItem(TREND_KEY, JSON.stringify({ ...loadTrendPrefs(), ...p })); } catch { /* storage unavailable */ }
}

function ManualVariables({ e }: { e: Experiment }) {
  const vars = usePolling(api.variables, 600000);
  const meas = usePolling(() => api.measurements(e.id), 30000, [e.id]);
  const [variable, setVariable] = useState<number | "">("");
  const [value, setValue] = useState("");
  const [time, setTime] = useState("");
  const [tH, setTH] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const live = e.status === "fermenting";
  const started = e.started_at != null;
  const varId = variable === "" ? (vars.data?.find((v) => v.name === "OD600") ?? vars.data?.[0])?.id : variable;
  const unit = vars.data?.find((v) => v.id === varId)?.unit ?? "";

  async function add() {
    setErr(null);
    const v = Number(value.replace(",", "."));
    if (!varId || !Number.isFinite(v) || value === "") { setErr("Enter a numeric value"); return; }
    let sampled_at: string | undefined;
    let t_h: number | undefined;
    if (tH.trim() !== "") {
      t_h = Number(tH.replace(",", "."));
      if (!Number.isFinite(t_h) || t_h < 0) { setErr("t (h) must be a number ≥ 0"); return; }
    } else if (live && time) {
      const [hh, mm] = time.split(":").map(Number);
      const d = new Date(); d.setHours(hh, mm, 0, 0);
      if (d.getTime() > Date.now()) d.setDate(d.getDate() - 1); // sample taken before midnight
      sampled_at = d.toISOString();
    } else if (!live && time) {
      sampled_at = new Date(time).toISOString();       // date and time of sampling
    } else if (!live) {
      setErr("Give when the sample was taken: t (h) since start, or date and time"); return;
    }
    try {
      await api.addMeasurement(e.id, { variable_id: varId, value: v, sampled_at, t_h, note });
      setValue(""); setNote(""); setTime(""); setTH(""); meas.refresh();
    } catch (x) { setErr((x as Error).message); }
  }

  return (
    <section className="card" aria-labelledby="man">
      <h2 id="man" style={{ fontSize: 18 }}>Manual variables</h2>
      {!started && <span className="small muted">Manual variables can be added once the experiment has started (time is counted from Start).</span>}
      {started && !live && <span className="small muted">
        The run has ended: values measured later (e.g. on frozen aliquots) are entered at the time the sample was taken.</span>}
      {started && (
        <div className="row" style={{ alignItems: "flex-end" }}>
          {live
            ? <label className="field" style={{ flex: "1 1 140px" }}>Sampling time (empty = now)
                <input type="time" value={time} disabled={tH.trim() !== ""} onChange={(x) => setTime(x.target.value)} />
              </label>
            : <label className="field" style={{ flex: "1 1 200px" }}>Sampling date and time
                <input type="datetime-local" value={time} disabled={tH.trim() !== ""} onChange={(x) => setTime(x.target.value)} />
              </label>}
          <label className="field" style={{ flex: "0 1 110px" }}>or t (h)
            <input inputMode="decimal" value={tH} onChange={(x) => setTH(x.target.value)} placeholder="e.g. 24" style={{ fontFamily: "var(--font-mono)" }} />
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
