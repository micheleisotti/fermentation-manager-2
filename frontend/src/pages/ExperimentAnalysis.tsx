import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import * as echarts from "echarts/core";
import { CustomChart, ScatterChart } from "echarts/charts";
import { api, type AnalysisResult, type DoseEpisodes, type LoopPerformance, type Experiment } from "../api";
import { fmt, usePolling } from "../hooks";
import { addEndLine } from "../components/charts";
import { C, DataValue, EChart, ExpName, Icon, Segmented, StatusChip } from "../components/ui";

echarts.use([ScatterChart, CustomChart]);

const STEPS = [{ value: "0.25", label: "15 min" }, { value: "1", label: "1 h" }, { value: "2", label: "2 h" }, { value: "4", label: "4 h" }];
const PANEL_COLORS: Record<string, string> = {
  OD600: "#1d3786", AFU: "#7a4fa0", ph: "#1d3786", base_rate: "#1f7a63", base_cumulative_ml: "#1f7a63",
  temperature: "#b3261e", stirrer: "#54565a", do2: "#00639b",
};
const OTHER_COLORS = ["#8a5300", "#006a6a", "#5c4d00", "#7d2a52"];
const DEFAULT_ON = ["OD600", "AFU", "ph", "base_rate"];

interface PanelDef { key: string; title: string; color: string; manual: boolean }

/** Mode switch shared by the fermentation and analysis views of one experiment. */
export function ModeTabs({ e, current }: { e: Experiment; current: "fermentation" | "analysis" }) {
  const nav = useNavigate();
  const tabs = [
    { k: "fermentation", label: "Fermentation", to: `/experiments/${e.id}` },
    { k: "analysis", label: "Analysis", to: `/analysis/experiments/${e.id}` },
  ] as const;
  return (
    <div className="segmented mode-tabs" role="tablist" aria-label="Mode">
      {tabs.map((t) => (
        <button key={t.k} role="tab" aria-selected={t.k === current} className={t.k === current ? "on" : ""}
          onClick={() => nav(t.to)}>
          {t.k === current && <Icon name="check" size={16} width={2.2} />}{t.label}
        </button>
      ))}
    </div>
  );
}

export function ExperimentAnalysisPage() {
  const id = Number(useParams().id);
  const exp = usePolling(() => api.experiment(id), 60000, [id]);
  const [model, setModel] = useState("gompertz");
  const [step, setStep] = useState("1");
  const running = exp.data?.status === "fermenting";
  const an = usePolling(() => api.analysisFull(id, model, Number(step)), running ? 60000 : 3600000, [id, model, step]);
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [showFit, setShowFit] = useState(true);

  const panelsAll: PanelDef[] = useMemo(() => {
    if (!an.data) return [];
    let other = 0;
    const manual = Object.entries(an.data.manual).map(([k, m]) => ({
      key: k, title: m.unit ? `${k} (${m.unit})` : k, manual: true,
      color: PANEL_COLORS[k] ?? OTHER_COLORS[other++ % OTHER_COLORS.length],
    }));
    const ctrl = an.data.series_defs.map((d) => ({
      key: d.key, title: d.unit ? `${d.label} (${d.unit})` : d.label, manual: false, color: PANEL_COLORS[d.key] ?? C.slate,
    }));
    return [...manual, ...ctrl];
  }, [an.data]);
  const on = chosen ?? panelsAll.filter((p) => DEFAULT_ON.includes(p.key)).map((p) => p.key);
  const panels = panelsAll.filter((p) => on.includes(p.key));

  if (exp.error) return <div className="card">Experiment not found: {exp.error}</div>;
  if (!exp.data) return <div className="muted">Loading…</div>;
  const e = exp.data;
  const a = an.data;
  const tMax = a ? Math.max(1, a.summary.recorded_h ?? 0, ...Object.values(a.manual).flatMap((m) => m.t), ...a.series.t) : 1;

  return (
    <>
      <header className="row between">
        <div className="row" style={{ gap: 12 }}>
          <Link to={`/experiments/${e.id}`} className="btn icon text" aria-label="Back to the experiment"><Icon name="back" /></Link>
          <div className="stack" style={{ gap: 2 }}>
            <h1 style={{ fontSize: 22, fontWeight: 600 }}>{e.name}{e.strain && <span style={{ fontWeight: 400 }}> — {e.strain}</span>} <span className="mono code-chip">{e.code}</span></h1>
            <span className="small muted">Bioreactor {e.reactor_id.slice(1)}
              {e.started_at && ` · ${new Date(e.started_at).toLocaleDateString()}`}
              {a?.summary.recorded_h != null && ` · recorded ${fmt(a.summary.recorded_h, 1)} h`}
              {a?.experiment.partner && <> · parallel run <b className="mono">{a.experiment.partner.parallel_run}</b> with {a.experiment.partner.code} ({a.experiment.partner.reactor_id})</>}
            </span>
          </div>
          <StatusChip status={e.status} />
        </div>
        <ModeTabs e={e} current="analysis" />
      </header>

      {running && <div className="card tight small" style={{ background: "var(--warn-container)" }}>
        The experiment is still recording: parameters are provisional and are recalculated every minute.</div>}
      {an.error && <div className="card tight" style={{ color: "#b30020" }}>Analysis failed: {an.error}</div>}
      {!a ? <div className="muted">Calculating…</div> : (
        <>
          <section className="card summary-grid" aria-label="Summary">
            <DataValue value={fmt(a.summary.recorded_h, 1)} unit="h" label="Recorded" color={C.ink} />
            <DataValue value={fmt(a.summary.final_od, 2)} unit="AU" label="Final OD600" color={C.blue} />
            <DataValue value={fmt(a.summary.max_afu, 0)} unit="AFU" label="Max AFU" color={C.blue} />
            <DataValue value={fmt(a.summary.total_base_ml, 1)} unit="mL" label="Total base" color={C.greenDark} />
            <DataValue value={fmt(a.summary.peak_base_rate_ml_min, 3)} unit="mL/min" label="Peak base rate" color={C.greenDark} />
            {a.estimated_end && <DataValue value={fmt(a.summary.t_end_h, 1)} unit="h"
              label={`Estimated end · base rate < ${Math.round(a.estimated_end.fraction * 100)} % of peak for ${Math.round(a.estimated_end.hold_h * 60)} min`} color={C.ink} />}
            <DataValue value={a.summary.out_of_limit_events} label="Out-of-limit events" color={a.summary.out_of_limit_events ? "#b30020" : C.ink} />
          </section>

          <section className="card" aria-labelledby="tc">
            <div className="row between">
              <h2 id="tc" style={{ fontSize: 18 }}>Time course</h2>
              <div className="row" style={{ gap: 12 }}>
                <span className="small muted">Controller data every</span>
                <Segmented label="Point interval" options={STEPS} value={step} onChange={setStep} />
                <label className="row small" style={{ gap: 6 }}>
                  <input type="checkbox" checked={showFit} onChange={(x) => setShowFit(x.target.checked)} style={{ width: 18, height: 18 }} />Fitted model
                </label>
              </div>
            </div>
            <div className="row" role="group" aria-label="Variables shown" style={{ gap: 8 }}>
              {panelsAll.map((p) => (
                <button key={p.key} className={`chip ${on.includes(p.key) ? "on" : ""}`} aria-pressed={on.includes(p.key)}
                  onClick={() => setChosen(on.includes(p.key) ? on.filter((k) => k !== p.key) : [...on, p.key])}>
                  {p.title.replace(/ \(.*\)$/, "")}
                </button>
              ))}
            </div>
            {panels.length === 0 ? <span className="muted">Select at least one variable.</span> : (
              <div className="facets">
                {panels.map((p) => (
                  <div key={p.key} className="facet">
                    <div className="facet-strip">{p.title}</div>
                    <EChart className="facet-chart" ariaLabel={`${p.title} over time since start`}
                      option={facetOption(a, p, tMax, showFit)} />
                  </div>
                ))}
              </div>
            )}
            <span className="small muted">Time since start (h). Free y scales. Manual variables: points (mean ± SD where a time point has
              repeated readings), line = fitted {a.models[model]} model.{a.summary.t_end_h != null && " Dashed vertical line: estimated end of fermentation."} Controller data: mean over each interval;
              base rate from the cumulative volume (gaps shown as breaks).</span>
          </section>

          <div className="split">
            <div className="wide">
              <KineticsCard a={a} model={model} setModel={setModel} />
              <ControlCard a={a} />
            </div>
            <div className="side">
              <ExportCard e={e} on={panels.map((p) => p.key)} model={model} step={step} showFit={showFit} />
            </div>
          </div>
        </>
      )}
    </>
  );
}

function facetOption(a: AnalysisResult, p: PanelDef, tMax: number, showFit: boolean): echarts.EChartsCoreOption {
  const axis = {
    axisLine: { show: false }, axisTick: { show: true, lineStyle: { color: "#4d4d4d" } },
    axisLabel: { fontFamily: "IBM Plex Sans", fontSize: 11, color: "#4d4d4d" },
    splitLine: { show: true, lineStyle: { color: "#ffffff", width: 1.2 } },
  };
  const series: unknown[] = [];
  if (p.manual) {
    const m = a.manual[p.key];
    const pts = m.t.map((t, i) => [t, m.mean[i], m.sd[i], m.n[i]]);
    if (m.sd.some((s) => s != null)) {
      series.push({
        type: "custom", name: "SD", silent: true, data: pts.filter((d) => d[2] != null),
        renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[] }) => {
          const [t, y, sd] = [api.value(0), api.value(1), api.value(2)];
          const lo = api.coord([t, y - sd]), hi = api.coord([t, y + sd]);
          const st = { stroke: p.color, lineWidth: 1 };
          return { type: "group", children: [
            { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: st },
            { type: "line", shape: { x1: lo[0] - 3, y1: lo[1], x2: lo[0] + 3, y2: lo[1] }, style: st },
            { type: "line", shape: { x1: hi[0] - 3, y1: hi[1], x2: hi[0] + 3, y2: hi[1] }, style: st },
          ] };
        },
      });
    }
    series.push({ type: "scatter", name: p.key, data: pts, symbolSize: 7, itemStyle: { color: p.color } });
    const f = a.fits[p.key];
    if (showFit && f?.curve) {
      series.push({ type: "line", name: "fit", data: f.curve.t.map((t, i) => [t, f.curve!.y[i]]), showSymbol: false,
        lineStyle: { color: p.color, width: 1.4 }, silent: true });
    }
  } else {
    series.push({ type: "line", name: p.key, data: a.series.t.map((t, i) => [t, a.series[p.key]?.[i] ?? null]),
      showSymbol: a.series.t.length < 40, symbolSize: 5, connectNulls: false, step: p.key === "base_rate" ? "middle" : undefined,
      itemStyle: { color: p.color }, lineStyle: { color: p.color, width: 1.4 } });
  }
  addEndLine(series as object[], a.estimated_end?.t_end_h, true);
  return {
    animation: false,
    grid: { left: 48, right: 14, top: 8, bottom: 26, show: true, backgroundColor: "#ebebeb", borderWidth: 0 },
    xAxis: { type: "value", min: 0, max: Math.ceil(tMax), ...axis, axisLabel: { ...axis.axisLabel, showMaxLabel: false } },
    yAxis: { type: "value", scale: true, ...axis },
    tooltip: {
      trigger: "item", valueFormatter: undefined,
      formatter: (q: { data: (number | null)[]; seriesName: string }) => {
        const [t, y, sd, n] = q.data;
        if (y == null) return "";
        return `t = ${fmt(t, 2)} h<br/><b>${fmt(y, 3)}</b>${sd != null ? ` ± ${fmt(sd, 3)} (n = ${n})` : ""}`;
      },
    },
    series,
  };
}

function pm(v: number | undefined | null, se: number | undefined | null, d: number) {
  if (v == null) return "—";
  return se != null && Number.isFinite(se) ? `${fmt(v, d)} ± ${fmt(se, d)}` : fmt(v, d);
}

function KineticsCard({ a, model, setModel }: { a: AnalysisResult; model: string; setModel: (m: string) => void }) {
  const vars = Object.keys(a.fits).filter((k) => !a.fits[k].skipped);
  const skipped = Object.keys(a.fits).filter((k) => a.fits[k].skipped);
  const products = vars.filter((k) => a.manual[k]?.category === "product");
  const rows: { p: string; u: string; v: (k: string) => string }[] = [
    { p: "Lag time λ", u: "(h)", v: (k) => pm(a.fits[k].params?.lag_h, a.fits[k].params?.lag_se, 2) },
    { p: "μmax", u: "(h⁻¹)", v: (k) => pm(a.fits[k].params?.mu_max_per_h, a.fits[k].params?.mu_max_se, 3) },
    { p: "Doubling time at μmax", u: "(h)", v: (k) => fmt(a.fits[k].params?.doubling_time_h, 2) },
    { p: "Time at μmax", u: "(h)", v: (k) => fmt(a.fits[k].params?.t_mu_max_h, 2) },
    { p: "Asymptote A", u: "(ln X/X₀)", v: (k) => pm(a.fits[k].params?.A_ln, a.fits[k].params?.A_se, 3) },
    { p: "Predicted maximum X₀·e^A", u: "", v: (k) => fmt(a.fits[k].params?.X_max, 3) + (a.manual[k]?.unit ? ` ${a.manual[k].unit}` : "") },
    { p: "Fit R²", u: "(ln scale)", v: (k) => fmt(a.fits[k].params?.r_squared, 4) },
    { p: "RMSE", u: "(ln scale)", v: (k) => fmt(a.fits[k].params?.rmse, 4) },
    { p: "Readings (time points)", u: "", v: (k) => `${a.fits[k].params?.n_points ?? "—"} (${a.fits[k].params?.n_times ?? "—"})` },
  ];
  const bd = a.base_dosing;
  const binLabel = a.bin_h === 0.25 ? "15-min bins" : `${a.bin_h} h bins`;
  const bufRows = [
    { p: "Total base", u: "(mL)", v: fmt(bd?.total_ml, 2), src: "controller, cumulative at end" },
    { p: "Peak base rate", u: "(mL min⁻¹)", v: fmt(bd?.peak_rate_ml_min, 4), src: binLabel },
    { p: "Time of peak base rate", u: "(h)", v: fmt(bd?.t_peak_h, 2), src: `${binLabel}, bin centre` },
    { p: "Time to 50 % of total base", u: "(h)", v: fmt(bd?.t_half_total_h, 2), src: "controller, cumulative" },
    { p: "Base rate in the last hour", u: "(mL min⁻¹)", v: fmt(bd?.last_hour_rate_ml_min, 4), src: "slowdown at end of run" },
    { p: "Total base per final OD600", u: "(mL AU⁻¹)", v: fmt(bd?.base_per_final_od, 2), src: "proxy of acid per biomass" },
    { p: "Doses", u: "", v: bd?.n_doses == null ? "—" : String(bd.n_doses), src: "steps of the cumulative counter" },
  ];
  const partner = a.experiment.partner;
  return (
    <section className="card" aria-labelledby="k">
      <div className="row between">
        <h2 id="k" style={{ fontSize: 18 }}>Kinetic and base-dosing parameters</h2>
        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <span className="muted">Model</span>
          <select value={model} onChange={(x) => setModel(x.target.value)} style={{ height: 40, width: "auto" }}>
            {Object.entries(a.models).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
      </div>
      {vars.length === 0 ? <span className="muted">No manual measurements: nothing to fit.</span> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Parameter</th>{vars.map((k) => <th key={k} className="num">on {k}{a.manual[k]?.category === "product" ? " (product)" : ""}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.p}><td>{r.p} <span className="muted">{r.u}</span></td>
                  {vars.map((k) => <td key={k} className="num mono">{a.fits[k].params ? r.v(k) : "—"}</td>)}</tr>
              ))}
            </tbody>
          </table>
          {vars.filter((k) => a.fits[k].params?.warnings.length).map((k) => (
            <div key={k} className="small" style={{ color: "#8a5a00", marginTop: 6 }}>
              <Icon name="warn" size={14} /> <b>{k}:</b> {a.fits[k].params!.warnings.join("; ")} — interpret with caution.</div>
          ))}
          {vars.filter((k) => a.fits[k].error).map((k) => (
            <div key={k} className="small" style={{ color: "#8a5a00", marginTop: 6 }}><b>{k}:</b> {a.fits[k].error}</div>
          ))}
          <div className="small muted" style={{ marginTop: 6 }}>Fitted on y = ln(X/X₀), X₀ = first measurement; ± = standard error of the fit.
            {products.length > 0 && <> For product variables ({products.join(", ")}) λ and μmax describe accumulation (specific accumulation rate), not growth.</>}
            {skipped.length > 0 && <> Not fitted (substrate/other role): {skipped.join(", ")}.</>}</div>
        </div>
      )}
      {bd ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Base dosing</th><th className="num">Value</th><th>Source</th></tr></thead>
            <tbody>{bufRows.map((r) => (
              <tr key={r.p}><td>{r.p} <span className="muted">{r.u}</span></td><td className="num mono">{r.v}</td>
                <td className="small muted">{r.src}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : <span className="small muted">No base-dosing data (pH channel off or no controller readings).</span>}
      {partner && (
        <Link className="btn tonal" style={{ alignSelf: "flex-start" }}
          to={`/analysis/new?with=${a.experiment.id},${partner.id}&cond=${encodeURIComponent(a.experiment.code)},${encodeURIComponent(partner.code)}` +
            `&name=${encodeURIComponent(`${a.experiment.code} vs ${partner.code} (${partner.parallel_run})`)}`}>
          Compare with {partner.code} (parallel run) in a new analysis session
        </Link>
      )}
    </section>
  );
}

/** How well the loops held their setpoints (analysis.control): descriptive, no suggestion yet. */
function ControlCard({ a }: { a: AnalysisResult }) {
  const c = a.control;
  if (!c || (c.loops.length === 0 && Object.keys(c.doses).length === 0)) return null;
  const d = (loop: string) => (loop === "ph" ? 3 : loop === "temperature" ? 2 : 1);
  const sgn = (v: number | null, n: number) => {
    if (v == null) return "—";
    const r = Number(v.toFixed(n));      // no "−0.00"
    return `${r > 0 ? "+" : r < 0 ? "−" : ""}${fmt(Math.abs(r), n)}`;
  };
  const ok = (l: LoopPerformance) => l.regulated && l.mean != null;
  const why = (l: LoopPerformance) => (!l.regulated ? "never reached the setpoint" : l.loop_state_recorded ? "loop off while at the setpoint" : "too few readings");
  const loopRows: { p: string; u?: string; v: (l: LoopPerformance) => ReactNode }[] = [
    { p: "Setpoint", v: (l) => <>{fmt(l.setpoint_mean, d(l.loop))}{l.setpoint_changes ? <span className="muted"> ({l.setpoint_changes} change{l.setpoint_changes > 1 ? "s" : ""})</span> : ""}</> },
    { p: "Effective mean ± SD", v: (l) => `${fmt(l.mean, d(l.loop))} ± ${fmt(l.sd, d(l.loop))}` },
    { p: "Mean deviation from setpoint", v: (l) => <span style={{ color: l.bias != null && Math.abs(l.bias) > l.tolerance ? "#8a5a00" : undefined }}>{sgn(l.bias, d(l.loop))}</span> },
    { p: "Mean absolute deviation", v: (l) => fmt(l.mae, d(l.loop)) },
    { p: "Time within tolerance", v: (l) => <>{fmt(l.within_tol_pct, 0)} % <span className="muted">(±{l.tolerance})</span></> },
    { p: "Largest excursion above / below", v: (l) => `+${fmt(l.max_above, d(l.loop))} / −${fmt(l.max_below, d(l.loop))}` },
    { p: "Setpoint first reached", u: "h", v: (l) => fmt(l.t_reached_h, 1) },
    { p: "Crossings of the tolerance band", u: "per h", v: (l) => fmt(l.crossings_per_h, 1) },
    { p: "Time counted", u: "h", v: (l) => fmt(l.regulated_h, 1) },
  ];
  const doses = Object.values(c.doses);
  const doseRows: { p: string; v: (x: DoseEpisodes) => ReactNode }[] = [
    { p: "Dosing episodes", v: (x) => x.n },
    { p: "pH at pump start (median, range)", v: (x) => <>{fmt(x.ph_start_median, 2)}{x.ph_start_range && <span className="muted"> ({fmt(x.ph_start_range[0], 2)}–{fmt(x.ph_start_range[1], 2)})</span>}</> },
    { p: "Distance from setpoint at start", v: (x) => `${fmt(x.start_below_setpoint_median, 2)} ${x.counter === "base" ? "below" : "above"}` },
    { p: "pH at the last dose", v: (x) => fmt(x.ph_stop_median, 2) },
    { p: "pH peak in the 5 min after", v: (x) => fmt(x.ph_peak_median, 2) },
    { p: "Volume per episode", v: (x) => `${fmt(x.volume_median_ml, 2)} mL` },
    { p: "Interval between episodes", v: (x) => (x.interval_median_min == null ? "—" : `${fmt(x.interval_median_min, 0)} min`) },
  ];
  return (
    <section className="card" aria-labelledby="ctl">
      <div className="stack" style={{ gap: 2 }}>
        <h2 id="ctl" style={{ fontSize: 18 }}>Regulation</h2>
        <span className="small muted">How closely each loop held its setpoint, from the first time the value reached it to the end of the run.
          Time-weighted; the setpoint in force at each reading is used.</span>
      </div>
      {c.loops.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Parameter</th>{c.loops.map((l) => <th key={l.loop} className="num">{l.label}{l.unit && <span className="muted"> ({l.unit})</span>}</th>)}</tr></thead>
            <tbody>{loopRows.map((r, i) => (
              <tr key={r.p}><td>{r.p} {r.u && <span className="muted">{r.u}</span>}</td>
                {c.loops.map((l) => <td key={l.loop} className="num mono">{i === 0 || ok(l) ? r.v(l) : i === 1 ? <span className="muted small">{why(l)}</span> : "—"}</td>)}</tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {doses.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Dosing on the pH loop</th>{doses.map((x) => <th key={x.counter} className="num">{x.counter === "base" ? "Base buffer" : "Acid (Addition 2)"}</th>)}</tr></thead>
            <tbody>{doseRows.map((r, i) => (
              <tr key={r.p}><td>{r.p}</td>{doses.map((x) => <td key={x.counter} className="num mono">{i === 0 || x.n ? r.v(x) : "—"}</td>)}</tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {c.loops.some((l) => l.hints?.length) && (
        <div className="stack" style={{ gap: 6 }}>
          <h3 style={{ fontSize: 15 }}>Hints on the loop parameters</h3>
          {c.loops.filter((l) => l.hints?.length).map((l) => (
            <div key={l.loop} className="stack" style={{ gap: 4 }}>
              <span className="small"><b>{l.label}</b>{l.control_mode && <span className="muted"> · {l.control_mode === "pid" ? "PID" : l.control_mode}</span>}</span>
              {l.hints!.map((h, k) => (
                <div key={k} className={`banner small ${h.kind === "ok" ? "good" : h.kind === "hint" ? "warn" : ""}`} style={{ padding: "6px 12px" }}>
                  <Icon name={h.kind === "ok" ? "check" : h.kind === "hint" ? "warn" : "info"} size={16} />
                  <span>{h.text}</span>
                </div>
              ))}
            </div>
          ))}
          <span className="xs muted">Indications from general control rules, to be evaluated: change one parameter at a time, by a small step, and compare
            the next runs. Parameters are those of the plan; limits, calibration and actuators stay on the controller.</span>
        </div>
      )}
      <span className="small muted">Medians over the dosing episodes (doses less than 1 min apart form one episode). "pH at pump start" is the last reading
        before the counter grew: its resolution is the sampling interval.
        {c.loops.some((l) => l.regulated && !l.loop_state_recorded) && " Loop on/off was not recorded in this run: the loops are assumed on from the moment the setpoint was reached."}</span>
    </section>
  );
}

const RESAMPLE = [{ v: "", l: "raw (every reading)" }, { v: "1", l: "1 min" }, { v: "5", l: "5 min" }, { v: "15", l: "15 min" }, { v: "60", l: "1 h" }];
const BLOCKS = [
  { k: "controller", l: "Controller data (pH, T, stirrer, base, actuator outputs)" },
  { k: "manual", l: "Manual variables" },
  { k: "parameters", l: "Kinetic and base-dosing parameters" },
  { k: "metadata", l: "Metadata, profile, change log and events" },
];

function ExportCard({ e, on, model, step, showFit }: { e: Experiment; on: string[]; model: string; step: string; showFit: boolean }) {
  const [blocks, setBlocks] = useState<string[]>(BLOCKS.map((b) => b.k));
  const [resample, setResample] = useState("");
  const [format, setFormat] = useState<"xlsx" | "zip">("xlsx");
  const [fig, setFig] = useState<"png" | "svg" | "pdf">("png");
  const [width, setWidth] = useState("174");
  const [dpi, setDpi] = useState("300");
  const [theme, setTheme] = useState("grey");
  const [mono, setMono] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ ok: boolean; text: string } | null>(null);
  const w = Number(width);
  const widthOk = Number.isFinite(w) && w >= 40 && w <= 500;
  const dataUrl = `/api/experiments/${e.id}/export.${format}?blocks=${blocks.join(",")}&model=${model}${resample ? `&resample_min=${resample}` : ""}`;
  const figUrl = `/api/experiments/${e.id}/figure.${fig}?vars=${encodeURIComponent(on.join(","))}&model=${model}&step_h=${step}` +
    `&width_mm=${w}&dpi=${dpi}&theme=${theme}&fit=${showFit}&mono=${mono}`;
  return (
    <section className="card" aria-labelledby="ex">
      <h2 id="ex" style={{ fontSize: 18 }}>Export</h2>
      <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0, gap: 4 }}>
        <legend className="small muted" style={{ paddingBottom: 6 }}>Data</legend>
        {BLOCKS.map((b) => (
          <div key={b.k}>
            <label className="row" style={{ gap: 12, minHeight: 40, flexWrap: "nowrap" }}>
              <input type="checkbox" checked={blocks.includes(b.k)} style={{ width: 20, height: 20, flex: "none" }}
                onChange={() => setBlocks(blocks.includes(b.k) ? blocks.filter((x) => x !== b.k) : [...blocks, b.k])} />{b.l}
            </label>
            {b.k === "controller" && (
              <label className="row small muted" style={{ gap: 8, paddingLeft: 32 }}>Resample
                <select value={resample} onChange={(x) => setResample(x.target.value)} style={{ height: 36, width: "auto" }}
                  disabled={!blocks.includes("controller")}>
                  {RESAMPLE.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
                </select>
              </label>
            )}
          </div>
        ))}
      </fieldset>
      <Segmented label="Data format" value={format} onChange={setFormat}
        options={[{ value: "zip", label: "CSV (zip)" }, { value: "xlsx", label: "XLSX · one sheet per block" }]} />
      <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
        <a className="btn filled" href={blocks.length ? dataUrl : undefined} download
          aria-disabled={!blocks.length} style={{ justifyContent: "center", flex: "1 1 0" }}>Download</a>
        <button className="btn tonal" style={{ justifyContent: "center", flex: "1 1 0" }} disabled={!blocks.length || saving}
          title="Written by the lab PC into the archive folder (Settings › Data and backup), e.g. a USB stick"
          onClick={async () => {
            setSaving(true); setSaved(null);
            try { const r = await api.archiveCopy(e.id, { format, blocks, resample_min: resample ? Number(resample) : null, model }); setSaved({ ok: true, text: r.path }); }
            catch (x) { setSaved({ ok: false, text: (x as Error).message }); }
            finally { setSaving(false); }
          }}>{saving ? "Saving…" : "Save to archive"}</button>
      </div>
      {saved && <div className={`banner ${saved.ok ? "good" : "warn"} small`} role="status">
        {saved.ok ? <>Saved: <span className="mono">{saved.text}</span></> : saved.text}</div>}
      <div style={{ height: 1, background: "var(--surface-container)" }} />
      <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0, gap: 10 }}>
        <legend className="small muted" style={{ paddingBottom: 6 }}>Figure (the panels shown above)</legend>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: "1 1 90px" }}>Format
            <select value={fig} onChange={(x) => setFig(x.target.value as "png")}>
              <option value="png">PNG</option><option value="svg">SVG</option><option value="pdf">PDF</option></select>
          </label>
          <label className="field" style={{ flex: "1 1 90px" }}>Width (mm)
            <input className="mono" value={width} onChange={(x) => setWidth(x.target.value)} inputMode="decimal"
              aria-invalid={!widthOk} />
          </label>
          <label className="field" style={{ flex: "1 1 90px" }}>Resolution
            <select value={dpi} onChange={(x) => setDpi(x.target.value)} disabled={fig !== "png"}>
              <option value="300">300 dpi</option><option value="600">600 dpi</option></select>
          </label>
        </div>
        <label className="field">Theme
          <select value={theme} onChange={(x) => setTheme(x.target.value)}>
            <option value="grey">ggplot grey (as shown)</option><option value="bw">ggplot bw (white panel)</option>
            <option value="classic">Classic (axes only)</option>
          </select>
        </label>
        <label className="field">Series
          <select value={mono ? "1" : "0"} onChange={(x) => setMono(x.target.value === "1")}>
            <option value="0">Colour</option><option value="1">Black &amp; white</option>
          </select>
        </label>
        <span className="small muted">Common widths: 85 mm (one column), 174 mm (two columns).</span>
      </fieldset>
      <a className="btn outlined" href={widthOk && on.length ? figUrl : undefined} download aria-disabled={!widthOk || !on.length}
        style={{ justifyContent: "center" }}>Export figure</a>
      <span className="small muted">Times in exports are hours since start; absolute timestamps (UTC) are a separate column.</span>
    </section>
  );
}

/** /analysis: completed experiments to open, and the place for analysis sessions. */
export function AnalysisHome() {
  const list = usePolling(() => api.experiments(), 60000);
  const sessions = usePolling(api.sessions, 60000);
  const done = (list.data ?? []).filter((x) => x.status !== "planned" && !x.archived)
    .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""));
  return (
    <>
      <h1 className="display">Analysis</h1>
      <section className="card" aria-labelledby="one">
        <h2 id="one" style={{ fontSize: 18 }}>Single experiment</h2>
        {done.length === 0 ? <span className="muted">No recorded experiments yet.</span> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Experiment</th><th>Strain</th><th>Reactor</th><th>Started</th><th>Status</th></tr></thead>
              <tbody>{done.map((x) => (
                <tr key={x.id} >
                  <td><ExpName name={x.name} code={x.code} to={`/analysis/experiments/${x.id}`} /></td>
                  <td>{x.strain || "—"}</td><td className="mono">{x.reactor_id}</td>
                  <td>{x.started_at ? new Date(x.started_at).toLocaleDateString() : "—"}</td>
                  <td><StatusChip status={x.status} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card" aria-labelledby="sess">
        <div className="row between">
          <h2 id="sess" style={{ fontSize: 18 }}>Analysis sessions</h2>
          <Link className="btn filled" to="/analysis/new"><Icon name="plus" size={18} />New session</Link>
        </div>
        <span className="small muted">Compare completed experiments grouped by condition: mean ± SD, Welch's t-test (2 conditions) or ANOVA + Tukey (3 or more).</span>
        {(sessions.data ?? []).length === 0 ? <span className="muted">No sessions yet.</span> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Code</th><th>Name</th><th>Conditions</th><th>Updated</th></tr></thead>
              <tbody>{sessions.data!.map((x) => (
                <tr key={x.id}>
                  <td className="mono"><Link to={`/analysis/sessions/${x.id}`}>{x.code}</Link></td>
                  <td>{x.name}</td>
                  <td>{x.conditions.map((c) => `${c} (n = ${x.n_by_condition[c] ?? 0})`).join(" · ") || "—"}</td>
                  <td>{new Date(x.updated_at).toLocaleString()}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
